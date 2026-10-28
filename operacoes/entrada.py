"""Entrada local persistente. Nunca repete uma entrega externa incerta."""
import hashlib
import json
import re
import time
from pathlib import Path
from operacoes.gestao import database, observations, table_exists
from operacoes.recuperacao import state_module


def migrate(root):
    from operacoes.continuidade import migrate as recovery_migrate
    recovery_migrate(root)
    with database(root, True) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS central_requests(
          id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, fingerprint TEXT NOT NULL,
          payload TEXT NOT NULL, state TEXT NOT NULL, team TEXT, reason TEXT NOT NULL,
          revision INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL, updated REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS central_commands(
          id TEXT PRIMARY KEY, task_id TEXT NOT NULL, kind TEXT NOT NULL, text TEXT NOT NULL,
          state TEXT NOT NULL, created REAL NOT NULL, updated REAL NOT NULL)''')
        if 'result' not in {r['name'] for r in c.execute('PRAGMA table_info(central_commands)')}:
            c.execute("ALTER TABLE central_commands ADD COLUMN result TEXT NOT NULL DEFAULT ''")


def registry(root):
    return json.loads((Path(root)/'registry.json').read_text(encoding='utf-8-sig'))


def clean(value, limit, required=True):
    if not isinstance(value, str) or len(value) > limit or (required and not value.strip()):
        raise ValueError('Texto vazio ou acima do limite permitido.')
    if re.search(r'\bsk-(?:or-v1-)?[A-Za-z0-9_-]{20,}|Bearer\s+[A-Za-z0-9_.-]{20,}', value):
        raise ValueError('Não inclua chaves ou tokens no pedido.')
    return value.strip()


def submit(root, data):
    key=clean(data.get('key'), 80)
    if not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',key):raise ValueError('Identificador inválido.')
    request=clean(data.get('request'),16000)
    acceptance=clean(data.get('acceptance',''),2000,False)
    mode=data.get('mode','auto')
    if mode not in ('premium','reserve','auto'):raise ValueError('Modo inválido.')
    payload={'request':request,'acceptance':acceptance,'mode':mode}
    from operacoes.receitas import validate
    workflow=validate(data.get('workflow',''),request)
    if workflow:payload['workflow']=workflow
    encoded=json.dumps(payload,ensure_ascii=False,sort_keys=True)
    fingerprint=hashlib.sha256(encoded.encode()).hexdigest()
    tid='central-'+hashlib.sha256(key.encode()).hexdigest()[:24]
    now=time.time()
    with database(root,True) as c:
        old=c.execute('SELECT * FROM central_requests WHERE request_key=?',(key,)).fetchone()
        if old:
            if old['fingerprint']!=fingerprint:raise ValueError('Este envio já foi registrado com outro conteúdo. Abra um novo pedido.')
            return {'task_id':old['id'],'state':old['state'],'reused':True}
        c.execute('INSERT INTO central_requests VALUES(?,?,?,?,?,?,?,?,?,?)',
                  (tid,key,fingerprint,encoded,'registered',None,'Pedido registrado; aguardando disponibilidade.',0,now,now))
    return {'task_id':tid,'state':'registered','reused':False}


def list_entries(root):
    from operacoes.gestao import overview
    tasks={t['id']:t for t in overview(root)['tasks']}
    with database(root) as c:
        entries=[dict(r) for r in c.execute('SELECT id,state,team,reason,revision,created,updated FROM central_requests ORDER BY created DESC LIMIT 200')]
    for entry in entries:
        task=tasks.get(entry['id'])
        if task:
            entry.update(execution_state=task['state'],owner=task['team'],
                         reason_code=task['reason_code'],last_progress=task['updated'],
                         confirmed=task['confirmed'],uncertain=task['uncertain'])
    return entries


def ready(root, mode):
    obs=observations(root);reg=registry(root)
    def alive(actor,kind):
        try:
            h=json.loads((Path(root)/'operacoes/heartbeats'/f'{actor}-{kind}.json').read_text())
            return 0<=time.time()-h['observed']<45
        except (OSError,ValueError,KeyError):return False
    q=obs.get('quota_codex',{})
    premium=(q.get('stale') is False and q.get('state')=='available'
             and isinstance(q.get('remaining_percent'),(float,int)) and q['remaining_percent']>10
             and alive('orquestrador','mcp'))
    if reg.get('reserve_engine')=='antigravity':
        reserve=bool(reg.get('enabled') and (alive('reserve_orquestrador','mcp') or alive('reserve_orquestrador','runner')))
    else:
        q=obs.get('quota_free',{})
        reserve=(q.get('stale') is False and q.get('state')=='available'
                 and reg.get('enabled') and reg.get('route_verified') and alive('reserve_orquestrador','runner'))
    if mode in ('premium','auto') and premium:return 'premium',''
    if mode in ('reserve','auto') and reserve:return 'reserve',''
    return None,'Aguardando cota recente e coordenador disponível; nenhuma inferência iniciada.'


def create_task(c, root, row, team):
    """Fila de entrada e job são gravados na mesma transação."""
    state=state_module(root);reg=registry(root);payload=json.loads(row['payload']);tid=row['id']
    from operacoes.mcp import initial_scope
    from operacoes.fila_whatsapp import presentation_manifest
    policy=json.loads((Path(root)/'operacoes/policy.json').read_text(encoding='utf-8-sig'))
    task={'task_id':tid,'device_id':reg['device_id'],'request':payload['request'],
          'scope':{'operations':{} if row['request_key'].startswith('reuniao-') else initial_scope(policy,payload['request'])},'confidentiality':'real',
          'plan':['Ler pedido da Central, preparar plano e registrar aceite antes de executar'],
          'completed':[],'next_step':'Coordenador deve registrar checkpoint e planejar execução.',
          'evidence':[],'external_actions':[],'blockers':[],
          'acceptance':[payload['acceptance'] or 'Atender pedido com evidências e validação independente'],
          'phase':'reserve_active' if team=='reserve' else 'premium_active','revision':-1,
          'owner':team,'origin':'user_direct' if team=='reserve' else 'central_user','central_source':True,
          'automatic_recovery':True,'workflow_hint':payload.get('workflow'),
          'ready_for_handoff':False,'version':1}
    # Publicação apenas da proposta revisada no painel local; fala não concede escopo.
    if row['request_key'].startswith('reuniao-'):
        from reunioes.core import publication_grant
        grant=publication_grant(root,row['request_key'],payload)
        if not grant:raise ValueError('Revisão da proposta de reunião não confirmada.')
        task['scope']={'operations':{'files':True},'youtrack_publication':grant,'youtrack_read':True}
    request=payload['request'].casefold()
    if not row['request_key'].startswith('reuniao-') and 'whatsapp' in request and 'apresent' in request and any(x in request for x in ('cada','todos','quatro')):
        task['expected_messages']=presentation_manifest(team)
    state.validate_capsule(task);state.put(c,task,'central_registered')
    c.execute('INSERT OR IGNORE INTO central_recovery_policy VALUES(?,1,?)',(tid,time.time()))
    if team=='reserve' and reg.get('reserve_engine')!='antigravity':
        instruction=('Pedido explícito do usuário pela Central. Use exclusivamente o task_id recebido. '
                     'Leia reserve_read_task, prepare reserve_plan_manual e registre progresso antes e depois de operações. '
                     'Delegue somente quando necessário; conclua com evidências e validação independente. '
                     'Não recrute modelos pagos. Preserve permissões e IDs de operações.')
        jid=hashlib.sha256((tid+'central-orquestrador').encode()).hexdigest()
        c.execute('INSERT INTO jobs(id,task_id,role,instruction,state,result,updated) VALUES(?,?,?,?,?,?,?)',
                  (jid,tid,'orquestrador',instruction,'queued','',time.time()))


def cycle(root):
    """Uma entrega por ciclo; lease persistente impede replay após crash."""
    from operacoes.continuidade import reconcile_finished,expire_waiting
    expire_waiting(root)
    reconcile_finished(root)
    state=state_module(root)
    # Aceite é uma alteração real do checkpoint pelo executor, não retorno do CLI.
    with database(root,True) as c:
        # Um comando em entrega não é repetido após reinício do serviço.
        c.execute("UPDATE central_commands SET state='needs_review',result=?,updated=? WHERE state='uncertain' AND updated<?",
                  ('Entrega interrompida ou sem confirmação; conferir checkpoint antes de repetir.',time.time(),time.time()-180))
        for row in c.execute("SELECT * FROM central_requests WHERE state IN ('dispatching','awaiting_ack','uncertain')").fetchall():
            record=c.execute('SELECT payload FROM tasks WHERE task_id=?',(row['id'],)).fetchone()
            task=json.loads(record[0]) if record else {}
            if task.get('checkpoint_at') or task.get('reserve_started_at'):
                c.execute("UPDATE central_requests SET state='accepted',reason=?,revision=revision+1,updated=? WHERE id=?",
                          ('Checkpoint atualizado pelo executor; acompanhe a execução nos detalhes.',time.time(),row['id']))
            elif row['state'] in ('dispatching','awaiting_ack') and time.time()-row['updated']>180:
                c.execute("UPDATE central_requests SET state='uncertain',reason=?,revision=revision+1,updated=? WHERE id=?",
                          ('Aceite ainda não confirmado; confira o executor e o checkpoint. O pedido não será reenviado automaticamente.',time.time(),row['id']))
        rows=[dict(r) for r in c.execute("SELECT * FROM central_requests WHERE state IN ('registered','waiting') ORDER BY created LIMIT 30")]
    for row in rows:
        team,reason=ready(root,json.loads(row['payload'])['mode'])
        reg=registry(root)
        agy_reserve=(team=='reserve' and reg.get('reserve_engine')=='antigravity')
        if team=='premium' or agy_reserve:
            from ponte_maestri import cli
            from operacoes.entrega_terminal import idle_input
            target_name=reg['roles']['orquestrador']['name'] if agy_reserve else reg['primary_name']
            try:
                terminal=cli(reg,['check',target_name],identity=reg['primary_terminal'],timeout=15)
                if not idle_input(terminal):
                    team=None;reason='Coordenador ocupado ou com rascunho no campo de entrada. Preserve ou conclua o texto no terminal; nenhum pedido foi colado ou enviado.'
            except Exception:
                team=None;reason='Não foi possível confirmar o terminal do coordenador. Nenhum pedido foi colado ou enviado.'
        with database(root,True) as c:
            fresh=c.execute('SELECT * FROM central_requests WHERE id=?',(row['id'],)).fetchone()
            if fresh['state'] not in ('registered','waiting'):continue
            if not team:
                c.execute("UPDATE central_requests SET state='waiting',reason=? WHERE id=?",(reason,row['id']));continue
            # Uma única entrada ativa por coordenador; histórico bloqueado não impede novos pedidos.
            busy=c.execute("SELECT 1 FROM tasks WHERE json_extract(payload,'$.owner')=? AND json_extract(payload,'$.phase') IN ('premium_active','reserve_active','handoff_preparing','review') LIMIT 1",(team,)).fetchone()
            if team=='reserve' and reg.get('reserve_engine')!='antigravity' and c.execute("SELECT 1 FROM jobs WHERE role='orquestrador' AND state IN ('running','queued','awaiting_operations','awaiting_delegations') LIMIT 1").fetchone():busy=True
            if busy:
                c.execute("UPDATE central_requests SET state='waiting',reason=? WHERE id=?",('Há tarefa ativa deste coordenador, inclusive fora da Central. Concluir ou conferir seu estado antes de encaminhar outra.',row['id']));continue
            create_task(c,root,row,team)
            c.execute("UPDATE central_requests SET state=?,team=?,reason=?,revision=revision+1,updated=? WHERE id=?",
                      ('dispatching' if (team=='premium' or agy_reserve) else 'awaiting_ack',team,'Aguardando aceite do coordenador.',time.time(),row['id']))
        if team=='premium' or agy_reserve:
            from ponte_maestri import cli
            instruction=('PEDIDO DIRETO DO USUÁRIO PELA CENTRAL. Tarefa já registrada: '+row['id']+
                '. Leia reserve_read_task para esse ID. NÃO use flow_task_open nem crie outro ID. '
                'Antes de executar, registre o plano/aceite pelo reserve_checkpoint preservando escopo e revisão atual. '
                'Execute somente o pedido registrado; acompanhe complementos e pausa. '
                'Use especialistas conforme a necessidade; tarefas simples podem ser executadas pelo coordenador, preservando validação exigida. '
                'Reutilize resultados e IDs confirmados; registre próxima etapa antes de parar por cota ou limite. '
                'Registre progresso e evidências; conclusão exige conferência independente. Pedido e aceite: '+row['payload'])
            try:
                if agy_reserve:
                    with database(root,True) as c:
                        c.execute('INSERT INTO events(task_id,kind,created) VALUES(?,?,?)',
                                  (row['id'],'central_pending_input_submit_intent',time.time()))
                    cli(reg,['ask',reg['roles']['orquestrador']['name'],instruction],identity=reg['primary_terminal'],timeout=60)
                else:
                    from operacoes.entrega_terminal import send_registered_request
                    def record_submit_intent():
                        with database(root,True) as c:
                            c.execute('INSERT INTO events(task_id,kind,created) VALUES(?,?,?)',
                                      (row['id'],'central_pending_input_submit_intent',time.time()))
                    send_registered_request(cli,reg,row['id'],instruction,
                                            reg['roles']['orquestrador']['terminalId'],record_submit_intent)
                status='awaiting_ack';reason='Encaminhado ao canal; aguardando atualização do checkpoint.'
            except Exception:
                status='uncertain';reason='Não foi possível confirmar a entrega. Não será repetida automaticamente.'
            with database(root,True) as c:
                c.execute("UPDATE central_requests SET state=?,reason=?,revision=revision+1,updated=? WHERE id=? AND state='dispatching'",(status,reason,time.time(),row['id']))
        break
    deliver_command(root)


def detail(root,tid):
    state=state_module(root);state.validate_task_id(tid)
    with database(root) as c:
        entry=c.execute('SELECT * FROM central_requests WHERE id=?',(tid,)).fetchone()
        record=c.execute('SELECT payload FROM tasks WHERE task_id=?',(tid,)).fetchone()
        if not entry and not record:raise ValueError('Tarefa não encontrada.')
        task=json.loads(record[0]) if record else {}
        actions=[dict(r) for r in c.execute('SELECT action_id,state,evidence,updated FROM actions WHERE task_id=?',(tid,))] if table_exists(c,'actions') else []
        jobs=[dict(r) for r in c.execute('SELECT id,role,state,updated FROM jobs WHERE task_id=? ORDER BY updated',(tid,))] if record else []
        from operacoes.continuidade import status as recovery_status
        recovery=recovery_status(c,tid)
        if table_exists(c,'central_recovery_policy'):
            policy=c.execute('SELECT enabled FROM central_recovery_policy WHERE task_id=?',(tid,)).fetchone()
            if policy:task['automatic_recovery']=bool(policy['enabled'])
        progress=[dict(r) for r in c.execute('SELECT actor,operation,state,updated FROM ops_progress WHERE task_id=? ORDER BY updated DESC',(tid,))] if table_exists(c,'ops_progress') else []
        outbox=[dict(r) for r in c.execute('SELECT action_id,requester_actor,state,attempts,next_retry,deadline,commit_started,reason,updated FROM whatsapp_queue WHERE task_id=? ORDER BY seq',(tid,))] if table_exists(c,'whatsapp_queue') else []
        events=[dict(r) for r in c.execute('SELECT kind,created FROM events WHERE task_id=? ORDER BY id DESC LIMIT 50',(tid,))] if record else []
        commands=[dict(r) for r in c.execute('SELECT id,kind,text,state,created,result FROM central_commands WHERE task_id=? ORDER BY created',(tid,))]
    validation=task.get('independent_validation') or task.get('completion_evidence')
    independent=task.get('independent_validation',{})
    independently_recorded=False
    if independent.get('job_id')=='interactive-validador':
        independently_recorded=bool(independent.get('approved') is True and independent.get('acceptance_met') is True and
                                    sorted(independent.get('action_ids',[]))==sorted(a['action_id'] for a in actions if a['state']=='confirmed'))
    elif independent.get('job_id'):
        with database(root) as c:
            validated=c.execute("SELECT 1 FROM jobs WHERE id=? AND task_id=? AND role='validador' AND state='done' AND result!=''",(independent['job_id'],tid)).fetchone()
        independently_recorded=bool(validated and independent.get('acceptance_met') is True and
                                    sorted(independent.get('action_ids',[]))==sorted(a['action_id'] for a in actions if a['state']=='confirmed'))
    # Fila ainda não clicada é espera, não resultado externo incerto.
    queue_by_id={q['action_id']:q for q in outbox}
    uncertain=any(a['state']=='unknown' or (a['state']=='in_progress' and not (
        queue_by_id.get(a['action_id'],{}).get('state') in ('queued','waiting_portal')
        and not queue_by_id[a['action_id']]['commit_started'])) for a in actions)
    reported=list(task.get('evidence',[]))
    reported.extend({'actor':role,'evidence':result['evidence']} for role,result in task.get('role_results',{}).items() if result.get('evidence'))
    evidence={'reported':reported,'confirmed':[a for a in actions if a['state']=='confirmed'],
              'validation':validation,'pending_reconciliation':uncertain,
              'independently_recorded':independently_recorded,
              'label':'Validação declarada; confira abaixo as evidências das ferramentas. Não equivale por si só a comprovação independente.' if validation and not uncertain else 'Comprovação completa ainda não registrada'}
    return {'task_id':tid,'entry':{k:entry[k] for k in ('state','team','reason','revision')} if entry else None,
            'request':task.get('request') or (json.loads(entry['payload'])['request'] if entry else ''),
            'task':task,'jobs':jobs,'events':events,'commands':commands,'evidence':evidence,
            'progress':progress,'outbox':outbox,'recovery':recovery}


def command(root,data):
    tid=clean(data.get('task_id'),80);state=state_module(root);state.validate_task_id(tid)
    key=clean(data.get('key'),80);kind=data.get('kind');text=clean(data.get('text',''),2000,False)
    if kind not in ('complement','pause','resume'):raise ValueError('Comando inválido.')
    if kind=='complement' and not text:raise ValueError('Informe o complemento.')
    cid=hashlib.sha256((tid+'\0'+key).encode()).hexdigest()
    with database(root,True) as c:
        old=c.execute('SELECT * FROM central_commands WHERE id=?',(cid,)).fetchone()
        if old:
            if old['kind']!=kind or old['text']!=text:raise ValueError('Comando já registrado com outro conteúdo.')
            return {'id':cid,'state':old['state']}
        taskrow=c.execute('SELECT payload FROM tasks WHERE task_id=?',(tid,)).fetchone()
        entry=c.execute('SELECT * FROM central_requests WHERE id=?',(tid,)).fetchone()
        if not taskrow:
            if not entry:raise ValueError('Tarefa não encontrada.')
            if data.get('revision')!=entry['revision']:raise ValueError('Dados atualizados; abra os detalhes novamente.')
            if kind=='complement':raise ValueError('Aguarde o coordenador receber o pedido para complementar.')
            allowed=('registered','waiting') if kind=='pause' else ('paused',)
            if entry['state'] not in allowed:raise ValueError('Pedido já foi encaminhado; atualize os detalhes.')
            c.execute('UPDATE central_requests SET state=?,reason=?,revision=revision+1,updated=? WHERE id=?',
                      ('paused' if kind=='pause' else 'registered','Pausa local solicitada.' if kind=='pause' else 'Retomada solicitada.',time.time(),tid))
            status='recorded'
        else:
            task=json.loads(taskrow[0])
            if task['revision']!=data.get('revision'):raise ValueError('Checkpoint mudou; atualize os detalhes.')
            if task['phase']=='completed':raise ValueError('Tarefa concluída; crie outro pedido.')
            task.setdefault('central_commands',[]).append({'id':cid,'kind':kind,'text':text,'at':time.time()})
            if kind=='pause' and task.get('owner')=='reserve':
                task['central_pause_owned']=task.get('central_pause_owned',False) or not task.get('sending_paused',False)
                task['sending_paused']=True
                task['central_pause_requested']=True
            state.put(c,task,'central_'+kind+'_requested')
            status='queued'
        c.execute('INSERT INTO central_commands(id,task_id,kind,text,state,created,updated) VALUES(?,?,?,?,?,?,?)',(cid,tid,kind,text,status,time.time(),time.time()))
    return {'id':cid,'state':status,'message':'Solicitação registrada. No principal, aguarde confirmação do coordenador. Operações em andamento precisam de conferência.'}


def deliver_command(root):
    # Pedido de controle não executa nova operação comercial; apenas informa o dono atual.
    reg=registry(root)
    with database(root,True) as c:
        row=c.execute("SELECT * FROM central_commands WHERE state='queued' ORDER BY created LIMIT 1").fetchone()
        if not row:return
        row=dict(row);record=c.execute('SELECT payload FROM tasks WHERE task_id=?',(row['task_id'],)).fetchone()
        if not record:return
        task=json.loads(record[0])
        if task.get('owner')!='premium' and reg.get('reserve_engine')!='antigravity' and row['kind']!='resume':
            # Checkpoint consultável pelos jobs; nenhuma nova inferência nem desbloqueio forçado.
            c.execute("UPDATE central_commands SET state='checkpoint',updated=? WHERE id=?",(time.time(),row['id']))
            return
        c.execute("UPDATE central_commands SET state='uncertain',updated=? WHERE id=?",(time.time(),row['id']))
    if task.get('owner')=='reserve' and reg.get('reserve_engine')!='antigravity':
        resume_reserve(root,row,task)
        return
    from ponte_maestri import cli
    target_name=reg['roles']['orquestrador']['name'] if task.get('owner')=='reserve' else reg['primary_name']
    instruction=('CONTROLE EXPLÍCITO DO USUÁRIO na tarefa '+row['task_id']+': '+row['kind']+'. '+row['text']+
                 '. Leia checkpoint atual e central_commands. Para pausa, finalize/reconcilie operação em andamento e confirme parada. '
                 'Para retomada, confira operações incertas e permissões; não repita ações confirmadas. Não crie outra tarefa.')
    try:
        cli(reg,['ask',target_name,instruction],identity=reg['primary_terminal'],timeout=60)
        with database(root,True) as c:c.execute("UPDATE central_commands SET state='forwarded',updated=? WHERE id=?",(time.time(),row['id']))
    except Exception:pass


def resume_reserve(root,command_row,task):
    """Somente pedido explícito, mesmos jobs e confirmação de ausência de execução."""
    from operacoes.recuperacao import inspect
    report=inspect(root,'reserve_orquestrador',task['task_id'])
    reasons=list(report['reasons'])
    if task.get('central_pause_owned') and 'human_pause_preserved' in reasons:
        reasons.remove('human_pause_preserved')
    with database(root) as c:
        queued=c.execute("SELECT 1 FROM jobs WHERE task_id=? AND state='queued'",(task['task_id'],)).fetchone()
        unsafe_queue=table_exists(c,'whatsapp_queue') and c.execute("SELECT 1 FROM whatsapp_queue WHERE task_id=? AND (state IN ('sending','confirming','unknown') OR (state IN ('queued','waiting_portal') AND commit_started=1))",(task['task_id'],)).fetchone()
        unsafe_actions=has_uncertain_effect(c,task['task_id'])
    if task.get('central_pause_owned') and not unsafe_queue and 'external_queue_not_settled' in reasons:
        reasons.remove('external_queue_not_settled')
    if task.get('central_pause_owned') and not unsafe_actions and 'external_reconciliation_required' in reasons:
        reasons.remove('external_reconciliation_required')
    if queued and 'no_interrupted_jobs' in reasons:reasons.remove('no_interrupted_jobs')
    module=state_module(root)
    with database(root,True) as c:
        current=module.get(c,task['task_id'])
        if current['revision']!=report['revision']:reasons.append('checkpoint_changed')
        if c.execute("SELECT 1 FROM jobs WHERE task_id=? AND state='running'",(task['task_id'],)).fetchone():reasons.append('job_still_running')
        if current.get('sending_paused') and not current.get('central_pause_owned'):reasons.append('human_pause_preserved')
        if has_uncertain_effect(c,task['task_id']):reasons.append('external_reconciliation_required')
        if table_exists(c,'whatsapp_queue') and c.execute("SELECT 1 FROM whatsapp_queue WHERE task_id=? AND (state IN ('sending','confirming','unknown') OR (state IN ('queued','waiting_portal') AND commit_started=1))",(task['task_id'],)).fetchone():reasons.append('external_queue_not_settled')
        if reasons:
            c.execute("UPDATE central_commands SET state='needs_review',result=?,updated=? WHERE id=?",
                      ('Retomada não executada: '+', '.join(dict.fromkeys(reasons)),time.time(),command_row['id']))
            return
        history=[]
        for jid in report['job_ids']:
            old=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not old or old['state'] not in ('blocked','paused'):raise ValueError('Estado mudou durante retomada.')
            history.append({'job_id':jid,'previous_state':old['state'],'result':old['result'],'at':time.time()})
            c.execute("UPDATE jobs SET state='queued',worker_pid=NULL,worker_birth=NULL,updated=? WHERE id=?",(time.time(),jid))
            current.get('job_blockers',{}).pop(jid,None)
        current.setdefault('recovery_history',[]).extend(history)
        if current.get('central_pause_owned'):
            current['sending_paused']=False;current['central_pause_owned']=False;current['central_pause_requested']=False
        current['phase']='reserve_active'
        module.put(c,current,'central_resume_confirmed')
        c.execute("UPDATE central_commands SET state='resumed',result=?,updated=? WHERE id=?",
                  ('Mesmos jobs recolocados na fila; resultados anteriores preservados.',time.time(),command_row['id']))


def has_uncertain_effect(c,tid):
    if not table_exists(c,'actions'):return False
    rows=c.execute("SELECT action_id,state FROM actions WHERE task_id=? AND state IN ('unknown','in_progress')",(tid,)).fetchall()
    for row in rows:
        if row['state']=='unknown':return True
        safe=table_exists(c,'whatsapp_queue') and c.execute("SELECT 1 FROM whatsapp_queue WHERE task_id=? AND action_id=? AND state IN ('queued','waiting_portal') AND commit_started=0",(tid,row['action_id'])).fetchone()
        if not safe:return True
    # Checkpoint-only legacy effects are not proved safe by absence from actions.
    record=c.execute('SELECT payload FROM tasks WHERE task_id=?',(tid,)).fetchone()
    known={r[0] for r in c.execute('SELECT action_id FROM actions WHERE task_id=?',(tid,))}
    known.update('op-'+hashlib.sha256(x.encode()).hexdigest()[:32] for x in list(known))
    return any(a.get('state') in ('unknown','in_progress') and a.get('id') not in known for a in json.loads(record[0]).get('external_actions',[])) if record else True
