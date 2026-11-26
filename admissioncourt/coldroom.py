"""Check a synthetic load's whole thermal history up to the offered close time."""
from . import thermal


def run(fixture):
    timeline = thermal.run(fixture['model'])['timeline']
    zone, cap = fixture['storage']['bay'], fixture['storage']['limit']
    exceeded = [time for time, window in enumerate(timeline) if window[zone] > cap]
    return {'decision':'kept_in_range' if not exceeded else 'excursion', 'steps_above_limit':exceeded,
            'measured': [window[zone] for window in timeline]}
