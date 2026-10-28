"""Estado operacional aditivo. Sem execução de efeitos externos."""
import hashlib
import importlib.util
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path

VERSION = 'operacao-local-20261018'
ACTORS = ('orquestrador','analista_youtrack','testador_flow','validador',
          'reserve_orquestrador','reserve_analista','reserve_testador','reserve_validador')
LABELS = {'premium_active':'Em execução · principal','handoff_preparing':'Preparando passagem',
          'reserve_active':'Em execução · Antigravity','review':'Em validação','completed':'Concluída','blocked':'Bloqueada'}

@contextmanager
def database(root, write=False):
    path=Path(root)/'estado.sqlite'
    conn=sqlite3.connect(str(path) if write else 'file:'+path.as_posix()+'?mode=ro',uri=not write,timeout=15)
    conn.row_factory=sqlite3.Row
    try:
        if write:conn.execute('BEGIN IMMEDIATE')
        yield conn
        if write:conn.commit()
    except BaseException:
        if write:conn.rollback()
        raise
    finally:conn.close()

def migrate(root):
    with database(root,True) as c:
        c.execute('CREATE TABLE IF NOT EXISTS ops_observations(key TEXT PRIMARY KEY,payload TEXT NOT NULL,observed REAL NOT NULL,valid_until REAL NOT NULL)')
        c.execute('CREATE TABLE IF NOT EXISTS ops_progress(task_id TEXT,actor TEXT,operation TEXT,state TEXT,updated REAL,PRIMARY KEY(task_id,actor))')
        c.execute('CREATE TABLE IF NOT EXISTS ops_contracts(id TEXT PRIMARY KEY,task_id TEXT NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,updated REAL NOT NULL)')

def observe(root,key,payload,ttl=120):
    now=time.time()
    with database(root,True) as c:
        c.execute('INSERT INTO ops_observations VALUES(?,?,?,?) ON CONFLICT(key) DO UPDATE SET payload=excluded.payload,observed=excluded.observed,valid_until=excluded.valid_until',
                  (key,json.dumps(payload,ensure_ascii=False),now,now+ttl))

def observations(root):
    with database(root) as c:
        return {r['key']:{**json.loads(r['payload']),'observed_at':r['observed'],'valid_until':r['valid_until'],
                          'stale':time.time()>r['valid_until']} for r in c.execute('SELECT * FROM ops_observations')}

def progress(root,task_id,actor,operation,state):
    # Nomes de ferramentas, nunca resultado privado nem texto de cliente.
    with database(root,True) as c:
        c.execute('INSERT INTO ops_progress VALUES(?,?,?,?,?) ON CONFLICT(task_id,actor) DO UPDATE SET operation=excluded.operation,state=excluded.state,updated=excluded.updated',
                  (task_id,actor,operation,state,time.time()))

def table_exists(c,name):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())

def reason_code(task,jobs,actions,obs,queue=None):
    if task.get('phase')=='completed':return 'completed'
    queue=queue or {}
    if any(a['state']=='unknown' or (a['state']=='in_progress' and not (
        queue.get(a['action_id'],{}).get('state') in ('queued','waiting_portal')
        and not queue[a['action_id']]['commit_started'])) for a in actions):return 'reconciliation'
    if task.get('sending_paused'):return 'human_pause'
    if task.get('phase')=='handoff_preparing':return 'checkpoint'
    if any(j['state']=='running' for j in jobs):return 'running'
    if any(q['state'] in ('queued','waiting_portal','sending','confirming') for q in queue.values()):return 'resource'
    blockers=' '.join(str(x) for x in task.get('blockers',[]))+ ' '+ ' '.join(str(j.get('result','')) for j in jobs if j['state']=='blocked')
    low=blockers.casefold()
    if any(x in low for x in ('cota','quota','rate limit')):
        q=obs.get('quota_free',{})
        return 'quota_unknown' if q.get('stale',True) or q.get('state')=='unknown' else 'quota'
    if any(x in low for x in ('scope','autoriz','grant','permiss')):return 'authorization'
    if any(j['state']=='awaiting_operations' for j in jobs):return 'resource'
    if any(j['state'] in ('blocked','paused') for j in jobs) or task.get('phase')=='blocked':return 'interrupted'
    return 'waiting' if jobs else 'ready'

REASONS={'completed':'Concluída','reconciliation':'Aguardando reconciliação de ação externa',
 'human_pause':'Pausa explícita preservada','checkpoint':'Aguardando checkpoint e liberação do principal',
 'quota_unknown':'Cota não confirmada','quota':'Aguardando cota','authorization':'Autorização ou escopo pendente',
 'resource':'Aguardando recurso ou fila','running':'Em execução','interrupted':'Etapa interrompida; retomada disponível para avaliação',
 'waiting':'Aguardando execução','ready':'Pronta para próxima etapa'}

