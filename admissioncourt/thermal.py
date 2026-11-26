"""Simulate a fixture-only integer temperature model for isolated equipment bays."""

def run(fixture):
    temperature = {name: bay['initial'] for name,bay in fixture['bays'].items()}
    timeline = [dict(temperature)]
    for controls in fixture['steps']:
        following = {}
        for name, bay in fixture['bays'].items():
            transfer = sum(temperature[neighbor] - temperature[name] for neighbor in bay.get('neighbors', ()))
            following[name] = temperature[name] + bay['heat'] - controls.get(name, 0) + transfer
        temperature = following
        timeline.append(dict(temperature))
    return {'decision':'simulated','timeline':timeline}
