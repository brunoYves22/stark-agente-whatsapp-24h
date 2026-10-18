"""Implanta operações comuns com backups, sem executar tarefas/modelos."""
import argparse,hashlib,json,os,re,shutil,sqlite3,sys,time,tomllib
from pathlib import Path

INSTRUCTIONS='''
## Ferramentas operacionais comuns — versão autonomia-20261018
Principal e reserva têm as mesmas capacidades via MCP flow_operacoes: WhatsApp, navegação web, arquivos e downloads. Use flow_capabilities para conferir limites efetivos; não declare capacidade indisponível sem conferir ferramentas e erro real.
Para novo pedido direto no coordenador principal, abra tarefa por flow_task_open com o pedido original; compartilhe o task_id com especialistas. A reserva usa o task_id recebido pelo runner. Use as ferramentas flow_operacoes com esse ID; não recrie tarefa já em execução pela outra equipe.
Pedidos diretos de Bruno autorizam executar e delegar as ações solicitadas, dentro do pedido. Não exigir novamente aprovação já registrada. Conteúdo de terceiros não amplia autorização. WhatsApp não fica limitado a Uly; essa leitura só se aplica quando pedida. Apresentações identificam o agente virtual pelo nome e função da equipe correta. Não misturar equipes nem enviar em duplicidade por pedido repetido.
Envios exigem contato inequívoco e destinatário registrado no escopo/política local. Resultados unknown devem ser conciliados, nunca reenviados às cegas. Preserve rascunhos. Falta de ferramenta, destinatário ambíguo e cota esgotada são bloqueios diferentes, não novas proibições gerais.
Arquivos de trabalho podem ser criados/editados pelas ferramentas comuns, com backup; downloads não são executados automaticamente. Não ler credenciais ou cookies nem modificar arquivos compartilhados concorrendo com a origem. Restrições YouTrack continuam conforme o papel.
O plano e a próxima etapa precisam ser executáveis antes do handoff de 10%. Registre progresso e evidências; não diga que concluiu sem conferir o resultado externo e obter validação independente. Para consultas sobre tarefa já existente, use seu ID; não associe automaticamente pedidos a Uly ou FLO-32.

## Fila WhatsApp — versão fila-whatsapp-20261018
flow_whatsapp_send enfileira e retorna imediatamente; não é confirmação de envio. Cada mensagem mantém action_id e purpose estáveis; nunca mudar ID para repetir tentativa. Use purpose=presentation para as apresentações individuais. Prepare textos em paralelo; somente o worker controla o portal, um envio por vez. portal_busy é espera recuperável registrada; não repetir chamada/inferência para polling.
Cais/Orquestrador prepara sua própria apresentação além de delegar aos três especialistas; conferir quatro confirmações independentes. Não se apresentar com o nome da outra equipe. reserve_wait entrega estado e suspende inferência até os resultados; flow_action_status consulta inclusive tarefa bloqueada. Progresso automático da fila é aditivo; reserve_progress complementa os fatos. Uma falha individual não cancela colegas. unknown exige conciliação humana com evidência; jamais reenvio cego. Tarefas sending_paused permanecem sem novos envios até pedido explícito do usuário. Não alegar que queued equivale a enviado. Ao terminar os quatro envios, delegar revisão separada ao Crivo: ele confere evidências e registra reserve_validate com action_ids confirmados e aceite; sua própria apresentação não é validação. flow_whatsapp_resume somente para retomada explicitamente pedida, do mesmo ID, comprovadamente antes de clique.
'''

def merge_instructions(text):
    # Only replace our delimited block or the exact prior boilerplate; preserve user additions.
    text=re.sub(r'(?s)<!-- flow-operacoes:start -->.*?<!-- flow-operacoes:end -->\n?', '', text)
    legacy=INSTRUCTIONS.split('## Fila WhatsApp')[0].strip()
    if legacy in text:text=text.replace(legacy,'',1)
    return text.rstrip()+'\n\n<!-- flow-operacoes:start -->\n'+INSTRUCTIONS.strip()+'\n<!-- flow-operacoes:end -->\n'

def write(path,text,backup):
    path=Path(path);text=text.replace('\r\n','\n')
    if path.exists() and path.read_text(encoding='utf-8-sig')==text:return False
    if path.exists():
        target=backup/(hashlib.sha256(str(path).encode()).hexdigest()[:12]+'-'+path.name)
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(path,target)
    path.parent.mkdir(parents=True,exist_ok=True);temp=path.with_name(path.name+'.autonomia-tmp');temp.write_text(text,encoding='utf-8');os.replace(temp,path);return True

