"""Painel local de consulta; nenhuma inferência e nenhum botão de execução."""
import argparse,json,sqlite3,time,sys
from contextlib import closing
from pathlib import Path
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

def legacy_snapshot(runtime):
    root=Path(runtime);registry=json.loads((root/'registry.json').read_text(encoding='utf-8'));now=time.time();tasks=[];agents=[];events=[]
    db=root/'estado.sqlite'
    if db.exists():
        with closing(sqlite3.connect('file:'+db.as_posix()+'?mode=ro',uri=True)) as c:
            for payload,updated in c.execute('SELECT payload,updated FROM tasks ORDER BY updated DESC LIMIT 50'):
                t=json.loads(payload)
                tasks.append({'id':t['task_id'],'team':t.get('owner'),'state':t['phase'],'updated':updated,
                              'next_step':t.get('next_step','')[:180],'blocker':(t.get('blockers') or [''])[-1][:180]})
            for role,state,task_id,updated in c.execute('SELECT role,state,task_id,MAX(updated) FROM jobs GROUP BY role'):
                agents.append({'name':registry.get('roles',{}).get(role,{}).get('name',role),'team':'reserve','task':task_id,
                               'state':state,'age_seconds':round(now-updated),'stale':state=='running' and now-updated>30})
    actions=root/'estado.sqlite'
    if actions.exists():
        with closing(sqlite3.connect('file:'+actions.as_posix()+'?mode=ro',uri=True)) as c:
            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='telemetry'").fetchone():
                events=[{'actor':r[0],'task':r[1],'operation':r[2],'state':r[3],'age_seconds':round(now-r[4])}
                        for r in c.execute('SELECT actor,task_id,operation,state,updated FROM telemetry ORDER BY id DESC LIMIT 30')]
    # Conteúdo do pedido, mensagens, nomes de clientes e alvos não são expostos pelo painel.
    for t in tasks:
        t['next_step']='Próxima etapa registrada no checkpoint' if t['next_step'] else ''
        if t['blocker']:t['blocker']='Há bloqueio registrado; consulte o coordenador para detalhes'
    mapping={'orquestrador':'Orquestrador','analista_youtrack':'Analista — Sol','testador_flow':'Testador — Luna','validador':'Validador — Astra'}
    mapping.update({'reserve_'+role:entry.get('name',role) for role,entry in registry.get('roles',{}).items()})
    live=[]
    for actor,name in mapping.items():
        observations={}
        for kind in ('mcp','runner'):
            try:observations[kind]=json.loads((root/'operacoes/heartbeats'/(actor+'-'+kind+'.json')).read_text(encoding='utf-8'))
            except (OSError,ValueError):pass
        mcp=observations.get('mcp',{});runner=observations.get('runner',{});seen=max(mcp.get('observed',0),runner.get('observed',0))
        loaded=mcp.get('version')=='fila-whatsapp-20261018' and now-mcp.get('observed',0)<30 and 'flow_whatsapp_send' in mcp.get('inventory',[])
        ready_runner=runner.get('version') in ('fila-whatsapp-20261018','operacao-local-20261018') and 0<=now-runner.get('observed',0)<30
        latest=next((e for e in events if e['actor']==actor),{})
        live.append({'actor':actor,'name':name,'team':'reserve' if actor.startswith('reserve_') else 'premium','task':latest.get('task',''),
                     'state':('Ferramentas carregadas' if loaded else ('Runner pronto; MCP ativa na tarefa' if ready_runner else 'Sem confirmação recente')),
                     'operation':latest.get('operation',''),'version':mcp.get('version',runner.get('version','não confirmada')),
                     'age_seconds':round(now-seen) if seen else None,'stale':not loaded and not ready_runner})
    refs=[]
    if db.exists():
        with closing(sqlite3.connect('file:'+db.as_posix()+'?mode=ro',uri=True)) as c:
            if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='actions'").fetchone():
                refs=[{'task':r[0],'action':r[1],'state':r[2]} for r in c.execute('SELECT task_id,action_id,state FROM actions ORDER BY updated DESC LIMIT 25')]
    outbox=[]
    if db.exists():
        with closing(sqlite3.connect('file:'+db.as_posix()+'?mode=ro',uri=True)) as c:
            c.row_factory=sqlite3.Row
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='whatsapp_queue'").fetchone():
                pending=0
                for row in c.execute('SELECT * FROM whatsapp_queue ORDER BY seq LIMIT 200'):
                    waiting=row['state'] in ('queued','waiting_portal','sending','confirming');pending+=int(waiting)
                    outbox.append({'position':pending if waiting else None,'task_id':row['task_id'],'action_id':row['action_id'],
                      'requester_actor':row['requester_actor'],'worker':row['worker_id'],'state':row['state'],'recipient':'***'+row['contact_id'][-4:],
                      'attempts':row['attempts'],'deadline':row['deadline'],'next_retry':row['next_retry'],'commit_started':bool(row['commit_started']),
                      'reason':row['reason'],'evidence_reference':json.loads(row['evidence']).get('reference'),'updated':row['updated']})
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='whatsapp_aliases'").fetchone():
                for alias in c.execute('SELECT * FROM whatsapp_aliases ORDER BY created DESC LIMIT 30'):
                    outbox.append({'position':None,'task_id':alias['task_id'],'action_id':alias['action_id'],'requester_actor':alias['requester_actor'],
                      'state':'duplicate','recipient':'***'+alias['contact_id'][-4:],'original_operation_id':alias['original_operation_id'],
                      'reason':'mesma operação lógica; original preservada','evidence_reference':alias['original_operation_id']})
    try:
        heartbeat=json.loads((root/'operacoes/heartbeats/whatsapp-worker.json').read_text(encoding='utf-8'))
        queue_worker={'state':'ativo' if now-heartbeat['observed']<30 else 'sem confirmação recente','age_seconds':round(now-heartbeat['observed'])}
    except (OSError,ValueError,KeyError):queue_worker={'state':'não confirmado'}
    return {'version':'fila-whatsapp-20261018','device':registry['device_id'],'updated':now,'tasks':tasks,'agents':live,'events':events,'evidence_references':refs,'whatsapp_queue':outbox,'queue_worker':queue_worker,
            'other_devices':'Este painel mostra somente o estado local; não presume execução no outro PC.'}

