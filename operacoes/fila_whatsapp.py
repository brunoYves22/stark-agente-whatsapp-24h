"""Persistent WhatsApp outbox. No UI or model work during enqueue/status/reconcile."""
import hashlib,json,os,sqlite3,threading,time,uuid
from pathlib import Path
from .diario import connection
from .autoridade import authorize

RESOURCE='WhatsApp — Atendimento FLOW'
ACTIVE=('sending','confirming')
TERMINAL=('confirmed','unknown','duplicate','expired_before_send','failed_before_send','cancelled_by_user')

def schema(c):
    c.execute('''CREATE TABLE IF NOT EXISTS whatsapp_queue(
      seq INTEGER PRIMARY KEY AUTOINCREMENT,operation_id TEXT UNIQUE NOT NULL,
      task_id TEXT NOT NULL,action_id TEXT NOT NULL,requester_actor TEXT NOT NULL,requester_job_id TEXT,
      authorization_reference TEXT NOT NULL,resource_id TEXT NOT NULL,contact_id TEXT NOT NULL,
      body TEXT NOT NULL,digest TEXT NOT NULL,logical_key TEXT NOT NULL UNIQUE,purpose TEXT NOT NULL,
      state TEXT NOT NULL,deadline REAL NOT NULL,next_retry REAL NOT NULL,attempts INTEGER NOT NULL DEFAULT 0,
      worker_id TEXT,worker_pid INTEGER,worker_birth TEXT,lease_until REAL,commit_started INTEGER NOT NULL DEFAULT 0,
      evidence TEXT NOT NULL DEFAULT '{}',reason TEXT NOT NULL DEFAULT '',revision INTEGER NOT NULL DEFAULT 0,
      updated REAL NOT NULL,UNIQUE(task_id,action_id))''')
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS whatsapp_resource_owner ON whatsapp_queue(resource_id) WHERE state IN ('sending','confirming')")
    c.execute('CREATE TABLE IF NOT EXISTS whatsapp_aliases(task_id TEXT,action_id TEXT,original_operation_id TEXT,requester_actor TEXT,digest TEXT,contact_id TEXT,created REAL,PRIMARY KEY(task_id,action_id))')
    c.execute('CREATE TABLE IF NOT EXISTS whatsapp_events(id INTEGER PRIMARY KEY,operation_id TEXT,state TEXT,reason TEXT,created REAL)')

def snapshot(row):
    if row is None:return {'state':'not_found'}
    d=dict(row);d.pop('body',None);d.pop('logical_key',None);d['evidence']=json.loads(d['evidence']);return d

def progress(c,row,state,reason=''):
    now=time.time();c.execute('INSERT INTO whatsapp_events(operation_id,state,reason,created) VALUES(?,?,?,?)',(row['operation_id'],state,reason,now))
    taskrow=c.execute('SELECT payload FROM tasks WHERE task_id=?',(row['task_id'],)).fetchone()
    if not taskrow:return
    task=json.loads(taskrow[0]);task.setdefault('operation_progress',{}).setdefault(row['requester_actor'],{})[row['action_id']]={
      'operation_id':row['operation_id'],'job_id':row['requester_job_id'],'state':state,'reason':reason,'updated':now}
    task['revision']+=1
    c.execute('UPDATE tasks SET payload=?,revision=?,updated=? WHERE task_id=?',(json.dumps(task,ensure_ascii=False),task['revision'],now,row['task_id']))
    c.execute('INSERT INTO events(task_id,kind,created) VALUES(?,?,?)',(row['task_id'],'whatsapp_'+state,now))

def action_update(c,row,state,evidence):
    c.execute('UPDATE actions SET state=?,evidence=?,updated=? WHERE task_id=? AND action_id=?',
              (state,json.dumps(evidence,ensure_ascii=False),time.time(),row['task_id'],row['action_id']))

