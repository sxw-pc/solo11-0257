"""Export a draft contract approval packet from verified local business state."""
from .store import many,one


def current_votes(db, contract, revision, now):
    return many(db,"SELECT actor,role,expires FROM contract_approvals "
        "WHERE contract_id=? AND revision=? AND expires>=? ORDER BY role", (contract,revision,now))


def run(db, fixture):
    cid,revision,now=(fixture[k] for k in ('contract','revision','at'))
    decision=one(db,'SELECT owner,mode,mandatory,state FROM contract_revisions WHERE id=? AND revision=?',(cid,revision))
    if not decision:
        return {'decision':'contract_missing'}
    approvals=current_votes(db,cid,revision,now)
    independent={row['role'] for row in approvals}=={'owner','security'} and len({row['actor'] for row in approvals})>=2
    return {'decision':'packet_ready' if independent else 'pending_approval','contract':cid,
        'revision':revision,'state':decision['state'],'votes':approvals,
        'valid_through':min((row['expires'] for row in approvals),default=None)}
