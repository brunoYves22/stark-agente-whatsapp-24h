"""Supervisor local de consulta e memória. Não executa filas nem modelos."""
import argparse
import json
import sys
import threading
import time
from pathlib import Path
if __package__ in (None,''):sys.path.insert(0,str(Path(__file__).resolve().parent.parent))

def run(root):
    root=Path(root);sys.path.insert(0,str(root/'code'));sys.path.insert(0,str(root))
    from ponte_maestri import monitor_instance
    from operacoes.gestao import migrate,observe
    from operacoes.conhecimento import export
    from operacoes.diagnostico import refresh_quotas,refresh_integrations
    from operacoes.painel import serve
    from operacoes.saude import start_heartbeat
    with monitor_instance(root,'companhia'):
        from operacoes import entrada
        entrada.migrate(root)
        from operacoes.continuidade import migrate as migrate_recovery,recover
        migrate_recovery(root)
        def dispatch():
            while True:
                try:
                    entrada.cycle(root)
                    recover(root)
                except Exception as error:
                    try:observe(root,'central_dispatch',{'state':'unknown','error_type':type(error).__name__},45)
                    except Exception:pass
                time.sleep(5)
        threading.Thread(target=dispatch,daemon=True).start()
        migrate(root);start_heartbeat(root,'companhia','supervisor')
        # Panel supervisor existente pode coexistir: mesmo lock exclusivo em serve().
        def panel():
            while True:
                try:serve(root)
                except Exception:time.sleep(20)
        threading.Thread(target=panel,daemon=True).start()
        def ensure_sentinel(r_root):
            hb_file=r_root/'operacoes/heartbeats/sentinela-whatsapp.json'
            stale=True
            if hb_file.exists():
                try:
                    hb=json.loads(hb_file.read_text(encoding='utf-8'))
                    stale=(time.time()-float(hb.get('observed',0)))>45
                except Exception:
                    stale=True
            if not stale:
                return
            import socket,subprocess
            try:
                with socket.create_connection(('127.0.0.1',58377),timeout=0.5):
                    return
            except OSError:
                pass
            sentinel_script=r_root/'operacoes/sentinela_whatsapp_stark.py'
            if sentinel_script.exists():
                subprocess.Popen([sys.executable,'-X','utf8',str(sentinel_script),'--runtime',str(r_root)],
                                 cwd=str(r_root),stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                 creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0x08000000))
        ensure_sentinel(root)
        last_free=0
        while True:
            try:
                ensure_sentinel(root)
                free=time.time()-last_free>=300
                refresh_quotas(root,free)
                if free:last_free=time.time()
                refresh_integrations(root);export(root)
                observe(root,'supervisor',{'state':'available','source':'companhia','mode':'local_only'},45)
            except Exception as error:
                try:observe(root,'supervisor',{'state':'unknown','error_type':type(error).__name__},45)
                except Exception:pass
            time.sleep(20)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--runtime',required=True);run(p.parse_args().runtime)
