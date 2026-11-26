"""Run synthetic process events without launching native processes."""
def run(events):
    active = {}
    stdout = []
    exited = set()
    for event in events:
        pid = event['pid']
        if event['op'] == 'start':
            active[pid] = event['pipe']
            exited.discard(pid)
        elif event['op'] == 'write' and pid in active:
            stdout.append({'pipe':active[pid], 'pid':pid, 'text':event['text']})
        elif event['op'] == 'exit' and pid in active:
            active.pop(pid)
            exited.add(pid)
    return {'stdout':stdout, 'exited':sorted(exited)}
