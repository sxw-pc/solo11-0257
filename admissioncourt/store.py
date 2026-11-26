import json
import sqlite3
from contextlib import contextmanager

SCHEMA = '''
CREATE TABLE IF NOT EXISTS contracts(id TEXT PRIMARY KEY, revision INTEGER, mode TEXT, mandatory INTEGER, state TEXT, owner TEXT);
CREATE TABLE IF NOT EXISTS contract_revisions(id TEXT,revision INTEGER,mode TEXT,mandatory INTEGER,state TEXT,owner TEXT,PRIMARY KEY(id,revision));
CREATE TABLE IF NOT EXISTS contract_approvals(contract_id TEXT, revision INTEGER, role TEXT, actor TEXT, expires INTEGER, PRIMARY KEY(contract_id,revision,role));
CREATE TABLE IF NOT EXISTS mutation_scopes(contract_id TEXT, revision INTEGER, path TEXT, behavior TEXT, PRIMARY KEY(contract_id,revision,path));
CREATE TABLE IF NOT EXISTS waiver_requests(id TEXT PRIMARY KEY, tenant TEXT, uid TEXT, path TEXT, contract_id TEXT, contract_revision INTEGER, expires INTEGER, state TEXT);
CREATE TABLE IF NOT EXISTS waiver_approvals(waiver_id TEXT, role TEXT, actor TEXT, expires INTEGER, PRIMARY KEY(waiver_id,role));
CREATE TABLE IF NOT EXISTS admission_requests(id TEXT PRIMARY KEY,tenant TEXT,uid TEXT,body_hash TEXT,at INTEGER,state TEXT,contract_snapshot TEXT,final_hash TEXT);
CREATE TABLE IF NOT EXISTS webhook_calls(id TEXT PRIMARY KEY,request_id TEXT,contract_id TEXT,revision INTEGER,outcome TEXT,patch_path TEXT,patch_value TEXT,at INTEGER);
CREATE TABLE IF NOT EXISTS admission_verdicts(request_id TEXT PRIMARY KEY,decision TEXT,reason TEXT,used_waiver TEXT,final_hash TEXT);
CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY AUTOINCREMENT,subsystem TEXT,event_id TEXT UNIQUE,payload TEXT,result TEXT);
'''

def connect(path):
    db=sqlite3.connect(path,isolation_level=None)
    db.row_factory=sqlite3.Row
    db.execute('PRAGMA busy_timeout=5000')
    db.executescript(SCHEMA)
    return db

def one(db,sql,args=()):
    row=db.execute(sql,args).fetchone()
    return dict(row) if row else None

def many(db,sql,args=()):
    return [dict(row) for row in db.execute(sql,args)]

@contextmanager
def transaction(db):
    db.execute('BEGIN IMMEDIATE')
    try:
        yield
        db.execute('COMMIT')
    except Exception:
        db.execute('ROLLBACK')
        raise

def act(db,subsystem,x,handler):
    event=x['event_id']
    body=json.dumps(x,ensure_ascii=False,sort_keys=True)
    with transaction(db):
        old=one(db,'SELECT * FROM events WHERE event_id=?',(event,))
        if old:
            if old['payload']!=body: return {'decision':'event_identity_conflict'}
            return json.loads(old['result'])
        result=handler(db,x)
        db.execute('INSERT INTO events(subsystem,event_id,payload,result) VALUES(?,?,?,?)',(subsystem,event,body,json.dumps(result,ensure_ascii=False,sort_keys=True)))
        return result

TABLES=['contracts','contract_revisions','contract_approvals','mutation_scopes','waiver_requests','waiver_approvals','admission_requests','webhook_calls','admission_verdicts']
def replay(path):
    from . import contract,waiver,admit
    source=connect(path); target=connect(':memory:')
    handlers={'contract':contract.run,'waiver':waiver.run,'admit':admit.run}
    errors=[]; events=many(source,'SELECT * FROM events ORDER BY seq')
    for i,e in enumerate(events,1):
        got=handlers[e['subsystem']](target,json.loads(e['payload']))
        if got!=json.loads(e['result']): errors.append({'event':i,'actual':got})
    for table in TABLES:
        a=[tuple(row) for row in source.execute('SELECT * FROM '+table+' ORDER BY rowid')]
        b=[tuple(row) for row in target.execute('SELECT * FROM '+table+' ORDER BY rowid')]
        if a!=b: errors.append({'table':table})
    source.close();target.close()
    return {'events':len(events),'verified':not errors,'differences':errors}
