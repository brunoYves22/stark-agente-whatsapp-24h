"""Recuperação limitada a falhas comprovadas anteriores ao início do modelo."""
import json
import time
from pathlib import Path
from operacoes.gestao import database, observations, table_exists

MAX_RETRIES = 3
RETRY_CODES = {'quota_unknown', 'quota_exhausted'}


def reconcile_finished(root, survives=None):
    """Reconcilia reserva sem executor; preserva ações, jobs e resultados, sem replay."""
    from operacoes.recuperacao import model_survives, state_module
    from operacoes.entrada import has_uncertain_effect
    module=state_module(root);survives=survives or model_survives;now=time.time();changed=[];alive={}
    with database(root) as c:
        rows=[dict(r) for r in c.execute("SELECT task_id,payload,updated FROM tasks WHERE json_extract(payload,'$.owner')='reserve' AND json_extract(payload,'$.phase') IN ('reserve_active','review')")]
    for row in rows:
        tid=row['task_id']
        with database(root) as c:
            jobs=[dict(j) for j in c.execute('SELECT * FROM jobs WHERE task_id=?',(tid,))]
            if not jobs or any(j['state'] not in ('blocked','done','cancelled') for j in jobs):continue
            last=max([row['updated']]+[j['updated'] for j in jobs])
            if now-last<300:continue
            # Recuperação limitada de preflight continua sob sua política existente.
            if table_exists(c,'central_recovery_policy'):
                policy=c.execute('SELECT enabled FROM central_recovery_policy WHERE task_id=?',(tid,)).fetchone()
                if policy and policy['enabled'] and any(diagnostic(root,j) for j in jobs if j['state']=='blocked'):continue
        for role in {j['role'] for j in jobs}:
            if role not in alive:alive[role]=survives(root,role)
        if any(alive[j['role']] for j in jobs):continue
        with database(root,True) as c:
            task=module.get(c,tid)
            fresh=[dict(j) for j in c.execute('SELECT * FROM jobs WHERE task_id=?',(tid,))]
            if (task.get('owner')!='reserve' or task.get('phase') not in ('reserve_active','review')
                    or task.get('sending_paused') or task.get('revision')!=json.loads(row['payload'])['revision']
                    or [(j['id'],j['state'],j['updated']) for j in fresh]!=[(j['id'],j['state'],j['updated']) for j in jobs]):continue
            if has_uncertain_effect(c,tid):continue
            if any(a.get('state') in ('unknown','in_progress') for a in task.get('external_actions',[])):continue
            if table_exists(c,'whatsapp_queue') and c.execute("SELECT 1 FROM whatsapp_queue WHERE task_id=? AND state IN ('queued','waiting_portal','sending','confirming','unknown')",(tid,)).fetchone():continue
            task['phase']='blocked'
            task.setdefault('blockers',[]).append('Executor encerrado sem conclusão da tarefa; resultados preservados. Retomada exige conferir o checkpoint, sem repetir efeitos confirmados.')
            task.setdefault('recovery_history',[]).append({'at':now,'reason':'executor_finished_without_task_completion','previous_phase':json.loads(row['payload'])['phase']})
            module.validate_capsule(task);module.put(c,task,'executor_finished_reconciled');changed.append(tid)
    return changed


def expire_waiting(root):
    """Pedido nunca entregue há mais de um dia exige revisão, não execução tardia."""
    with database(root,True) as c:
        c.execute("UPDATE central_requests SET state='needs_review',reason=?,revision=revision+1,updated=? WHERE state IN ('registered','waiting') AND created<?",
                  ('Pedido antigo ainda não encaminhado. Revise o conteúdo e solicite novamente se continuar válido; nenhuma execução automática tardia.',time.time(),time.time()-86400))


def migrate(root):
    with database(root, True) as c:
        c.execute('''CREATE TABLE IF NOT EXISTS central_recovery(
            job_id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempts INTEGER NOT NULL,
            next_retry REAL NOT NULL, state TEXT NOT NULL, code TEXT NOT NULL, updated REAL NOT NULL)''')
        c.execute('''CREATE TABLE IF NOT EXISTS central_recovery_policy(
            task_id TEXT PRIMARY KEY, enabled INTEGER NOT NULL, created REAL NOT NULL)''')


def diagnostic(root, job):
    try:
        data = json.loads((Path(root)/'operacoes/runner-diagnostics'/(job['id']+'.json')).read_text(encoding='utf-8'))
        if (data.get('job_id') == job['id'] and data.get('task_id') == job['task_id']
                and data.get('stage') == 'preflight' and data.get('inference_started') is False
                and data.get('retry_code') in RETRY_CODES
                and abs(data.get('updated', 0)-job['updated']) < 30):
            return data
    except (OSError, ValueError, TypeError):
        pass
    return None