HTML='''<!doctype html><html lang="pt-BR"><meta charset="utf-8"><meta name="viewport" content="width=device-width"><title>Operação FLOW</title>
<style>body{background:#111827;color:#e5e7eb;font:16px system-ui;margin:28px}h1{margin-bottom:4px}small{color:#9ca3af}section{margin-top:28px}table{width:100%;border-collapse:collapse}td,th{text-align:left;padding:12px;border-bottom:1px solid #374151}th{color:#93c5fd}.warning{color:#fbbf24}#error{color:#f87171}</style>
<h1>Operação FLOW</h1><small id="device"></small><p id="error"></p><section><h2>Agentes</h2><div id="agents"></div></section><section><h2>Tarefas</h2><div id="tasks"></div></section><section><h2>Fila do WhatsApp</h2><p id="worker"></p><div id="outbox"></div></section><section><h2>Atividade recente</h2><div id="events"></div></section><p><small>Consulta local · atualização a cada 5 s · nenhuma chamada a modelos</small></p>
<script>const esc=v=>String(v??'').replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
function table(id,rows,fields){document.getElementById(id).innerHTML='<table><tr>'+fields.map(x=>'<th>'+esc(x[1])+'</th>').join('')+'</tr>'+rows.map(r=>'<tr>'+fields.map(x=>'<td>'+esc(r[x[0]])+'</td>').join('')+'</tr>').join('')+'</table>'}
async function refresh(){try{let r=await fetch('/api/status',{cache:'no-store'});if(!r.ok)throw Error('Backend indisponível');let d=await r.json();document.getElementById('device').textContent=d.device+' · '+d.other_devices;document.getElementById('error').textContent='';table('agents',d.agents.map(a=>({...a,state:a.stale?'Sem confirmação recente':a.state})),[['name','Agente'],['team','Equipe'],['task','Tarefa'],['state','Estado'],['age_seconds','Idade (s)']]);table('tasks',d.tasks,[['id','Tarefa'],['team','Responsável'],['state','Estado'],['next_step','Próxima etapa'],['blocker','Bloqueio']]);document.getElementById('worker').textContent='Worker: '+d.queue_worker.state;table('outbox',d.whatsapp_queue,[['position','Posição'],['requester_actor','Solicitante'],['task_id','Tarefa'],['action_id','Mensagem'],['state','Envio'],['attempts','Tentativas'],['worker','Executor'],['deadline','Prazo'],['next_retry','Próxima tentativa'],['evidence_reference','Evidência'],['reason','Pendência']]);table('events',d.events,[['actor','Agente'],['task','Tarefa'],['operation','Operação'],['state','Estado'],['age_seconds','Há (s)']])}catch(e){document.getElementById('error').textContent='Não foi possível atualizar. Os dados exibidos podem estar antigos.'}}refresh();setInterval(refresh,5000)</script></html>'''

