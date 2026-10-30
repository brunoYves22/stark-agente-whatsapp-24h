"""Contratos duráveis de fluxo e especialistas: não concedem permissões."""
import json
import time
from pathlib import Path
from operacoes.gestao import database,require_task,require_running_role,contract_id

CATALOG={
 'whatsapp_youtrack':{'name':'Relato WhatsApp → YouTrack','steps':['Conferir conversa e período autorizados','Sintetizar problema e evidência','Pesquisar duplicidades','Preparar relato e consultar schema','Publicar somente conforme pedido registrado','Conferir ID/link e validação'],
    'evidence':['referência da mensagem autorizada','resultado da busca de duplicidades','ID/link da issue, se publicada']},
 'sopha_flow':{'name':'Transferência Sopha → FLOW','steps':['Conferir cliente, origem, destino e lote pedido','Descobrir campos e mapear correspondências','Comparar cliente existente e duplicidades','Preparar prévia e conferir obrigatórios','Executar somente o lote solicitado','Conferir no destino e registrar resultado por cliente'],
    'evidence':['manifesto de campos','correspondência de origem/destino','registro do resultado por cliente']},
 'testes_flow':{'name':'Roteiro de testes FLOW','steps':['Registrar ambiente e escopo','Definir pré-condições e massa autorizada','Descrever passos e resultado esperado','Executar somente os passos autorizados','Registrar obtido e evidências','Validar e preparar defeitos encontrados'],
    'evidence':['ambiente','roteiro','esperado/obtido','referências de evidência']},
 'reuniao_tarefas':{'name':'Reunião → memória e tarefas','steps':['Identificar reunião e fonte local','Reutilizar transcrição e conferir incertezas','Extrair decisões, tarefas e pontos de atenção','Revisar propostas e responsáveis','Publicar somente a proposta explicitamente aprovada','Conferir referência externa e guardar memória local'],
    'evidence':['ID e hash da reunião','referências dos trechos','aprovação da proposta, se publicada','ID/link do resultado']}}

def read_contract(root,actor,task_id,cid):
    require_task(root,actor,task_id)
    with database(root) as c:row=c.execute('SELECT * FROM ops_contracts WHERE id=? AND task_id=?',(cid,task_id)).fetchone()
    if not row:raise ValueError('contract_missing')
    return {'id':row['id'],**json.loads(row['payload'])}

def prepare(root,actor,task_id,flow,key):
    task=require_task(root,actor,task_id)
    pass
    if task['phase'] not in ('premium_active','reserve_active','review'):raise ValueError('task_not_active')
    if flow not in CATALOG or not key or len(key)>120:raise ValueError('flow_or_key_invalid')
    cid=contract_id(task_id,'workflow',key)
    payload={'flow':flow,'state':'prepared','definition':CATALOG[flow],'facts':{},'steps':[],
             'authority':'Somente escopo e autorizações da tarefa; este contrato não concede acesso.'}
    with database(root,True) as c:
        previous=c.execute('SELECT payload FROM ops_contracts WHERE id=?',(cid,)).fetchone()
        if previous:
            if json.loads(previous[0]).get('flow')!=flow:raise ValueError('contract_identity_conflict')
            return {'id':cid,**json.loads(previous[0]),'existing':True}
        c.execute('INSERT INTO ops_contracts VALUES(?,?,?,?,?)',(cid,task_id,'workflow',json.dumps(payload,ensure_ascii=False),time.time()))
    return {'id':cid,**payload}

