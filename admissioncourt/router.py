"""Evaluate synthetic first-match routes against an explicit backend registry."""


def matches(prefix, path):
    if prefix=='/':
        return True
    return path==prefix or path.startswith(prefix.rstrip('/')+'/')


def winner(routes, backends, path):
    for rule in routes:
        if not matches(rule['prefix'],path):
            continue
        for backend in rule['backends']:
            if backends.get(backend)=='healthy':
                return {'rule':rule['id'],'backend':backend}
    return {'rule':None,'backend':None}
