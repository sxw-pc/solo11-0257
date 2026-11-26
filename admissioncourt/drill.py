"""Run synthetic route observations against a fixture-local replica fleet."""
from collections import Counter
from . import router


def query(routes, endpoints, path):
    health = {name: endpoint['state'] for name, endpoint in endpoints.items()}
    selection = router.winner(routes, health, path)
    destination = selection['backend']
    if destination is None:
        return {'path': path, 'status': 503, 'revision': None, **selection}
    replica = endpoints[destination]
    return {'path': path, 'status': replica['status'], 'revision': replica['revision'], **selection}


def run(fixture):
    if not fixture['samples'] or any(type(sample['count']) is not int or sample['count'] < 1
                                     for sample in fixture['samples']):
        return {'decision': 'invalid_plan'}
    observations = []
    statuses = Counter()
    for sample in fixture['samples']:
        observed = query(fixture['routes'], fixture['replicas'], sample['path'])
        observations.append({**observed, 'count': sample['count']})
        statuses[str(observed['status'])] += sample['count']
    return {'decision': 'observed', 'observations': observations, 'counts': dict(statuses)}