def overview(root):
    from operacoes.continuidade import status as recovery_status
    root=Path(root);registry=json.loads((root/'registry.json').read_text(encoding='utf-8-sig'))
    obs=observations(root);now=time.time();tasks=[];activities=[]
    with database(root) as c:
        rows=c.execute('SELECT payload,updated FROM tasks ORDER BY updated DESC LIMIT 200').fetchall() if table_exists(c,'tasks') else []
        for row in rows:
            task=json.loads(row['payload']);tid=task['task_id']
            jobs=[dict(j) for j in c.execute('SELECT * FROM jobs WHERE task_id=?',(tid,))]
            actions=[dict(a) for a in c.execute('SELECT action_id,state,updated FROM actions WHERE task_id=?',(tid,))] if table_exists(c,'actions') else []
            # O registro legado também pode conter ações não presentes na tabela actions.
            known={a['action_id'] for a in actions}
            known.update('op-'+hashlib.sha256(a['action_id'].encode()).hexdigest()[:32] for a in actions)
            actions+= [{'action_id':a.get('id'),'state':a.get('state'),'updated':row['updated']} for a in task.get('external_actions',[]) if a.get('state') in ('unknown','in_progress') and a.get('id') not in known]
            queue={r['action_id']:dict(r) for r in c.execute('SELECT action_id,state,commit_started FROM whatsapp_queue WHERE task_id=?',(tid,))} if table_exists(c,'whatsapp_queue') else {}
            code=reason_code(task,jobs,actions,obs,queue)
            ps=[dict(p) for p in c.execute('SELECT * FROM ops_progress WHERE task_id=?',(tid,))]
            last=max([row['updated']]+[p['updated'] for p in ps]+[j['updated'] for j in jobs])
            events=[{'kind':e['kind'],'at':e['created']} for e in c.execute('SELECT kind,created FROM events WHERE task_id=? ORDER BY id DESC LIMIT 12',(tid,))]
            handoff=[e for e in events if e['kind'] in ('handoff_requested','checkpoint','claim','reserve_acknowledged','continuation_queued','recovery_resumed')]
            tasks.append({'id':tid,'team':task.get('owner'),'device':task.get('device_id'),'phase':task['phase'],
                'state':REASONS[code],'reason_code':code,'revision':task.get('revision'),'updated':last,
                'age_seconds':round(max(0,now-last)),'stalled':code=='running' and now-last>300,
                'next_step':'Próxima etapa registrada' if task.get('next_step') else 'Próxima etapa ausente',
                'evidence_count':len(task.get('evidence',[])),'confirmed':sum(a['state']=='confirmed' for a in actions),
                'uncertain':sum(a['state']=='unknown' or (a['state']=='in_progress' and not (
                    queue.get(a['action_id'],{}).get('state') in ('queued','waiting_portal')
                    and not queue[a['action_id']]['commit_started'])) for a in actions),'handoff':handoff,
                'waiting_operations':sum(q['state'] in ('queued','waiting_portal') for q in queue.values()),
                'recovery':recovery_status(c,tid),
                'jobs':[{'id':j['id'],'role':j['role'],'state':j['state'],'updated':j['updated']} for j in jobs]})
            activities+=ps
        contracts=[{'id':r['id'],'task_id':r['task_id'],'kind':r['kind'],'state':json.loads(r['payload']).get('state'),'updated':r['updated']} for r in c.execute('SELECT * FROM ops_contracts ORDER BY updated DESC LIMIT 100')]
    # Somente metadados locais: nada presume a saúde/execução da BATCAVERNA.
    return {'version':VERSION,'device':registry['device_id'],'scope':'Somente este notebook',
            'remote':{'state':'not_deployed','label':'BATCAVERNA: implantação adiada pelo usuário'},
            'tasks':tasks,'activities':activities,'observations':obs,'contracts':contracts,
            'updated':now,'knowledge':str(root/'conhecimento'),'memory_mode':'Markdown local; Obsidian ainda não conectado'}


def require_task(root,actor,task_id):
    if actor not in ACTORS:raise ValueError('actor_invalid')
    with database(root) as c:
        row=c.execute('SELECT payload FROM tasks WHERE task_id=?',(task_id,)).fetchone()
    if not row:raise ValueError('task_missing')
    task=json.loads(row['payload']);reg=json.loads((Path(root)/'registry.json').read_text(encoding='utf-8-sig'))
    with database(root) as c:
        if table_exists(c,'actions'):
            authoritative=[dict(a) for a in c.execute('SELECT action_id,state,evidence FROM actions WHERE task_id=?',(task_id,))]
            known={a['action_id'] for a in authoritative}
            known.update('op-'+hashlib.sha256(a['action_id'].encode()).hexdigest()[:32] for a in authoritative)
            task['external_actions']=[a for a in task.get('external_actions',[]) if a.get('id') not in known]+[
                {'id':'op-'+hashlib.sha256(a['action_id'].encode()).hexdigest()[:32],'state':a['state'],
                 'external_ref':json.loads(a['evidence']).get('reference')} for a in authoritative]
    if task.get('device_id')!=reg['device_id']:raise ValueError('wrong_device')
    if task.get('owner')!=('reserve' if actor.startswith('reserve_') else 'premium'):raise ValueError('not_owner')
    if actor.startswith('reserve_') and actor!='reserve_orquestrador' and reg.get('reserve_engine')!='antigravity':
        with database(root) as c:
            if not c.execute('SELECT 1 FROM jobs WHERE task_id=? AND role=?',(task_id,actor.removeprefix('reserve_'))).fetchone():
                raise ValueError('role_not_delegated_to_task')
    return task

def require_running_role(root,actor,task_id):
    reg=json.loads((Path(root)/'registry.json').read_text(encoding='utf-8-sig'))
    if actor.startswith('reserve_') and actor!='reserve_orquestrador' and reg.get('reserve_engine')!='antigravity':
        with database(root) as c:
            if not c.execute("SELECT 1 FROM jobs WHERE task_id=? AND role=? AND state='running'",(task_id,actor.removeprefix('reserve_'))).fetchone():
                raise ValueError('reserve_job_not_running')

def code_module(root,name):
    if name not in ('catalogo','quota'):raise ValueError('module_not_allowed')
    spec=importlib.util.spec_from_file_location('flow_management_'+name,Path(root)/'code'/(name+'.py'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def contract_id(task_id,kind,key):
    return kind+'-'+hashlib.sha256((task_id+'\0'+kind+'\0'+key).encode()).hexdigest()[:24]
