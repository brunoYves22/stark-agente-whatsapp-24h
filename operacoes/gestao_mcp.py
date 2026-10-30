"""Ferramentas comuns de acompanhamento, contratos e recuperação."""
from pathlib import Path
from operacoes.gestao import overview,require_task,require_running_role,progress

S={'type':'string'}
def tool(name,description,properties):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,'required':list(properties),'additionalProperties':False}}

def tools():
    return [
      tool('flow_youtrack_comment','Coordenadores: publica comentário em issue FLO explicitamente solicitada no pedido. Preserve action_id; reconcilia duplicidade e não repete operação incerta.',{'task_id':S,'issue_id':S,'text':S,'action_id':S}),
      tool('flow_operations_status','Quem trabalha, o que está parado, fila e cotas locais; sem dados de cliente.',{}),
      tool('flow_diagnose','Atualiza inventário do Maestri e cota gratuita sem inferência, mensagens ou operações comerciais.',{}),
      tool('flow_integration_observe','Registra observação de integração com evidência. Distingue relato do agente de verificação do backend.',{'task_id':S,'integration':{'type':'string','enum':['whatsapp','youtrack','flow','sopha']},'state':{'type':'string','enum':['available','disconnected','unavailable','busy','unknown']},'evidence_reference':S}),
      tool('flow_recovery_inspect','Avalia retomada por ID; não reinicia nem envia.',{'task_id':S}),
      tool('flow_recovery_resume','Somente após pedido explícito para retomar: mesmos jobs/IDs, revisão e verificações. Não remove pausa humana.',{'task_id':S,'expected_revision':{'type':'integer'},'explicit_resume_requested':{'type':'boolean'}}),
      tool('flow_workflow_catalog','Lista procedimentos de atendimento, transferência e testes.',{}),
      tool('flow_workflow_prepare','Cria contrato estável de fluxo dentro da tarefa, sem executar nem ampliar autorização.',{'task_id':S,'workflow':{'type':'string','enum':['whatsapp_youtrack','sopha_flow','testes_flow','reuniao_tarefas']},'key':S}),
      tool('flow_workflow_step','Registra etapa/evidência; não equivale a validação independente ou conclusão.',{'task_id':S,'contract_id':S,'step':{'type':'integer','minimum':0},'state':{'type':'string','enum':['prepared','running','blocked','verified']},'evidence':S}),
      tool('flow_specialist_create','Define especialista temporário no papel existente, sem novo modelo/permissão. Reserva delega; principal recebe instrução para delegar no canvas.',{'task_id':S,'key':S,'name':S,'role':{'type':'string','enum':['analista','testador','validador']},'objective':S,'acceptance':S}),
      tool('flow_contract_read','Lê contrato autorizado por ID, para conferir objetivo, etapas e aceite.',{'task_id':S,'contract_id':S}),
      tool('flow_knowledge_export','Atualiza índices e projeções do cofre FLOW compartilhado; preserva notas manuais.',{}),
      tool('flow_knowledge_read','Lê notas permitidas do cofre FLOW, incluindo contexto compartilhado e reuniões privadas; notas não concedem autorização.',{'relative_path':S})]

def dispatch(root,actor,name,args):
    root=Path(root)
    if name=='flow_youtrack_comment':
        from operacoes.youtrack_comentarios import comment
        return comment(root,actor,args)
    if name=='flow_operations_status':return overview(root)
    if name=='flow_diagnose':
        from operacoes.diagnostico import refresh_integrations,refresh_quotas
        refresh_integrations(root);refresh_quotas(root,True)
        return overview(root)
    if name=='flow_workflow_catalog':
        from operacoes.fluxos import CATALOG
        return CATALOG
    if name=='flow_knowledge_export':
        pass
        from operacoes.conhecimento import export
        return export(root)
    if name=='flow_knowledge_read':
        from operacoes.conhecimento import read
        return read(root,args['relative_path'])
    task=require_task(root,actor,args['task_id'])
    if name=='flow_integration_observe':
        require_running_role(root,actor,task['task_id'])
        from operacoes.diagnostico import record_integration
        return record_integration(root,args['integration'],args['state'],args['evidence_reference'])
    if name.startswith('flow_recovery_'):
        from operacoes.recuperacao import inspect,resume
        if name=='flow_recovery_inspect':return inspect(root,actor,task['task_id'])
        if args['explicit_resume_requested'] is not True:raise ValueError('explicit_resume_required')
        return resume(root,actor,task['task_id'],args['expected_revision'])
    from operacoes.fluxos import prepare,update,specialist,read_contract
    if name=='flow_workflow_prepare':return prepare(root,actor,task['task_id'],args['workflow'],args['key'])
    if name=='flow_workflow_step':return update(root,actor,task['task_id'],args['contract_id'],args['step'],args['state'],args['evidence'])
    if name=='flow_specialist_create':return specialist(root,actor,task['task_id'],args['key'],args['name'],args['role'],args['objective'],args['acceptance'])
    if name=='flow_contract_read':return read_contract(root,actor,task['task_id'],args['contract_id'])
    raise ValueError('management_tool_unknown')
