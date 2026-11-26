"""Offline manifest delivery planning for finalized admission decisions."""
from .store import one


def admitted_request(db, member):
    return one(db, """SELECT r.tenant,r.uid,v.final_hash
        FROM admission_requests r JOIN admission_verdicts v ON v.request_id=r.id
        WHERE r.id=? AND v.decision='allowed' AND v.final_hash=?""",
        (member['request'],member['digest']))


def run(db, fixture):
    """Make a bounded reservation plan. Each manifest is indivisible in v1."""
    channels = {row['id']:row['slots'] for row in fixture['channels']}
    result = {'assignments': [], 'deferred': []}
    seen = set()
    for job in fixture['manifests']:
        receipt = admitted_request(db, job)
        channel = job['channel']
        slots = job['slots']
        identity = (receipt['tenant'],receipt['uid']) if receipt else None
        if receipt is None:
            reason = 'admission_unproven'
        elif identity in seen:
            reason = 'identity_already_reserved'
        elif channel not in channels or slots < 1:
            reason = 'invalid_reservation'
        elif channels[channel] < slots:
            reason = 'delivery_capacity'
        else:
            channels[channel] -= slots
            seen.add(identity)
            result['assignments'].append({'request':job['request'],'digest':receipt['final_hash'],'channel':channel,'slots':slots})
            continue
        result['deferred'].append({'request':job['request'],'reason':reason})
    return result
