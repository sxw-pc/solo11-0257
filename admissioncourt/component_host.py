"""Call synthetic exported operations in one component-host namespace."""
from . import components


def run(fixture):
    selected = components.select(fixture['catalog'])
    results = []
    for call in fixture['calls']:
        provider = selected.get(call['component'])
        if provider is None:
            results.append({'id':call['id'], 'decision':'component_missing'})
            continue
        item = provider['exports'].get(call['operation'])
        if item is None:
            results.append({'id':call['id'], 'decision':'export_missing'})
            continue
        if item['arity'] != len(call['arguments']):
            results.append({'id':call['id'], 'decision':'incompatible_call', 'edition':provider['edition']})
            continue
        result = sum(call['arguments']) + item['offset']
        results.append({'id':call['id'], 'decision':'returned', 'edition':provider['edition'], 'value':result})
    return {'decision':'executed', 'results':results}