def recover(root):
    """Não executa modelo/portal; recoloca o mesmo job na fila, no máximo 3 vezes."""
    from operacoes.entrada import has_uncertain_effect
    from operacoes.recuperacao import model_survives, state_module
    quota = observations(root).get('quota_free', {})
    now = time.time()
    with database(root) as c:
        candidates = [dict(j) for j in c.execute("""SELECT j.* FROM jobs j JOIN tasks t ON t.task_id=j.task_id
            WHERE j.state='blocked'
            AND json_extract(t.payload,'$.owner')='reserve'
            AND json_extract(t.payload,'$.phase') IN ('reserve_active','review')
            AND EXISTS (SELECT 1 FROM central_recovery_policy p WHERE p.task_id=j.task_id AND p.enabled=1)
            ORDER BY j.updated DESC LIMIT 200""")]
    for job in candidates:
        data = diagnostic(root, job)
        if not data:
            continue
        with database(root, True) as c:
            row = c.execute('SELECT payload FROM tasks WHERE task_id=?', (job['task_id'],)).fetchone()
            task = json.loads(row[0]) if row else {}
            policy=c.execute('SELECT enabled FROM central_recovery_policy WHERE task_id=?',(job['task_id'],)).fetchone()
            if (not policy or not policy['enabled'] or task.get('owner') != 'reserve'
                    or task.get('phase') not in ('reserve_active', 'review') or task.get('sending_paused')):
                continue
            c.execute('''INSERT OR IGNORE INTO central_recovery VALUES(?,?,0,?,'waiting',?,?)''',
                      (job['id'], job['task_id'], now+60, data['retry_code'], now))
            retry = dict(c.execute('SELECT * FROM central_recovery WHERE job_id=?', (job['id'],)).fetchone())
            if retry['attempts'] >= MAX_RETRIES:
                c.execute("UPDATE central_recovery SET state='needs_review' WHERE job_id=?", (job['id'],))
                continue
            c.execute("UPDATE central_recovery SET state='waiting',code=? WHERE job_id=?", (data['retry_code'],job['id']))
        if retry['next_retry'] > now or quota.get('stale', True) or quota.get('state') != 'available':
            continue
        if model_survives(root, job['role']):
            continue
        module = state_module(root)
        with database(root, True) as c:
            current = c.execute('SELECT * FROM jobs WHERE id=?', (job['id'],)).fetchone()
            task = module.get(c, job['task_id'])
            retry = dict(c.execute('SELECT * FROM central_recovery WHERE job_id=?', (job['id'],)).fetchone())
            policy=c.execute('SELECT enabled FROM central_recovery_policy WHERE task_id=?',(job['task_id'],)).fetchone()
            if (not current or current['state'] != 'blocked' or current['updated'] != job['updated']
                    or retry['attempts'] >= MAX_RETRIES or retry['next_retry'] > time.time()
                    or task.get('owner') != 'reserve' or task.get('phase') not in ('reserve_active', 'review')
                    or not policy or not policy['enabled'] or task.get('sending_paused')):
                continue
            if c.execute("SELECT 1 FROM jobs WHERE role=? AND state='running'", (job['role'],)).fetchone():
                continue
            if has_uncertain_effect(c, job['task_id']):
                continue
            if any(a.get('state') in ('unknown','in_progress') for a in task.get('external_actions', [])):
                continue
            if table_exists(c, 'whatsapp_queue') and c.execute("SELECT 1 FROM whatsapp_queue WHERE task_id=? AND state IN ('queued','waiting_portal','sending','confirming','unknown')", (job['task_id'],)).fetchone():
                continue
            task.setdefault('recovery_history', []).append({
                'job_id': job['id'], 'previous_state': 'blocked', 'result': current['result'],
                'at': time.time(), 'reason': data['retry_code'], 'before_inference': True})
            task.get('job_blockers', {}).pop(job['id'], None)
            c.execute("UPDATE jobs SET state='queued',worker_pid=NULL,worker_birth=NULL,updated=? WHERE id=?", (time.time(), job['id']))
            attempt = retry['attempts']+1
            c.execute("UPDATE central_recovery SET attempts=?,next_retry=?,state='requeued',code=?,updated=? WHERE job_id=?",
                      (attempt, time.time()+60*(2**attempt), data['retry_code'], time.time(), job['id']))
            module.put(c, task, 'preflight_recovery_queued')


def status(c, task_id):
    if not table_exists(c, 'central_recovery'):
        return []
    rows=[dict(r) for r in c.execute('''SELECT r.job_id,r.attempts,r.next_retry,r.state,r.code,r.updated,j.state AS job_state
        FROM central_recovery r JOIN jobs j ON j.id=r.job_id WHERE r.task_id=?''',(task_id,))]
    for row in rows:
        if row['job_state'] in ('running','done','cancelled','paused','awaiting_operations','awaiting_delegations'):
            row['state']=row['job_state']
    return rows
