from .store import act,one,many

def handle(db,x):
    op=x['op']
    if op=='request':
        c=one(db,'SELECT * FROM contracts WHERE id=?',(x['contract'],))
        scope=one(db,'SELECT 1 FROM mutation_scopes WHERE contract_id=? AND revision=? AND path=?',(x['contract'],c['revision'],x['path'])) if c else None
        if not c or c['state']!='active' or c['mandatory'] or not scope or x['expires']<=x['at']:
            return {'decision':'waiver_request_denied'}
        old=one(db,'SELECT * FROM waiver_requests WHERE id=?',(x['id'],))
        if old:return {'decision':'request_identity_conflict'}
        db.execute('INSERT INTO waiver_requests VALUES(?,?,?,?,?,?,?,?)',(x['id'],x['tenant'],x['uid'],x['path'],c['id'],c['revision'],x['expires'],'pending'))
        return {'decision':'waiver_requested'}
    if op=='approve':
        w=one(db,'SELECT * FROM waiver_requests WHERE id=?',(x['id'],))
        if not w or w['state']!='pending' or x['role'] not in ('security','owner') or x['expires']<=x['at'] or x['expires']>w['expires']:
            return {'decision':'waiver_approval_denied'}
        old=one(db,'SELECT * FROM waiver_approvals WHERE waiver_id=? AND role=?',(x['id'],x['role']))
        if old:return {'decision':'waiver_role_conflict'}
        db.execute('INSERT INTO waiver_approvals VALUES(?,?,?,?)',(x['id'],x['role'],x['actor'],x['expires']))
        return {'decision':'waiver_approved'}
    if op=='activate':
        w=one(db,'SELECT * FROM waiver_requests WHERE id=?',(x['id'],))
        c=one(db,'SELECT * FROM contracts WHERE id=?',(w['contract_id'],)) if w else None
        approvals=many(db,'SELECT * FROM waiver_approvals WHERE waiver_id=? AND expires>=?',(x['id'],x['at']))
        if not w or w['state']!='pending' or not c or c['state']!='active' or c['revision']!=w['contract_revision'] or w['expires']<=x['at'] or {a['role'] for a in approvals}!={'security','owner'} or len({a['actor'] for a in approvals})<2:
            return {'decision':'waiver_activation_denied'}
        effective_until=min([w['expires']]+[a['expires'] for a in approvals])
        db.execute("UPDATE waiver_requests SET state='active',expires=? WHERE id=?",(effective_until,x['id']))
        return {'decision':'waiver_active','expires':effective_until}
    if op=='revoke':
        w=one(db,'SELECT * FROM waiver_requests WHERE id=?',(x['id'],))
        if not w or w['state']!='active':return {'decision':'waiver_revoke_denied'}
        db.execute("UPDATE waiver_requests SET state='revoked' WHERE id=?",(x['id'],))
        return {'decision':'waiver_revoked'}
    return {'decision':'unknown_operation'}

def run(db,x):return act(db,'waiver',x,handle)