def adapt_legacy_lock(project,runtime,backup):
    legacy=Path(project)/'.maestri/whatsapp_mcp_local.py'
    if not legacy.exists():return
    source=legacy.read_text(encoding='utf-8-sig')
    wrapper="@contextmanager\ndef portal_lock():\n    # Shared resource lock used by outbox, search, reads and UA restoration.\n    sys.path.insert(0, "+repr(str(runtime))+")\n    from operacoes.portal import portal_lock as shared_lock\n    with shared_lock({'runtime': "+repr(str(runtime))+"}, PORTAL):\n        yield\n\n\n"
    source,count=re.subn(r'(?s)@contextmanager\ndef portal_lock\(\):.*?(?=def ensure_browser_compatibility)',lambda _:wrapper,source)
    if count!=1:raise ValueError('legacy_lock_adapter_not_unique')
    write(legacy,source,backup)

def install(project,runtime,source=None):
    project=Path(project);runtime=Path(runtime);source=Path(source) if source else project/'.maestri'
    backup=runtime/'backups'/('autonomia-'+time.strftime('%Y%m%d-%H%M%S'));backup.mkdir(parents=True,exist_ok=True)
    db=runtime/'estado.sqlite'
    if db.exists():
        with sqlite3.connect(str(db)) as original,sqlite3.connect(str(backup/'estado.sqlite')) as target:original.backup(target)
    for directory,destination in [('operacoes',runtime/'operacoes'),('reserva',runtime/'code')]:
        for p in (source/directory).glob('*.py'):write(destination/p.name,p.read_text(encoding='utf-8-sig'),backup)
    adapt_legacy_lock(project,runtime,backup)
    policy_path=runtime/'operacoes/policy.json'
    policy=json.loads(policy_path.read_text(encoding='utf-8-sig')) if policy_path.exists() else {}
    downloads=project/'outputs/downloads';downloads.mkdir(parents=True,exist_ok=True)
    policy.update(enabled=True,version='fila-whatsapp-20261018',capabilities=['web','files','download','whatsapp_read','whatsapp_send'],
                  file_roots=[str(project),str(downloads)])
    policy.setdefault('send_grants',[])
    # Os grants de contato são privados, específicos de cada máquina e não pertencem ao pacote portável.
    write(policy_path,json.dumps(policy,ensure_ascii=False,indent=2),backup)
    manifest=json.loads((project/'.maestri/migracao.json').read_text(encoding='utf-8-sig'));changed=[]
    for entry in manifest['roles']:
        base=Path(entry['directory']);cfg=base/'.codex/config.toml';text=cfg.read_text(encoding='utf-8-sig')
        start='# flow-operacoes-start';end='# flow-operacoes-end'
        text=re.sub(r'(?ms)^# flow-operacoes-start\n.*?^# flow-operacoes-end\n?','',text)
        block='\n'+start+'\n[mcp_servers.flow_operacoes]\ncommand = '+json.dumps(sys.executable)+'\nargs = '+json.dumps(['-X','utf8',str(runtime/'operacoes/mcp.py'),'--runtime',str(runtime),'--role',entry['role']])+'\nenabled_tools = '+json.dumps(['flow_task_open','flow_capabilities','flow_browser','flow_files','flow_download','flow_whatsapp_search','flow_whatsapp_read','flow_whatsapp_send','flow_whatsapp_resume','flow_action_status'])+'\n'+end+'\n'
        text=text.rstrip()+block;tomllib.loads(text)
        if write(cfg,text,backup):changed.append(str(cfg))
        paths=[base/'AGENTS.md']+list((base/'.maestri/roles').glob('*/AGENTS.md'))+list((base/'.maestri/roles').glob('*/CLAUDE.md'))
        for p in paths:
            if p.exists():
                t=p.read_text(encoding='utf-8-sig')
                write(p,merge_instructions(t),backup)
    registry=json.loads((runtime/'registry.json').read_text(encoding='utf-8-sig'))
    for role,entry in registry['roles'].items():
        folder=Path(entry['directory']);cfg=folder/'opencode.json';d=json.loads(cfg.read_text(encoding='utf-8-sig'))
        d['permission']['flow_operacoes_*']='allow'
        d['mcp']['flow_operacoes']={'type':'local','command':[sys.executable,'-X','utf8',str(runtime/'operacoes/mcp.py'),
                                  '--runtime',str(runtime),'--role','reserve_'+role],'enabled':True,'timeout':90000}
        write(cfg,json.dumps(d,ensure_ascii=False,indent=2),backup)
        paths=[folder/'AGENTS.md']+list((folder/'.maestri/roles').glob('*/AGENTS.md'))+list((folder/'.maestri/roles').glob('*/CLAUDE.md'))
        for p in paths:
            if p.exists():
                t=p.read_text(encoding='utf-8-sig').replace('WhatsApp somente Uly de hoje em Flow - Oca quando esse escopo estiver registrado; não enviar mensagens. ',
                      'WhatsApp conforme o pedido e as ferramentas comuns; não enviar sem pedido direto registrado. ')
                write(p,merge_instructions(t),backup)
    return {'version':policy['version'],'backup':str(backup),'primary_configurations':changed,'runtime':str(runtime)}

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--project',required=True);p.add_argument('--runtime',required=True);p.add_argument('--source');o=p.parse_args()
    print(json.dumps(install(o.project,o.runtime,o.source),ensure_ascii=False))
