"""Describe changes between a baseline and a proposed workload configuration."""
from .admit import hash_value
from .router import winner
from .store import one


def changes(left, right, prefix=''):
    if isinstance(left, dict) and isinstance(right, dict):
        for key in sorted(left.keys() | right.keys()):
            escaped = key.replace('~','~0').replace('/','~1')
            path = f'{prefix}/{escaped}'
            if key not in left:
                yield {'path':path,'change':'added','value':right[key]}
            elif key not in right:
                yield {'path':path,'change':'removed','value':left[key]}
            else:
                yield from changes(left[key],right[key],path)
    elif hash_value(left)!=hash_value(right):
        yield {'path':prefix,'change':'replaced','before':left,'value':right}


def run(db, fixture):
    request = one(db,"SELECT tenant,uid,body_hash FROM admission_requests WHERE id=?",(fixture['request'],))
    if request is None or request['body_hash']!=hash_value(fixture['candidate']):
        return {'decision':'request_content_mismatch'}
    result = {'decision':'projected','tenant':request['tenant'],'uid':request['uid'],
        'changes':list(changes(fixture['baseline'],fixture['candidate']))}

    if 'probe_paths' in fixture:
        backend_states=fixture['backends']
        result['routes']=[{'path':path,'before':winner(fixture['baseline']['routes'],backend_states,path),
            'after':winner(fixture['candidate']['routes'],backend_states,path)} for path in fixture['probe_paths']]
    return result
