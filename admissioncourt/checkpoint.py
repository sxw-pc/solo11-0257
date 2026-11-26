"""Offline control-plane recovery at a chosen committed event sequence."""
import json
from . import admit, contract, waiver
from .store import connect, many

HANDLERS = {'admit':admit.run, 'contract':contract.run, 'waiver':waiver.run}
RECOVERED_TABLES = ('contracts','waiver_requests','admission_requests','admission_verdicts')


def source_events(db, stop):
    return many(db,'SELECT * FROM events WHERE seq<=? ORDER BY seq',(stop,))


def run(db, fixture):
    watermark = fixture['through']
    if not isinstance(watermark,int) or watermark < 1:
        return {'decision':'invalid_watermark'}
    timeline=source_events(db,watermark)
    if not timeline or timeline[-1]['seq']!=watermark:
        return {'decision':'missing_watermark'}
    recovered=connect(':memory:')
    try:
        for event in timeline:
            result=HANDLERS[event['subsystem']](recovered,json.loads(event['payload']))
            if result!=json.loads(event['result']):
                return {'decision':'replay_diverged','event_id':event['event_id']}
        return {'decision':'recovered','through':watermark,'tables':{
            table:many(recovered,'SELECT * FROM '+table+' ORDER BY rowid') for table in RECOVERED_TABLES}}
    finally:
        recovered.close()
