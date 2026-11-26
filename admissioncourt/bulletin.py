"""Publish a persistent synthetic operations bulletin to a named readership."""
import json
from . import drill
from .store import one, transaction

SCHEMA = """CREATE TABLE IF NOT EXISTS drill_bulletins(
    name TEXT PRIMARY KEY, request TEXT NOT NULL, bulletin TEXT NOT NULL
);"""


def publish(db, fixture):
    db.executescript(SCHEMA)
    readers = fixture['readers']
    if not readers or len(readers) != len(set(readers)):
        return {'decision': 'invalid_readers'}
    source = drill.run(fixture['drill'])
    if source['decision'] != 'observed':
        return source
    name = fixture['bulletin']
    body = json.dumps(fixture, sort_keys=True, separators=(',', ':'))
    with transaction(db):
        old = one(db, 'SELECT request, bulletin FROM drill_bulletins WHERE name=?', (name,))
        if old:
            return json.loads(old['bulletin']) if old['request'] == body else {'decision': 'bulletin_identity_conflict'}
        result = {'decision': 'published', 'bulletin': name, 'readers': sorted(readers),
                  'counts': source['counts']}
        db.execute('INSERT INTO drill_bulletins VALUES (?,?,?)', (name, body, json.dumps(result)))
        return result


def read(db, name, reader):
    db.executescript(SCHEMA)
    row = one(db, 'SELECT bulletin FROM drill_bulletins WHERE name=?', (name,))
    document = json.loads(row['bulletin']) if row else None
    if document is None or reader not in document['readers']:
        return {'decision': 'bulletin_unavailable'}
    return document
