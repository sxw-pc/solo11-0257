import hashlib
import json
from .store import act,one,many

def hash_value(value):
    return hashlib.sha256(json.dumps(value,sort_keys=True,ensure_ascii=False).encode()).hexdigest()

def handle(db,x):
    op=x['op']
    if op=='submit':
        old=one(db,'SELECT * FROM admission_requests WHERE id=?',(x['id'],))
        digest=hash_value(x['body'])
        if old:return {'decision':'request_reused' if old['body_hash']==digest and old['uid']==x['uid'] else 'request_identity_conflict'}
        contracts=many(db,"SELECT id,revision FROM contracts WHERE state='active' ORDER BY id")
        if not contracts:return {'decision':'no_active_contract'}
        db.execute('INSERT INTO admission_requests VALUES(?,?,?,?,?,?,?,?)',(x['id'],x['tenant'],x['uid'],digest,x['at'],'pending',json.dumps({c['id']:c['revision'] for c in contracts},sort_keys=True),None))
        return {'decision':'pending','contracts':[c['id'] for c in contracts]}
    if op=='call':
        req=one(db,'SELECT * FROM admission_requests WHERE id=?',(x['request'],))
        c=one(db,'SELECT * FROM contracts WHERE id=?',(x['contract'],))
        if not req or req['state']!='pending' or not c or c['state']!='active' or json.loads(req['contract_snapshot']).get(c['id'])!=c['revision']:
            return {'decision':'call_stale'}
        if x['outcome'] not in ('allow','deny','timeout','patch'):return {'decision':'invalid_call'}
        if x['outcome'] in ('patch','deny') and not x.get('path'):
            return {'decision':'call_path_required'}
        if x['outcome']=='patch' and not one(db,'SELECT 1 FROM mutation_scopes WHERE contract_id=? AND revision=? AND path=?',(c['id'],c['revision'],x['path'])):
            return {'decision':'undeclared_patch'}
        if one(db,'SELECT 1 FROM webhook_calls WHERE request_id=? AND contract_id=?',(x['request'],x['contract'])):
            return {'decision':'duplicate_call'}
        db.execute('INSERT INTO webhook_calls VALUES(?,?,?,?,?,?,?,?)',(x['id'],x['request'],c['id'],c['revision'],x['outcome'],x.get('path'),x.get('value'),x['at']))
        return {'decision':'call_recorded'}
    if op=='decide':
        req=one(db,'SELECT * FROM admission_requests WHERE id=?',(x['id'],))
        if not req or req['state']!='pending':return {'decision':'request_not_pending'}
        snapshot=json.loads(req['contract_snapshot']); calls=many(db,'SELECT * FROM webhook_calls WHERE request_id=?',(x['id'],))
        missing=[]; failures=[]; patches=[]; used=[]
        for cid,revision in snapshot.items():
            c=one(db,'SELECT * FROM contracts WHERE id=?',(cid,)); call=next((a for a in calls if a['contract_id']==cid),None)
            if not c or c['state']!='active' or c['revision']!=revision:return {'decision':'contract_changed'}
            if not call:missing.append(cid);continue
            if call['outcome']=='timeout' and (c['mandatory'] or c['mode']=='reject'):failures.append((cid,'timeout',None))
            if call['outcome']=='deny':failures.append((cid,'deny',call['patch_path']))
            if call['outcome']=='patch':patches.append((cid,call['patch_path'],call['patch_value']))
        if missing:return {'decision':'incomplete_calls','missing':missing}
        for cid,reason,path in failures:
            if reason=='timeout':continue
            w=one(db,"SELECT * FROM waiver_requests WHERE contract_id=? AND tenant=? AND uid=? AND path=? AND state='active' AND expires>? ORDER BY id LIMIT 1",(cid,req['tenant'],req['uid'],path,x['at']))
            if w and w['contract_revision']==snapshot[cid]:used.append(w['id'])
            else:return _finish(db,req,'denied',cid+':'+reason,used,None)
        if any(reason=='timeout' for _,reason,_ in failures):return _finish(db,req,'denied','mandatory_timeout',used,None)
        paths={}
        for cid,path,value in patches:
            scope=one(db,'SELECT * FROM mutation_scopes WHERE contract_id=? AND revision=? AND path=?',(cid,snapshot[cid],path))
            if path in paths and (scope['behavior']=='exclusive' or paths[path][1]=='exclusive' or paths[path][0]!=value):
                return _finish(db,req,'denied','mutation_conflict:'+path,used,None)
            paths[path]=(value,scope['behavior'])
        digest=hash_value([req['body_hash'],sorted((path,value) for path,(value,_) in paths.items())])
        return _finish(db,req,'allowed','all_contracts_satisfied',used,digest)
    return {'decision':'unknown_operation'}

def _finish(db,req,decision,reason,used,digest):
    db.execute('INSERT INTO admission_verdicts VALUES(?,?,?,?,?)',(req['id'],decision,reason,','.join(used),digest))
    db.execute('UPDATE admission_requests SET state=?,final_hash=? WHERE id=?',(decision,digest,req['id']))
    return {'decision':decision,'reason':reason,'used_waivers':used,'final_hash':digest}

def run(db,x):return act(db,'admit',x,handle)
