"""Read fixture-local operational minute counters of replaceable equipment."""
from collections import defaultdict


def read(fixture):
    last = {}
    elapsed = defaultdict(int)
    segments = []
    for reading in fixture['readings']:
        name = reading['position']
        value = reading['minutes']
        if name in last:
            delta = max(0, value - last[name])
            elapsed[name] += delta
            segments.append({'position':name, 'end':reading['at'], 'minutes':delta})
        last[name] = value
    return {'decision':'read', 'minutes':dict(elapsed), 'segments':segments}
