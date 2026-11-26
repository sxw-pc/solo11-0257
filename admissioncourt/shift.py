"""Classify occupied rack work from a feed-meter sample (offline model)."""
from . import power


def run(fixture):
    state = power.observe(fixture['rack'])['snapshots'][-1]
    load_index = {item['id']:item for item in state['allocations']}
    ready = []
    held = []
    for job in fixture['jobs']:
        supply = load_index.get(job['load'])
        if not supply or supply['feed'] is None:
            held.append({'id':job['id'], 'reason':'no_supply'})
        elif supply['feed'] in state['overloads']:
            held.append({'id':job['id'], 'reason':'overload'})
        else:
            ready.append({'id':job['id'], 'feed':supply['feed']})
    return {'decision':'classified', 'ready':ready, 'held':held, 'meter':state}
