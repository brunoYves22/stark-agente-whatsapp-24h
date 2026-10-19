"""Heartbeat leve dos processos; não consulta modelos."""
import json,os,threading,time
from pathlib import Path
VERSION='operacao-local-20261018'

def start_heartbeat(root,actor,kind='mcp',inventory=None):
    stop=threading.Event();folder=Path(root)/'operacoes/heartbeats';folder.mkdir(parents=True,exist_ok=True)
    def update():
        while not stop.is_set():
            path=folder/(actor+'-'+kind+'.json');temp=path.with_name(path.name+'.'+str(os.getpid())+'.tmp')
            try:
                temp.write_text(json.dumps({'actor':actor,'kind':kind,'version':VERSION,'pid':os.getpid(),
                                           'inventory':inventory or [],'observed':time.time()}),encoding='utf-8');os.replace(temp,path)
            except OSError:pass
            stop.wait(10)
    threading.Thread(target=update,daemon=True).start();return stop

def panel_supervisor(root,stop):
    import subprocess,sys,urllib.request,urllib.parse
    root=Path(root)
    while not stop.is_set():
        healthy=False
        try:
            info=json.loads((root/'operacoes/panel.json').read_text(encoding='utf-8'));url=info['url'];parsed=urllib.parse.urlsplit(url)
            if parsed.hostname=='127.0.0.1' and parsed.scheme=='http':
                with urllib.request.urlopen(url+'/api/status',timeout=2) as response:healthy=json.load(response).get('version')==VERSION
        except Exception:pass
        if not healthy:
            python=Path(sys.executable).with_name('pythonw.exe')
            subprocess.Popen([str(python if python.exists() else sys.executable),'-X','utf8',str(root/'operacoes/painel.py'),'--runtime',str(root)],
                             stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        stop.wait(20)
