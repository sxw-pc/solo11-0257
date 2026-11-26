"""Assemble a small declarative artifact using fixture-local build tools."""
from .admit import hash_value
from . import components


def run(fixture):
    tools = components.select(fixture['catalog'])
    files = dict(fixture['sources'])
    trace = []
    for step in fixture['recipe']:
        tool = tools.get(step['tool'])
        if tool is None:
            return {'decision':'tool_missing', 'step':step['id']}
        operation = tool['exports'].get(step['operation'])
        if operation is None:
            return {'decision':'operation_missing', 'step':step['id']}
        contents = [files[name] for name in step['inputs']]
        output = operation['separator'].join(contents)
        files[step['output']] = output
        trace.append({'step':step['id'], 'tool':step['tool'], 'edition':tool['edition'],
                      'inputs':[hash_value(content) for content in contents], 'result':hash_value(output)})
    return {'decision':'built', 'artifact':files[fixture['artifact']], 'trace':trace}
