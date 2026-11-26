"""Apply synthetic dispatch reservations to an offline change receiver."""
import json
from . import dispatch
from .store import one,transaction

SCHEMA="""CREATE TABLE IF NOT EXISTS receiver_receipts(
    delivery TEXT PRIMARY KEY, channel TEXT NOT NULL, request TEXT NOT NULL,
    digest TEXT NOT NULL, body TEXT NOT NULL);"""


def apply(db, envelope, reservations):
    found=next((x for x in reservations if x['request']==envelope['request'] and x['channel']==envelope['channel']),None)
    if found is None or found['digest']!=envelope['digest']:
        return {'decision':'reservation_missing'}
    ident=envelope['delivery']
    body=json.dumps(envelope,sort_keys=True,separators=(',',':'))
    with transaction(db):
        old=one(db,'SELECT body FROM receiver_receipts WHERE delivery=?',(ident,))
        if old is not None:
            return {'decision':'already_applied' if old['body']==body else 'different_delivery'}
        db.execute('INSERT INTO receiver_receipts VALUES(?,?,?,?,?)',
            (ident,envelope['channel'],envelope['request'],envelope['digest'],body))
        return {'decision':'applied','delivery':ident}


def run(db, fixture):
    db.executescript(SCHEMA)
    result=dispatch.run(db,fixture['reservations'])
    return {'receipts':[apply(db,envelope,result['assignments']) for envelope in fixture['deliveries']],
        'dispatch':result}
