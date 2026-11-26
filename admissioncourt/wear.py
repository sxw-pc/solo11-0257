"""Return synthetic inspection recommendations from an actual meter trace."""
from . import odometer


def run(fixture):
    meter = odometer.read(fixture)
    rows = []
    for item in fixture['equipment']:
        used = item['carried_minutes'] + meter['minutes'].get(item['position'], 0)
        rows.append({'position':item['position'], 'used':used,
                     'decision':'inspection_due' if used >= item['service_limit'] else 'continue_service'})
    return {'decision':'classified','equipment':rows, 'meter':meter}
