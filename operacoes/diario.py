"""Diário idempotente independente dos prompts de modelo."""
import json,sqlite3,time
from pathlib import Path
from contextlib import contextmanager

@contextmanager
def connection(db):
    Path(db).parent.mkdir(parents=True,exist_ok=True)
    c=sqlite3.connect(str(db),timeout=15,isolation_level=None);c.row_factory=sqlite3.Row
    try:
        c.execute('PRAGMA journal_mode=WAL');c.execute('BEGIN IMMEDIATE')
        c.execute('CREATE TABLE IF NOT EXISTS actions(task_id TEXT,action_id TEXT,actor TEXT,operation TEXT,target TEXT,digest TEXT,state TEXT,evidence TEXT,updated REAL,PRIMARY KEY(task_id,action_id))')
        if 'dedupe' not in {r[1] for r in c.execute('PRAGMA table_info(actions)')}:c.execute('ALTER TABLE actions ADD COLUMN dedupe TEXT')
        c.execute('CREATE UNIQUE INDEX IF NOT EXISTS actions_dedupe ON actions(dedupe) WHERE dedupe IS NOT NULL')
        c.execute('CREATE TABLE IF NOT EXISTS telemetry(id INTEGER PRIMARY KEY,actor TEXT,task_id TEXT,operation TEXT,state TEXT,detail TEXT,updated REAL)')
        yield c;c.commit()
    except BaseException:c.rollback();raise
    finally:c.close()


def begin_action(db,task_id,action_id,actor,operation,target,digest,dedupe=None):
    if not action_id or len(action_id)>120:raise ValueError('action_id_invalid')
    with connection(db) as c:
        if c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='tasks'").fetchone():
            row=c.execute('SELECT payload FROM tasks WHERE task_id=?',(task_id,)).fetchone()
            if not row:raise ValueError('task_missing')
            task=json.loads(row[0])
            if task.get('owner')!=('reserve' if actor.startswith('reserve_') else 'premium') or task.get('phase') not in ('premium_active','reserve_active','review'):
                raise ValueError('owner_changed_before_effect')
        if c.execute('SELECT 1 FROM actions WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone():
            raise ValueError('action_exists: consultar flow_action_status, não repetir')
        if dedupe and c.execute('SELECT 1 FROM actions WHERE dedupe=?',(dedupe,)).fetchone():raise ValueError('duplicate_presentation: consultar histórico, não reenviar')
        c.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?,?,?)',(task_id,action_id,actor,operation,json.dumps(target),digest,'in_progress','{}',time.time(),dedupe))
    return {'state':'in_progress','action_id':action_id}

def finish_action(db,task_id,action_id,state,evidence):
    if state not in ('confirmed','unknown'):raise ValueError('invalid_action_state')
    with connection(db) as c:
        row=c.execute('SELECT state FROM actions WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        if not row:raise ValueError('action_missing')
        if row['state']=='confirmed':raise ValueError('confirmed_immutable')
        c.execute('UPDATE actions SET state=?,evidence=?,updated=? WHERE task_id=? AND action_id=?',(state,json.dumps(evidence,ensure_ascii=False),time.time(),task_id,action_id))
    return {'state':state,'action_id':action_id,'evidence':evidence}

def action_status(db,task_id,action_id):
    with connection(db) as c:
        row=c.execute('SELECT * FROM actions WHERE task_id=? AND action_id=?',(task_id,action_id)).fetchone()
        return dict(row) if row else {'state':'not_found'}

def event(db,actor,task_id,operation,state,detail=''):
    with connection(db) as c:
        c.execute('INSERT INTO telemetry(actor,task_id,operation,state,detail,updated) VALUES(?,?,?,?,?,?)',(actor,task_id,operation,state,detail[:300],time.time()))
        c.execute('CREATE TABLE IF NOT EXISTS ops_progress(task_id TEXT,actor TEXT,operation TEXT,state TEXT,updated REAL,PRIMARY KEY(task_id,actor))')
        c.execute('INSERT INTO ops_progress VALUES(?,?,?,?,?) ON CONFLICT(task_id,actor) DO UPDATE SET operation=excluded.operation,state=excluded.state,updated=excluded.updated',
                  (task_id,actor,operation,state,time.time()))