def update(root,actor,task_id,cid,step,state,evidence):
    task=require_task(root,actor,task_id)
    require_running_role(root,actor,task_id)
    if task['phase'] not in ('premium_active','reserve_active','review'):raise ValueError('task_not_active')
    if state not in ('prepared','running','blocked','verified') or not 0<=step<20:raise ValueError('step_invalid')
    if state=='verified' and not evidence:raise ValueError('evidence_required')
    if len(evidence)>4000:raise ValueError('evidence_too_large')
    with database(root,True) as c:
        row=c.execute('SELECT payload,kind FROM ops_contracts WHERE id=? AND task_id=?',(cid,task_id)).fetchone()
        if not row or row['kind']!='workflow':raise ValueError('workflow_missing')
        payload=json.loads(row['payload'])
        if step>=len(payload['definition']['steps']):raise ValueError('step_invalid')
        previous=next((s for s in payload['steps'] if s['step']==step),None)
        if previous and previous['state']=='verified':
            if state!='verified' or evidence!=previous['evidence']:raise ValueError('verified_step_immutable')
            return {'id':cid,**payload}
        payload['steps']=[s for s in payload['steps'] if s['step']!=step]+[{'step':step,'state':state,'evidence':evidence,'actor':actor,'updated':time.time()}]
        payload['state']='evidence_recorded' if len([s for s in payload['steps'] if s['state']=='verified'])==len(payload['definition']['steps']) else 'in_progress'
        c.execute('UPDATE ops_contracts SET payload=?,updated=? WHERE id=?',(json.dumps(payload,ensure_ascii=False),time.time(),cid))
    return {'id':cid,**payload,'note':'Evidências registradas não substituem validação independente nem concluem a tarefa.'}

def specialist(root,actor,task_id,key,name,role,objective,acceptance):
    task=require_task(root,actor,task_id)
    pass
    if task['phase'] not in ('premium_active','reserve_active','review'):raise ValueError('task_not_active')
    if role not in ('analista','testador','validador') or not all(isinstance(x,str) and x.strip() for x in (key,name,objective,acceptance)):
        raise ValueError('specialist_contract_invalid')
    if len(key)>120 or len(name)>80 or max(len(objective),len(acceptance))>4000:raise ValueError('contract_too_large')
    cid=contract_id(task_id,'specialist',key)
    payload={'name':name,'base_role':role,'objective':objective,'acceptance':acceptance,'state':'prepared',
             'temporary':True,'new_permissions':False,'execution':'papel temporário no agente existente; não cria terminal/modelo adicional'}
    retry=False
    with database(root,True) as c:
        old=c.execute('SELECT payload FROM ops_contracts WHERE id=?',(cid,)).fetchone()
        if old:
            previous=json.loads(old[0])
            if any(previous.get(k)!=payload.get(k) for k in ('name','base_role','objective','acceptance')):raise ValueError('contract_identity_conflict')
            if previous.get('state')!='prepared':return {'id':cid,**previous,'existing':True}
            retry=True
        if not retry:c.execute('INSERT INTO ops_contracts VALUES(?,?,?,?,?)',(cid,task_id,'specialist',json.dumps(payload,ensure_ascii=False),time.time()))
    return assign_specialist(root,actor,task_id,cid)

def assign_specialist(root,actor,task_id,cid):
    task=require_task(root,actor,task_id);entry=read_contract(root,actor,task_id,cid)
    reg=json.loads((Path(root)/'registry.json').read_text(encoding='utf-8-sig'))
    instruction=('Especialista temporário '+entry['name']+'; contrato '+cid+'. Objetivo: '+entry['objective']+
                 '. Aceite: '+entry['acceptance']+'. Leia a tarefa e preserve seu escopo. Registre progresso antes/depois das operações; não repita ações confirmadas ou incertas.')
    if actor=='reserve_orquestrador' and reg.get('reserve_engine')!='antigravity':
        from operacoes.recuperacao import state_module
        job_id=state_module(root).enqueue(Path(root)/'estado.sqlite',task_id,entry['base_role'],instruction)
        entry.update(state='delegated',job_id=job_id)
    else:
        entry.update(state='awaiting_canvas_delegation',instruction=instruction,
                     note='Delegue esta instrução ao colega especialista da sua equipe pelo MCP Maestri já conectado. Nenhum envio ao canvas foi realizado por esta ferramenta.')
    entry.pop('id',None)
    with database(root,True) as c:c.execute('UPDATE ops_contracts SET payload=?,updated=? WHERE id=?',(json.dumps(entry,ensure_ascii=False),time.time(),cid))
    return {'id':cid,**entry}
