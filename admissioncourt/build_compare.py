"""Compare actual emitted artifact bytes from two independent builds."""
from . import assembly
from .admit import hash_value


def run(fixture):
    left = assembly.run(fixture['reference'])
    right = assembly.run(fixture['observed'])
    complete = left['decision'] == right['decision'] == 'built'
    match = complete and left['artifact'] == right['artifact']
    return {'decision':'matched' if match else 'different' if complete else 'build_failed',
            'reference':hash_value(left['artifact']) if left['decision']=='built' else left,
            'observed':hash_value(right['artifact']) if right['decision']=='built' else right}