def enqueue_message(context,contact_id,text,action_id,purpose=None,deadline_seconds=1800):
    from .whatsapp import digits
    if not isinstance(text,str) or not text.strip() or len(text)>8000:raise ValueError('message_text_invalid')
    if not action_id or len(action_id)>120:raise ValueError('action_id_invalid')
    from operacoes.destinatarios import recipient_key
    contact_id=recipient_key(contact_id);db=Path(context['runtime'])/'estado.sqlite';task_id=context['task']['task_id'];actor=context['actor']
    digest=hashlib.sha256(text.encode()).hexdigest()
    # Explicit slot/purpose is durable across revised wording; no greeting/name regex.
    slots=context['task'].get('expected_messages',[])
    purpose=purpose or next((x['purpose'] for x in slots if x['actor']==actor),action_id)
    if not purpose or len(purpose)>120:raise ValueError('purpose_invalid')
    with connection(db) as c:
        schema(c);task=json.loads(c.execute('SELECT payload FROM tasks WHERE task_id=?',(task_id,)).fetchone()[0])
        prior=c.execute('SELECT * FROM whatsapp_queue WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if prior:
            if (prior['digest'],prior['contact_id'],prior['requester_actor'],prior['purpose'])!=(digest,contact_id,actor,purpose):raise ValueError('action_identity_conflict')
            return snapshot(prior)
        alias=c.execute('SELECT * FROM whatsapp_aliases WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if alias:
            if (alias['digest'],alias['requester_actor'],alias['contact_id'])!=(digest,actor,contact_id):raise ValueError('action_identity_conflict')
            return {'state':'duplicate','action_id':action_id,'original_operation_id':alias['original_operation_id']}
        authorize(context['policy'],task,actor,'whatsapp_send',{'contact_id':contact_id})
        if task.get('sending_paused'):raise ValueError('sending_paused')
        origin=task.get('original_request_id',task_id)
        key=hashlib.sha256(json.dumps([origin,actor,contact_id,purpose]).encode()).hexdigest()
        duplicate=c.execute('SELECT * FROM whatsapp_queue WHERE logical_key=?',(key,)).fetchone()
        if duplicate:
            c.execute('INSERT INTO whatsapp_aliases VALUES(?,?,?,?,?,?,?)',(task_id,action_id,duplicate['operation_id'],actor,digest,contact_id,time.time()))
            return {**snapshot(duplicate),'state':'duplicate','original_operation_id':duplicate['operation_id']}
        # Never overwrite an action from the pre-queue implementation.
        old=c.execute('SELECT * FROM actions WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if old:
            if old['digest']!=digest or old['actor']!=actor or json.loads(old['target']).get('contact_id')!=contact_id:raise ValueError('legacy_action_conflict')
            return {'state':old['state'],'action_id':action_id,'reconciliation_required':old['state']!='confirmed','evidence':json.loads(old['evidence'])}
        grant=task['scope']['operations']['whatsapp_send'];now=time.time();operation_id='wa-'+uuid.uuid4().hex
        c.execute('''INSERT INTO whatsapp_queue(operation_id,task_id,action_id,requester_actor,requester_job_id,
          authorization_reference,resource_id,contact_id,body,digest,logical_key,purpose,state,deadline,next_retry,updated)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',(operation_id,task_id,action_id,actor,context.get('job_id') or next((r[0] for r in c.execute("SELECT id FROM jobs WHERE task_id=? AND role=? AND state='running'",(task_id,actor.removeprefix('reserve_')))),None),grant['request_reference'],RESOURCE,contact_id,text,digest,key,purpose,'queued',now+max(1,min(deadline_seconds,3600)),now,now))
        c.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?)',(task_id,action_id,actor,'whatsapp_send',json.dumps({'contact_id':contact_id}),digest,'in_progress','{}',now,None))
        row=c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=?',(operation_id,)).fetchone();progress(c,row,'queued')
        threading.Thread(target=process_next,args=(context['runtime'],'worker-'+uuid.uuid4().hex),daemon=True).start()
        return snapshot(row)

def get_operation_status(db,task_id,action_id):
    from .diario import action_status
    with connection(db) as c:
        schema(c);row=c.execute('SELECT * FROM whatsapp_queue WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if row:
            if row['state'] in ('queued','waiting_portal'):
                threading.Thread(target=process_next,args=(Path(db).parent,'worker-'+uuid.uuid4().hex),daemon=True).start()
            return snapshot(row)
        alias=c.execute('SELECT * FROM whatsapp_aliases WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if alias:return {**dict(alias),'state':'duplicate'}
    return action_status(db,task_id,action_id)

def marker(pid):
    if os.name=='nt':
        import ctypes
        from ctypes import wintypes
        k=ctypes.WinDLL('kernel32',use_last_error=True);k.OpenProcess.restype=wintypes.HANDLE;k.OpenProcess.argtypes=[wintypes.DWORD,wintypes.BOOL,wintypes.DWORD]
        k.GetProcessTimes.argtypes=[wintypes.HANDLE,*([ctypes.POINTER(wintypes.FILETIME)]*4)];k.CloseHandle.argtypes=[wintypes.HANDLE]
        h=k.OpenProcess(0x1000,False,pid)
        if not h:return None
        try:
            v=[wintypes.FILETIME() for _ in range(4)]
            return str((v[0].dwHighDateTime<<32)|v[0].dwLowDateTime) if k.GetProcessTimes(h,*(ctypes.byref(x) for x in v)) else None
        finally:k.CloseHandle(h)
    try:return Path('/proc/'+str(pid)+'/stat').read_text().split()[21]
    except OSError:return None

def proven_dead(pid,birth):
    if not pid or not birth:return False
    current=marker(pid)
    if current is not None:return current!=birth
    # Access denial is not proof of death. Enumerate PID on Windows.
    if os.name=='nt':
        import subprocess
        r=subprocess.run(['tasklist','/FI','PID eq '+str(pid),'/FO','CSV','/NH'],capture_output=True,text=True,creationflags=0x08000000,timeout=10)
        return r.returncode==0 and '"'+str(pid)+'"' not in r.stdout
    return not Path('/proc/'+str(pid)).exists()

def recover_leases(db,now=None,is_dead=proven_dead):
    now=time.time() if now is None else now
    with connection(db) as c:
        schema(c)
        for row in c.execute("SELECT * FROM whatsapp_queue WHERE state IN ('sending','confirming') AND lease_until<?",(now,)).fetchall():
            if not is_dead(row['worker_pid'],row['worker_birth']):continue
            state='unknown' if row['commit_started'] else 'waiting_portal'
            c.execute('UPDATE whatsapp_queue SET state=?,worker_id=NULL,lease_until=NULL,next_retry=?,reason=?,updated=?,revision=revision+1 WHERE operation_id=?',
                      (state,now,'worker_terminated',now,row['operation_id']))
            action_update(c,row,'unknown' if state=='unknown' else 'in_progress',{'reason':'worker_terminated','clicked':bool(row['commit_started'])})
            progress(c,row,state,'worker_terminated')

def claim_next_message(db,resource_id,worker_id,now=None):
    now=time.time() if now is None else now
    with connection(db) as c:
        schema(c)
        if c.execute("SELECT 1 FROM whatsapp_queue WHERE resource_id=? AND state IN ('sending','confirming')",(resource_id,)).fetchone():return None
        rows=c.execute("SELECT * FROM whatsapp_queue WHERE resource_id=? AND state IN ('queued','waiting_portal') ORDER BY seq",(resource_id,)).fetchall()
        for row in rows:
            task=json.loads(c.execute('SELECT payload FROM tasks WHERE task_id=?',(row['task_id'],)).fetchone()[0])
            if task.get('sending_paused') or task.get('phase') not in ('reserve_active','premium_active','review'):continue
            if c.execute("SELECT 1 FROM actions WHERE task_id=? AND state='unknown'",(row['task_id'],)).fetchone():continue
            if row['next_retry']>now:return None
            if row['deadline']<=now:
                c.execute("UPDATE whatsapp_queue SET state='expired_before_send',reason='deadline',updated=?,revision=revision+1 WHERE operation_id=?",(now,row['operation_id']))
                action_update(c,row,'failed_before_send',{'reason':'deadline','clicked':False});progress(c,row,'expired_before_send','deadline')
                return {**dict(row),'state':'expired_before_send'}
            c.execute("UPDATE whatsapp_queue SET state='sending',worker_id=?,worker_pid=?,worker_birth=?,lease_until=?,attempts=attempts+1,updated=?,revision=revision+1 WHERE operation_id=?",
                      (worker_id,os.getpid(),marker(os.getpid()),now+30,now,row['operation_id']))
            row=c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=?',(row['operation_id'],)).fetchone();progress(c,row,'sending');return dict(row)
    return None

def mark_commit(db,operation_id,worker_id):
    with connection(db) as c:
        row=c.execute("SELECT * FROM whatsapp_queue WHERE operation_id=? AND worker_id=? AND state='sending' AND lease_until>?",(operation_id,worker_id,time.time())).fetchone()
        if not row:raise ValueError('lease_lost_before_commit')
        if row['deadline']<=time.time():raise ValueError('deadline_before_commit')
        c.execute("UPDATE whatsapp_queue SET commit_started=1,state='confirming',updated=?,revision=revision+1 WHERE operation_id=?",(time.time(),operation_id));progress(c,row,'confirming')

def validate_execution(root,operation):
    root=Path(root);registry=json.loads((root/'registry.json').read_text(encoding='utf-8-sig'));policy=json.loads((root/'operacoes/policy.json').read_text(encoding='utf-8-sig'))
    with connection(root/'estado.sqlite') as c:task=json.loads(c.execute('SELECT payload FROM tasks WHERE task_id=?',(operation['task_id'],)).fetchone()[0])
    if not registry.get('enabled'):raise ValueError('team_disabled')
    if task.get('sending_paused'):raise ValueError('sending_paused')
    with connection(root/'estado.sqlite') as c:
        if c.execute("SELECT 1 FROM actions WHERE task_id=? AND action_id!=? AND state='unknown'",(operation['task_id'],operation['action_id'])).fetchone():raise ValueError('reconciliation_pending')
    if task['device_id']!=registry['device_id']:raise ValueError('wrong_device')
    if operation['requester_actor'].startswith('reserve_') and not registry.get('route_verified'):raise ValueError('private_free_route_not_verified')
    authorize(policy,task,operation['requester_actor'],'whatsapp_send',operation)
    if task['scope']['operations']['whatsapp_send']['request_reference']!=operation['authorization_reference']:raise ValueError('authorization_changed')
    return {'runtime':str(root),'task':task,'actor':operation['requester_actor'],'registry':registry,'policy':policy}

def process_next(root,worker_id,executor=None,authorize_fn=validate_execution,now=None):
    root=Path(root);db=root/'estado.sqlite';now=time.time() if now is None else now
    operation=claim_next_message(db,RESOURCE,worker_id,now)
    if not operation:return None
    if operation['state']=='expired_before_send':return snapshot(operation)
    stop=threading.Event()
    def renew():
        while not stop.wait(5):
            with connection(db) as c:c.execute("UPDATE whatsapp_queue SET lease_until=? WHERE operation_id=? AND worker_id=? AND state IN ('sending','confirming')",(time.time()+30,operation['operation_id'],worker_id))
    thread=threading.Thread(target=renew,daemon=True);thread.start()
    evidence={};reason='';state='confirmed'
    try:
        context=authorize_fn(root,operation) or {'runtime':str(root),'task':{'task_id':operation['task_id']},'actor':operation['requester_actor']}
        def commit():
            authorize_fn(root,operation);mark_commit(db,operation['operation_id'],worker_id)
        if executor is None:
            from .whatsapp import execute_queued_message
            executor=execute_queued_message
        evidence=executor(context,operation,commit)
        if not isinstance(evidence,dict) or not evidence.get('reference'):raise ValueError('confirmation_evidence_missing')
        with connection(db) as c:
            current=c.execute('SELECT commit_started FROM whatsapp_queue WHERE operation_id=?',(operation['operation_id'],)).fetchone()
            if not current[0]:raise ValueError('commit_not_recorded')
    except Exception as error:
        reason=str(error) if isinstance(error,ValueError) else type(error).__name__
        with connection(db) as c:clicked=bool(c.execute('SELECT commit_started FROM whatsapp_queue WHERE operation_id=?',(operation['operation_id'],)).fetchone()[0])
        state='unknown' if clicked else ('waiting_portal' if 'portal_busy' in reason else 'failed_before_send')
        evidence={'reason':reason[:160],'clicked':clicked}
    finally:stop.set();thread.join(timeout=6)
    with connection(db) as c:
        row=c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=? AND worker_id=?',(operation['operation_id'],worker_id)).fetchone()
        if not row:raise ValueError('lease_fence_lost')
        retry=now+min(5,2**max(0,row['attempts']-1))
        c.execute('UPDATE whatsapp_queue SET state=?,evidence=?,reason=?,next_retry=?,updated=?,lease_until=NULL,worker_id=NULL,revision=revision+1 WHERE operation_id=?',
                  (state,json.dumps(evidence),reason[:160],retry,time.time(),row['operation_id']))
        action_update(c,row,'in_progress' if state=='waiting_portal' else state,evidence);progress(c,row,state,reason[:160])
        return snapshot(c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=?',(row['operation_id'],)).fetchone())

def reconcile_operation(db,task_id,action_id,evidence,expected_revision):
    with connection(db) as c:
        schema(c);row=c.execute('SELECT * FROM whatsapp_queue WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if not row:raise ValueError('operation_missing')
        if evidence.get('text_sha256')!=row['digest'] or not evidence.get('reference') or not evidence.get('contact_verified'):raise ValueError('reconciliation_evidence_invalid')
        if row['revision']!=expected_revision:raise ValueError('revision_conflict')
        if row['state']=='confirmed':return snapshot(row)
        c.execute("UPDATE whatsapp_queue SET state='confirmed',evidence=?,reason='operator_reconciliation',revision=revision+1,updated=? WHERE operation_id=?",(json.dumps(evidence),time.time(),row['operation_id']))
        action_update(c,row,'confirmed',evidence);progress(c,row,'confirmed','operator_reconciliation')
        return snapshot(c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=?',(row['operation_id'],)).fetchone())

def worker_loop(root,stop):
    worker_id='worker-'+uuid.uuid4().hex
    from .saude import start_heartbeat
    heartbeat=start_heartbeat(root,'whatsapp','worker')
    try:
        while not stop.is_set():
            try:
                recover_leases(Path(root)/'estado.sqlite');process_next(root,worker_id)
            except Exception:pass # Durable state/lease survives; panel exposes stale worker.
            stop.wait(.5)
    finally:heartbeat.set()


def presentation_manifest(team):
    roles=('orquestrador','analista','testador','validador') if team=='reserve' else ('orquestrador','analista_youtrack','testador_flow','validador')
    return [{'actor':('reserve_' if team=='reserve' else '')+role,'purpose':'presentation'} for role in roles]

def recover_task_paused(db,task_id,action_id,body,evidence,expected_revision):
    """Operator-only migration; never opens a portal or schedules inference/send."""
    with connection(db) as c:
        schema(c);task=json.loads(c.execute('SELECT payload FROM tasks WHERE task_id=?',(task_id,)).fetchone()[0])
        if task.get('whatsapp_recovery')==action_id:
            return {'prisma':snapshot(c.execute('SELECT * FROM whatsapp_queue WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()),'already_reconciled':True}
        if task['revision']!=expected_revision:raise ValueError('task_revision_conflict')
        if task.get('owner')!='reserve':raise ValueError('not_owner')
        if c.execute("SELECT 1 FROM jobs WHERE task_id=? AND state='running'",(task_id,)).fetchone():raise ValueError('job_still_running')
        action=c.execute('SELECT * FROM actions WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if not action or action['actor']!='reserve_analista':raise ValueError('prisma_action_missing')
        digest=hashlib.sha256(body.encode()).hexdigest()
        if digest!=action['digest'] or evidence.get('text_sha256')!=digest or not evidence.get('reference') or not evidence.get('contact_verified'):raise ValueError('reconciliation_evidence_invalid')
        contact=json.loads(action['target'])['contact_id'];now=time.time();operation_id='wa-reconciled-'+hashlib.sha256((task_id+action_id).encode()).hexdigest()[:24]
        key=hashlib.sha256(json.dumps([task.get('original_request_id',task_id),'reserve_analista',contact,'presentation']).encode()).hexdigest()
        c.execute('''INSERT INTO whatsapp_queue(operation_id,task_id,action_id,requester_actor,authorization_reference,resource_id,contact_id,body,digest,logical_key,purpose,state,deadline,next_retry,commit_started,evidence,reason,updated)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,'confirmed',?,?,1,?,'operator_visual_reconciliation',?)''',(operation_id,task_id,action_id,'reserve_analista',task['scope']['operations']['whatsapp_send']['request_reference'],RESOURCE,contact,body,digest,key,'presentation',now,now,json.dumps(evidence),now))
        row=c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=?',(operation_id,)).fetchone();action_update(c,row,'confirmed',evidence)
        task['sending_paused']=True;task['expected_messages']=presentation_manifest('reserve');task['whatsapp_recovery']=action_id
        task['recovery_job_history']=[dict(x) for x in c.execute('SELECT id,role,state,result,updated FROM jobs WHERE task_id=?',(task_id,))]
        task.setdefault('operation_progress',{}).setdefault('reserve_analista',{})[action_id]={'operation_id':operation_id,'state':'confirmed','updated':now,'reason':'operator_visual_reconciliation'}
        task['revision']+=1;task['next_step']='Recuperação conferida; envios pausados até novo pedido explícito de Bruno.'
        c.execute('UPDATE tasks SET payload=?,revision=?,updated=? WHERE task_id=?',(json.dumps(task,ensure_ascii=False),task['revision'],now,task_id))
        c.execute("UPDATE jobs SET state=CASE WHEN role='analista' THEN 'done' ELSE 'paused' END,result=CASE WHEN role='analista' THEN 'Envio confirmado por reconciliação visual' ELSE result END,updated=? WHERE task_id=?",(now,task_id))
        c.execute('INSERT INTO events(task_id,kind,created) VALUES(?,?,?)',(task_id,'whatsapp_recovered_paused',now))
        c.execute('INSERT INTO whatsapp_events(operation_id,state,reason,created) VALUES(?,?,?,?)',(operation_id,'confirmed','operator_visual_reconciliation',now))
        return {'prisma':snapshot(row),'sending_paused':True}


def resume_before_send(context,action_id,expected_revision):
    """Explicit recovery only; cannot resume unknown/committed actions or paused tasks."""
    db=Path(context['runtime'])/'estado.sqlite';task_id=context['task']['task_id']
    with connection(db) as c:
        schema(c);row=c.execute('SELECT * FROM whatsapp_queue WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        task=json.loads(c.execute('SELECT payload FROM tasks WHERE task_id=?',(task_id,)).fetchone()[0])
        if not row or row['revision']!=expected_revision:raise ValueError('revision_conflict')
        if row['commit_started'] or row['state'] not in ('expired_before_send','failed_before_send'):raise ValueError('resume_not_safe')
        if task.get('sending_paused'):raise ValueError('sending_paused')
        if context['actor'] not in (row['requester_actor'],'reserve_orquestrador','orquestrador'):raise ValueError('not_requester_or_coordinator')
        authorize(context['policy'],task,row['requester_actor'],'whatsapp_send',dict(row))
        c.execute("UPDATE whatsapp_queue SET state='queued',reason='',deadline=?,next_retry=?,updated=?,revision=revision+1 WHERE operation_id=?",(time.time()+120,time.time(),time.time(),row['operation_id']))
        action_update(c,row,'in_progress',{});progress(c,row,'queued','explicit_resume')
        return snapshot(c.execute('SELECT * FROM whatsapp_queue WHERE operation_id=?',(row['operation_id'],)).fetchone())
