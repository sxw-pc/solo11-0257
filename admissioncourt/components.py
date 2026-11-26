"""Resolve fixture-local named component exports without running external code."""
from .admit import hash_value


def select(fixture):
    providers = {}
    for candidate in fixture['providers']:
        if not candidate['available']:
            continue
        name = candidate['name']
        old = providers.get(name)
        if old is None or candidate['edition'] > old['edition']:
            providers[name] = candidate
    return providers


def manifest(fixture):
    selected = select(fixture)
    return {'decision':'resolved', 'components':[
        {**row, 'digest':hash_value(row['exports'])} for _, row in sorted(selected.items())]}
