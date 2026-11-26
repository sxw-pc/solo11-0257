"""Apply approved final rack poses in a local facility fixture."""
from copy import deepcopy
from . import floorplan


def run(fixture):
    plan = deepcopy(fixture['plan'])
    outcomes = []
    for command in fixture['moves']:
        item = next((row for row in plan['racks'] if row['id'] == command['rack']), None)
        if item is None:
            outcomes.append({'rack':command['rack'], 'result':'rack_missing'})
            continue
        x,y = command['x'],command['y']
        result = floorplan.inspect(plan, item, x, y)
        if result == 'available':
            item.update(x=x,y=y)
            result = 'relocated'
        outcomes.append({'rack':item['id'], 'result':result})
    return {'decision':'executed', 'outcomes':outcomes, 'racks':plan['racks']}
