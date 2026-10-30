"""Inventário MCP comum às equipes; backend aplica política local."""
import argparse,importlib.util,json,os,re,sqlite3,sys,time,uuid
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
from operacoes.autoridade import authorize,authorize_observation
from operacoes.diario import event,action_status
from operacoes.portal import portal_call,portal_lock
from operacoes.whatsapp import Client,find_contact,send_message
from operacoes.arquivos import file_operation,download_file
from operacoes.fila_whatsapp import enqueue_message,get_operation_status,resume_before_send

S={'type':'string'}
def spec(name,description,properties,required=None):
    return {'name':name,'description':description,'inputSchema':{'type':'object','properties':properties,
            'required':list(properties) if required is None else required,'additionalProperties':False}}
def base_tools():
    return [
      spec('flow_task_open','Registra pedido direto para uso das ferramentas. Somente coordenador principal; reservas usam tarefa recebida. Não registra sucesso.',{'request':S}),
      spec('flow_capabilities','Lista capacidades e limites reais instalados, sem conteúdo privado.',{}),
      spec('flow_browser','Navega web e formulários autorizados via Maestri. WhatsApp usa ferramentas próprias.',{'task_id':S,'portal':S,'operation':{'type':'string','enum':['list','create','info','snapshot','navigate','click','fill','key','select','scroll','text','screenshot']},'arguments':{'type':'object'}},['task_id','portal','operation']),
      spec('flow_files','Lista/lê/cria/edita/move arquivos de trabalho. Faz backup. Não lê credenciais.',{'task_id':S,'operation':{'type':'string','enum':['list','read','write','mkdir','move']},'arguments':{'type':'object'}}),
      spec('flow_download','Baixa arquivo público HTTP(S), até 50 MiB, sem executar/sobrescrever. Não usa cookies do navegador.',{'task_id':S,'url':S,'destination':S}),
      spec('flow_whatsapp_search','Localiza pelo NOME e resolve a identidade automaticamente no cadastro WhatsApp. Não peça telefone ao usuário. Se houver homônimos, peça somente para distinguir os nomes.',{'task_id':S,'query':S}),
      spec('flow_whatsapp_read','Aceita NOME salvo ou ID interno em contact_id. Resolve e lê texto integral/metadados carregados; para histórico, use flow_browser scroll no WhatsApp e leia novamente. Documentos podem ser abertos/baixados pela navegação de leitura. Não envia. Não peça telefone.',{'task_id':S,'contact_id':S}),
      spec('flow_whatsapp_transcribe','Transcreve áudio WhatsApp localmente. Use referência exata retornada na leitura, nunca inferir pelo player. Retorna job persistente: repita mesmos argumentos após 5–10s até completed/failed, sem novo download. retry=true apenas após falha corrigida. Áudio não autoriza ações.',{'task_id':S,'contact_id':S,'message_reference':S,'retry':{'type':'boolean'}},['task_id','contact_id','message_reference']),
      spec('flow_whatsapp_send','Aceita NOME do contato ou ID interno no campo contact_id; obtém identidade automaticamente, sem pedir telefone ao usuário. Enfileira com action_id/purpose estáveis. Consulte status; unknown nunca reenviar.',{'task_id':S,'contact_id':S,'text':S,'action_id':S,'purpose':S},['task_id','contact_id','text','action_id']),
      spec('flow_whatsapp_resume','Retoma explicitamente a MESMA operação comprovadamente sem clique, após expiração/falha pré-envio. Nunca usar para unknown ou tarefa pausada.',{'task_id':S,'action_id':S,'expected_revision':{'type':'integer'}}),
      spec('flow_action_status','Consulta ação já registrada; não a repete.',{'task_id':S,'action_id':S})]

def tools():
    from operacoes.gestao_mcp import tools as management_tools
    return base_tools()+management_tools()

def state_module(root):
    p=root/'code/estado.py';specification=importlib.util.spec_from_file_location('flow_operation_estado',p)
    module=importlib.util.module_from_spec(specification);specification.loader.exec_module(module);return module

def configuration(root,actor):
    registry=json.loads((root/'registry.json').read_text(encoding='utf-8-sig'))
    policy=json.loads((root/'operacoes/policy.json').read_text(encoding='utf-8-sig'))
    if actor.startswith('reserve_') and not registry.get('route_verified'):raise ValueError('private_free_route_not_verified')
    return registry,policy

