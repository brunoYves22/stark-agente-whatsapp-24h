"""Retomada explícita, com mesmos identificadores e conservação de resultados."""
import importlib.util
import json
import subprocess
import time
from pathlib import Path
from operacoes.gestao import database,require_task,observations,table_exists

def state_module(root):
    spec=importlib.util.spec_from_file_location('flow_local_recovery_estado',Path(root)/'code/estado.py')
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

def model_survives(root,role):
    reg=json.loads((Path(root)/'registry.json').read_text(encoding='utf-8-sig'))
    directory=reg['roles'][role]['directory'].replace('\\','/').replace("'","''")
    script="@(Get-CimInstance Win32_Process | Where-Object { $_.Name -eq 'opencode.exe' -and $_.CommandLine.Replace('\\','/').Contains('"+directory+"') }).Count"
    try:
        result=subprocess.run(['powershell.exe','-NoProfile','-Command',script],capture_output=True,text=True,timeout=15,
                              creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return result.returncode!=0 or not result.stdout.strip().isdigit() or int(result.stdout.strip())>0
    except (OSError,subprocess.TimeoutExpired):return True

def inspect(root,actor,task_id):
    task=require_task(root,actor,task_id);reasons=[];module=state_module(root)
    pass
    if task.get('phase')=='completed':reasons.append('completed_immutable')
    if task.get('owner')!='reserve':reasons.append('premium_requires_own_session')
    if task.get('sending_paused'):reasons.append('human_pause_preserved')
    if not task.get('plan') or not task.get('next_step') or not task.get('acceptance'):reasons.append('checkpoint_incomplete')
    if any(a.get('state') in ('unknown','in_progress') for a in task.get('external_actions',[])):reasons.append('external_reconciliation_required')
    obs=observations(root).get('quota_free',{})
    if obs.get('stale',True) or obs.get('state')!='available':reasons.append('free_quota_unavailable_or_unknown')
    with database(root) as c:
        jobs=[dict(j) for j in c.execute('SELECT * FROM jobs WHERE task_id=?',(task_id,))]
        if table_exists(c,'actions') and c.execute("SELECT 1 FROM actions WHERE task_id=? AND state IN ('unknown','in_progress')",(task_id,)).fetchone():reasons.append('external_reconciliation_required')
        if table_exists(c,'whatsapp_queue') and c.execute("SELECT 1 FROM whatsapp_queue WHERE task_id=? AND state IN ('queued','waiting_portal','sending','confirming','unknown')",(task_id,)).fetchone():reasons.append('external_queue_not_settled')
    for job in jobs:
        if job['state']=='running':reasons.append('job_still_running')
        if job['state'] in ('blocked','paused'):
            # Runner em prontidão continua vivo depois de um job bloqueado.
            # Somente o CLI do modelo e jobs running comprovam execução em curso.
            if model_survives(root,job['role']):reasons.append('model_surviving_or_unverified:'+job['role'])
    resumable=[j['id'] for j in jobs if j['state'] in ('blocked','paused')]
    if not resumable:reasons.append('no_interrupted_jobs')
    return {'task_id':task_id,'revision':task['revision'],'can_resume':not reasons,'reasons':list(dict.fromkeys(reasons)),
            'job_ids':resumable,'requires_explicit_resume':True,'confirmed_results_preserved':True}

def resume(root,actor,task_id,expected_revision):
    report=inspect(root,actor,task_id)
    if report['revision']!=expected_revision:raise ValueError('stale_revision')
    if not report['can_resume']:return {**report,'resumed':False}
    module=state_module(root)
    with module.transaction(Path(root)/'estado.sqlite') as c:
        task=module.get(c,task_id)
        if task['revision']!=expected_revision:raise ValueError('stale_revision')
        if task.get('sending_paused') or task.get('owner')!='reserve':raise ValueError('recovery_no_longer_authorized')
        if c.execute("SELECT 1 FROM jobs WHERE task_id=? AND state='running'",(task_id,)).fetchone():raise ValueError('job_still_running')
        # Mesmo ID; histórico de resultados fica no checkpoint de recuperação.
        history=[]
        for jid in report['job_ids']:
            row=c.execute('SELECT * FROM jobs WHERE id=? AND task_id=?',(jid,task_id)).fetchone()
            if not row or row['state'] not in ('blocked','paused'):raise ValueError('job_state_changed')
            history.append({'job_id':jid,'previous_state':row['state'],'result':row['result'],'at':time.time()})
        task.setdefault('recovery_history',[]).extend(history)
        task['phase']='reserve_active';task['recovered_at']=time.time()
        for jid in report['job_ids']:
            c.execute("UPDATE jobs SET state='queued',result='',updated=?,worker_pid=NULL,worker_birth=NULL WHERE id=?",(time.time(),jid))
            task.get('job_blockers',{}).pop(jid,None)
        module.put(c,task,'recovery_resumed')
    return {'task_id':task_id,'resumed':True,'same_job_ids':report['job_ids'],'revision':task['revision']}
