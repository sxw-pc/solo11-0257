from .store import act,one,many

def handle(db,x):
    op=x['op']
    if op=='propose':
        if x['mode'] not in ('reject','degrade') or not x.get('owner'):
            return {'decision':'invalid_contract'}
        latest=one(db,'SELECT MAX(revision) revision FROM contract_revisions WHERE id=?',(x['id'],))
        if latest['revision'] is not None and x['revision']<=latest['revision']:
            return {'decision':'stale_contract'}
        db.execute('INSERT INTO contract_revisions VALUES(?,?,?,?,?,?)',(x['id'],x['revision'],x['mode'],int(x['mandatory']),'draft',x['owner']))
        return {'decision':'drafted','revision':x['revision']}
    if op=='scope':
        c=one(db,'SELECT * FROM contract_revisions WHERE id=? AND revision=?',(x['id'],x['revision']))
        if not c or c['state']!='draft' or c['revision']!=x['revision'] or not x['path'].startswith('/'):
            return {'decision':'scope_denied'}
        if x['behavior'] not in ('exclusive','merge'):
            return {'decision':'invalid_scope_behavior'}
        old=one(db,'SELECT * FROM mutation_scopes WHERE contract_id=? AND revision=? AND path=?',(x['id'],x['revision'],x['path']))
        if old:return {'decision':'scope_conflict' if old['behavior']!=x['behavior'] else 'scope_reused'}
        db.execute('INSERT INTO mutation_scopes VALUES(?,?,?,?)',(x['id'],x['revision'],x['path'],x['behavior']))
        return {'decision':'scope_recorded'}
    if op=='approve':
        c=one(db,'SELECT * FROM contract_revisions WHERE id=? AND revision=?',(x['id'],x['revision']))
        if not c or c['state']!='draft' or c['revision']!=x['revision'] or x['role'] not in ('security','owner') or x['expires']<=x['at']:
            return {'decision':'approval_denied'}
        if x['role']=='owner' and x['actor']!=c['owner']:
            return {'decision':'wrong_owner'}
        old=one(db,'SELECT * FROM contract_approvals WHERE contract_id=? AND revision=? AND role=?',(x['id'],x['revision'],x['role']))
        if old:return {'decision':'approval_conflict'}
        db.execute('INSERT INTO contract_approvals VALUES(?,?,?,?,?)',(x['id'],x['revision'],x['role'],x['actor'],x['expires']))
        return {'decision':'approval_recorded'}
    if op=='activate':
        c=one(db,'SELECT * FROM contract_revisions WHERE id=? AND revision=?',(x['id'],x['revision']))
        approvals=many(db,'SELECT * FROM contract_approvals WHERE contract_id=? AND revision=? AND expires>=?',(x['id'],x['revision'],x['at']))
        if not c or c['state']!='draft' or c['revision']!=x['revision'] or {a['role'] for a in approvals}!={'security','owner'} or len({a['actor'] for a in approvals})<2:
            return {'decision':'activation_denied'}
        db.execute("UPDATE contract_revisions SET state='superseded' WHERE id=? AND state='active'",(x['id'],))
        db.execute("UPDATE contract_revisions SET state='active' WHERE id=? AND revision=?",(x['id'],x['revision']))
        db.execute('INSERT INTO contracts VALUES(?,?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET revision=excluded.revision,mode=excluded.mode,mandatory=excluded.mandatory,state=excluded.state,owner=excluded.owner',(x['id'],x['revision'],c['mode'],c['mandatory'],'active',c['owner']))
        db.execute("UPDATE admission_requests SET state='stale' WHERE state='pending'")
        db.execute("UPDATE waiver_requests SET state='stale' WHERE contract_id=? AND contract_revision<? AND state='active'",(x['id'],x['revision']))
        return {'decision':'activated'}
    if op=='withdraw':
        c=one(db,'SELECT * FROM contracts WHERE id=?',(x['id'],))
        if not c or c['revision']!=x['revision'] or c['state']!='active':return {'decision':'withdraw_denied'}
        db.execute("UPDATE contracts SET state='withdrawn' WHERE id=?",(x['id'],))
        db.execute("UPDATE contract_revisions SET state='withdrawn' WHERE id=? AND revision=?",(x['id'],x['revision']))
        db.execute("UPDATE waiver_requests SET state='stale' WHERE contract_id=? AND state='active'",(x['id'],))
        db.execute("UPDATE admission_requests SET state='stale' WHERE state='pending' AND contract_snapshot LIKE ?",('%"'+x['id']+'"%',))
        return {'decision':'withdrawn'}
    return {'decision':'unknown_operation'}

def run(db,x):return act(db,'contract',x,handle)