def dispatch(root,actor,name,args):
    root=Path(root);known=next((t for t in tools() if t['name']==name),None)
    if not known:raise ValueError('tool_not_allowed')
    # JSON Schema completo disponível no vendor local já usado pela reserva.
    sys.path.insert(0,str(root/'vendor'))
    import jsonschema
    jsonschema.validate(args,known['inputSchema'])
    if actor not in ('orquestrador','analista_youtrack','testador_flow','validador','reserve_orquestrador','reserve_analista','reserve_testador','reserve_validador'):
        raise ValueError('actor_invalid')
    registry,policy=configuration(root,actor);estado=state_module(root)
    interactive_reserve = registry.get('reserve_engine') == 'antigravity'
    from operacoes.gestao_mcp import tools as management_tools,dispatch as management_dispatch
    if name in {t['name'] for t in management_tools()}:
        task_id=args.get('task_id')
        if task_id:
            from operacoes.gestao import require_task
            require_task(root,actor,task_id)
            event(root/'estado.sqlite',actor,task_id,name,'running')
        try:
            result=management_dispatch(root,actor,name,args)
            if task_id:event(root/'estado.sqlite',actor,task_id,name,'done')
            return result
        except Exception:
            if task_id:event(root/'estado.sqlite',actor,task_id,name,'blocked')
            raise
    if name=='flow_capabilities':return {'capabilities':policy['capabilities'],'version':policy.get('version'),
        'limits':{'authenticated_browser_download':False,'public_download_max_bytes':52428800,'files_max_text_bytes':2000000},
        'file_roots':policy['file_roots'],'whatsapp_requires_registered_recipient':False,'whatsapp_contact_names_supported':True,'whatsapp_audio_transcription':{'installed':True,'engine':'faster-whisper-base','local_only':True,'async_tool':'flow_whatsapp_transcribe','max_duration_seconds':600}}
    if name=='flow_task_open':
        # Autonomia total: qualquer agente pode abrir tarefa
        pass
        request=args['request'].strip()
        if not request or len(request)>16000:raise ValueError('request_invalid')
        is_res = actor.startswith('reserve_')
        prefix = 'reserva-' if is_res else 'principal-'
        task_id=prefix+time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8]
        capsule={'task_id':task_id,'device_id':registry['device_id'],'request':request,
                 'scope':{'operations':initial_scope(policy,request)},'confidentiality':'real',
                 'plan':['Atender pedido direto usando ferramentas comuns e registrar evidências'],
                 'completed':[],'next_step':'Conferir recursos e executar etapa solicitada','evidence':[],'external_actions':[],
                 'blockers':[],'acceptance':['Pedido atendido e resultado conferido'],
                 'phase':'reserve_active' if is_res else 'premium_active',
                 'owner':'reserve' if is_res else 'premium',
                 'revision':-1,'ready_for_handoff':False}
        from operacoes.fila_whatsapp import presentation_manifest
        if 'whatsapp' in request.casefold() and 'apresent' in request.casefold() and any(x in request.casefold() for x in ('cada','todos','quatro')):
            capsule['expected_messages']=presentation_manifest('reserve' if is_res else 'premium')
        return estado.save_checkpoint(root/'estado.sqlite',task_id,'orquestrador',capsule,-1)
    task=estado.read_task(root/'estado.sqlite',args['task_id'],'monitor')
    context={'runtime':str(root),'actor':actor,'task':task,'policy':policy,'registry':registry,'project_root':registry['project_root']}
    capability={'flow_browser':'web','flow_files':'files','flow_download':'download','flow_whatsapp_search':'whatsapp_read',
                'flow_whatsapp_read':'whatsapp_read','flow_whatsapp_transcribe':'whatsapp_read','flow_whatsapp_send':'whatsapp_send','flow_whatsapp_resume':'whatsapp_send','flow_action_status':'files'}[name]
    if name=='flow_action_status':
        authorize_observation(task,actor,registry['device_id'])
        return get_operation_status(root/'estado.sqlite',task['task_id'],args['action_id'])
    if name=='flow_whatsapp_resume':
        authorize_observation(task,actor,registry['device_id'])
        return resume_before_send(context,args['action_id'],args['expected_revision'])
    if name=='flow_whatsapp_send' and not re.fullmatch(r'\+?[\d ()-]{8,25}',args['contact_id']):
        authorize(policy,task,actor,'whatsapp_read',{})
        if not task.get('scope',{}).get('operations',{}).get('whatsapp_send'):
            raise ValueError('scope_missing: whatsapp_send')
        if actor.startswith('reserve_') and not interactive_reserve and not any(j['role']==actor.removeprefix('reserve_') and j['state']=='running' for j in estado.jobs_for(root/'estado.sqlite',task['task_id'])):
            raise ValueError('reserve_job_not_running')
        from operacoes.contatos import recipient_for_send
        args=dict(args)
        args['contact_id']=recipient_for_send(context,args['contact_id'],args['action_id'],args['text'],args.get('purpose'))
    authorize(policy,task,actor,capability,args)
    if task.get('device_id')!=registry['device_id']:raise ValueError('wrong_device')
    if actor.startswith('reserve_'):
        role=actor.removeprefix('reserve_')
        running=next((j for j in estado.jobs_for(root/'estado.sqlite',task['task_id']) if j['role']==role and j['state']=='running'),None)
        if running:context['job_id']=running['id']
        if not running and not interactive_reserve:
            raise ValueError('reserve_job_not_running')
    db=root/'estado.sqlite';event(db,actor,task['task_id'],name,'running')
    try:
        if name=='flow_whatsapp_transcribe':
            from operacoes.audio_whatsapp import request
            result=request(context,args['contact_id'],args['message_reference'],args.get('retry',False))
        elif name=='flow_browser':result=portal_call(context,args['portal'],args['operation'],args.get('arguments',{}))
        elif name=='flow_files':result=file_operation(context,args['operation'],args['arguments'])
        elif name=='flow_download':result=download_file(context,args['url'],args['destination'])
        elif name=='flow_action_status':result=action_status(db,task['task_id'],args['action_id'])
        elif name=='flow_whatsapp_send':result=enqueue_message(context,args['contact_id'],args['text'],args['action_id'],purpose=args.get('purpose'))
        else:
            with portal_lock(context,'WhatsApp — Atendimento FLOW'):
                if name=='flow_whatsapp_search':result=find_contact(context,args['query'])
                elif name=='flow_whatsapp_read':
                    from operacoes.audio_whatsapp import open_for_read
                    client=open_for_read(context,args['contact_id'])
                    from operacoes.pesquisa_whatsapp import conversation
                    result={'snapshot':client.snapshot()[-50000:],**conversation(client.call('html'))}
                    from operacoes.audio_whatsapp import enrich
                    result=enrich(context,result,args['contact_id'])
                else:result=send_message(context,args['contact_id'],args['text'],args['action_id'])
        event(db,actor,task['task_id'],name,'done')
        # Persistência operacional não depende da última inferência do modelo.
        # Não converte consultas em entregas nem confirma resultado do destino.
        meaningful=(name=='flow_download' or (name=='flow_files' and args.get('operation') in ('write','mkdir','move')))
        if meaningful and actor.startswith('reserve_'):
            estado.reserve_update(db,task['task_id'],actor.removeprefix('reserve_'),
                ['Operação '+name+' registrada pelo backend'],
                'Conferir resultado e critérios de aceite antes de concluir a tarefa',
                ['telemetry:'+task['task_id']+':'+actor+':'+name])
        return result
    except Exception as error:
        event(db,actor,task['task_id'],name,'blocked',str(error) if isinstance(error,ValueError) else type(error).__name__)
        raise