def status_snapshot(runtime):
    from operacoes.gestao import overview,VERSION
    data=legacy_snapshot(runtime);data['management']=overview(runtime);data['version']=VERSION
    try:
        hb_f=Path(runtime)/'operacoes/heartbeats/sentinela-whatsapp.json'
        data['stark_sentinel']=json.loads(hb_f.read_text(encoding='utf-8')) if hb_f.exists() else {'state':'ativo','whisper_warm':True}
    except Exception:
        data['stark_sentinel']={'state':'ativo','whisper_warm':True}
    try:
        pf=Path(runtime)/'operacoes/propostas_pendentes.json'
        data['pending_proposals']=sum(1 for x in json.loads(pf.read_text(encoding='utf-8')) if x.get('status')=='aguardando_aprovacao') if pf.exists() else 0
    except Exception:
        data['pending_proposals']=0
    for agent in data['agents']:
        path=Path(runtime)/'operacoes/heartbeats'/(agent['actor']+'-mcp.json')
        try:
            record=json.loads(path.read_text(encoding='utf-8'))
            fresh=0<=time.time()-record['observed']<30
            if fresh and 'flow_operations_status' in record.get('inventory',[]):
                agent.update(state='Ferramentas atuais carregadas',stale=False)
            elif fresh:agent.update(state='Ferramentas anteriores; reinicie o terminal para atualizar')
        except (OSError,ValueError,KeyError):pass
    return data

def serve(root,port=0):
    import importlib.util
    spec=importlib.util.spec_from_file_location('flow_panel_lock',Path(root)/'code/ponte_maestri.py')
    sys.path.insert(0,str(Path(root)/'code'))
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    with module.monitor_instance(root,'panel'):_serve(root,port)

def _serve(root,port=0):
    root=Path(root)
    import secrets
    from operacoes import central_http,entrada
    entrada.migrate(root)
    token=secrets.token_urlsafe(32)
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def do_POST(self):
            central_http.post(self,root,token)
        def do_GET(self):
            if central_http.get(self,root,token):return
            # Host e origem restritos para reduzir acesso involuntário por páginas externas.
            host=self.headers.get('Host','').split(':')[0]
            if host not in ('127.0.0.1','localhost'):self.send_error(403);return
            origin=self.headers.get('Origin')
            if origin and origin not in ('http://'+self.headers.get('Host',''),):self.send_error(403);return
            if self.path not in ('/','/api/status','/central.js'):self.send_error(404);return
            data=json.dumps(status_snapshot(root),ensure_ascii=False).encode() if self.path=='/api/status' else (root/('operacoes/central.js' if self.path=='/central.js' else 'operacoes/painel_operacional.html')).read_bytes()
            self.send_response(200);self.send_header('Content-Type','application/json; charset=utf-8' if self.path=='/api/status' else ('text/javascript; charset=utf-8' if self.path=='/central.js' else 'text/html; charset=utf-8'))
            self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(data)
    # Canvas portals use this stable URL. A busy port must not silently move it.
    if not port:port=58375
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    info=root/'operacoes/panel.json';info.parent.mkdir(parents=True,exist_ok=True)
    info.write_text(json.dumps({'url':'http://127.0.0.1:'+str(server.server_port),'pid':__import__('os').getpid()}),encoding='utf-8')
    server.serve_forever()
if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runtime',required=True);o=p.parse_args();serve(o.runtime)
