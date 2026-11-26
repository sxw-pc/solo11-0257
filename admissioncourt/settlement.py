"""Invoice the actually recorded delivery receipts in a synthetic workspace."""
import json
from . import receiver
from .store import one, transaction

SCHEMA = """
CREATE TABLE IF NOT EXISTS settlement_invoices(
    invoice_id TEXT PRIMARY KEY, body TEXT NOT NULL, result TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS settlement_invoice_lines(
    delivery TEXT PRIMARY KEY, invoice_id TEXT NOT NULL, tenant TEXT NOT NULL,
    channel TEXT NOT NULL, cents INTEGER NOT NULL
);
"""


def run(db, fixture):
    db.executescript(receiver.SCHEMA + SCHEMA)
    invoice_id = fixture['invoice']
    deliveries = fixture['deliveries']
    tariffs = fixture['tariffs']
    if len(set(deliveries)) != len(deliveries) or not deliveries:
        return {'decision': 'invalid_delivery_list'}
    if any(type(rate) is not int or rate < 0 or rate > 10**12 for rate in tariffs.values()):
        return {'decision': 'invalid_tariff'}
    body = json.dumps(fixture, sort_keys=True, separators=(',', ':'))
    with transaction(db):
        old = one(db, 'SELECT body, result FROM settlement_invoices WHERE invoice_id=?', (invoice_id,))
        if old is not None:
            return json.loads(old['result']) if old['body'] == body else {'decision': 'invoice_identity_conflict'}
        lines = []
        for delivery in deliveries:
            existing = one(db, 'SELECT invoice_id FROM settlement_invoice_lines WHERE delivery=?', (delivery,))
            if existing is not None:
                return {'decision': 'delivery_already_invoiced', 'delivery': delivery, **existing}
            receipt = one(db, """SELECT rr.channel, ar.tenant
                FROM receiver_receipts rr JOIN admission_requests ar ON ar.id=rr.request
                WHERE rr.delivery=?""", (delivery,))
            if receipt is None:
                return {'decision': 'receipt_missing', 'delivery': delivery}
            if receipt['channel'] not in tariffs:
                return {'decision': 'tariff_missing', 'channel': receipt['channel']}
            lines.append({'delivery': delivery, **receipt, 'cents': tariffs[receipt['channel']]})
        result = {'decision': 'issued', 'invoice': invoice_id, 'lines': lines,
                  'total_cents': sum(line['cents'] for line in lines)}
        db.execute('INSERT INTO settlement_invoices VALUES(?,?,?)', (invoice_id, body, json.dumps(result)))
        db.executemany('INSERT INTO settlement_invoice_lines VALUES(?,?,?,?,?)',
            [(line['delivery'], invoice_id, line['tenant'], line['channel'], line['cents']) for line in lines])
        return result