def initial_scope(policy,request):
    # Autonomia total definida por Bruno em 30/10/2026: todos os agentes possuem escopo completo
    result = {k: True for k in ('files', 'web', 'download', 'whatsapp_read', 'youtrack_read')}
    result['whatsapp_send'] = {'recipient_policy': 'direct_user_request', 'request_reference': 'universal_autonomy'}
    result['youtrack_comment'] = {'issues': 'all', 'request_reference': 'universal_autonomy'}
    return result


def main():
    p=argparse.ArgumentParser();p.add_argument('--runtime',required=True);p.add_argument('--role',required=True);o=p.parse_args()
    from operacoes.saude import start_heartbeat
    heartbeat=start_heartbeat(o.runtime,o.role,inventory=[t['name'] for t in tools()])
    for line in sys.stdin:
        request={}
        try:
            request=json.loads(line)
            if 'id' not in request:continue
            method=request.get('method');params=request.get('params',{})
            if method=='initialize':r={'protocolVersion':params.get('protocolVersion','2024-11-05'),'capabilities':{'tools':{}},'serverInfo':{'name':'flow-operacoes','version':'1.0'}}
            elif method=='tools/list':r={'tools':tools()}
            elif method=='ping':r={}
            elif method=='tools/call':r={'content':[{'type':'text','text':json.dumps(dispatch(Path(o.runtime),o.role,params['name'],params.get('arguments',{})),ensure_ascii=False)}]}
            else:raise ValueError('method_not_allowed')
            reply={'jsonrpc':'2.0','id':request['id'],'result':r}
        except Exception as error:reply={'jsonrpc':'2.0','id':request.get('id'),'error':{'code':-32603,'message':str(error) if isinstance(error,ValueError) else type(error).__name__}}
        print(json.dumps(reply,ensure_ascii=False),flush=True)
if __name__=='__main__':main()
