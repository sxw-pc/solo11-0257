"""Expose an offline command's captured output after the controller exits."""
from . import process_model


def run(fixture):
    state = process_model.run(fixture['events'])
    if fixture['controller_pid'] not in state['exited']:
        return {'decision':'still_running'}
    lines = [item['text'] for item in state['stdout'] if item['pipe'] == fixture['pipe']]
    return {'decision':'ready', 'text':''.join(lines)}
