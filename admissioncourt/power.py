"""Evaluate fixture-local rack loads and feed meter readings at one instant."""
from copy import deepcopy


def sample(fixture):
    readings = {name: 0 for name in fixture['feeds']}
    allocations = []
    for load in fixture['loads']:
        feed = next((name for name in load['feeds'] if fixture['feeds'][name]['online']), None)
        if feed is not None:
            readings[feed] += load['watts']
        allocations.append({'id':load['id'], 'feed':feed, 'watts':load['watts'] if feed else 0})
    return {'allocations':allocations, 'watts':readings,
            'overloads':sorted(name for name, value in readings.items()
                               if value > fixture['feeds'][name]['limit'])}


def observe(fixture):
    state = deepcopy(fixture)
    snapshots = [sample(state)]
    for change in fixture.get('changes', []):
        state['feeds'][change['feed']]['online'] = change['online']
        snapshots.append(sample(state))
    return {'decision':'sampled', 'snapshots':snapshots}
