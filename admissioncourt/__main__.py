import argparse,json
from pathlib import Path
from . import endorse, odometer, settlement, wear
from . import receiver
from . import checkpoint
from . import projections
from . import dispatch
from . import dispatch
from .store import connect,replay,many
from . import contract,waiver,admit

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['contract','waiver','admit','demo','replay','report','dispatch','projections','checkpoint','receiver','endorse','settlement','odometer','wear']);p.add_argument('--db',default='demo.sqlite');p.add_argument('--fixture');a=p.parse_args()
    if a.command=='replay':
        result=replay(a.db)
    else:
        db=connect(a.db)
        if a.command in ('odometer','wear'):
            if not a.fixture:
                p.error('--fixture required')
            fixture=json.loads(Path(a.fixture).read_text(encoding='utf-8'))
            result=odometer.read(fixture) if a.command=='odometer' else wear.run(fixture)
        elif a.command=='settlement':
            if not a.fixture:
                p.error('--fixture required')
            result=settlement.run(db,json.loads(Path(a.fixture).read_text(encoding='utf-8')))
        elif a.command=='endorse':
            if not a.fixture:
                p.error('--fixture required')
            result=endorse.run(db,json.loads(Path(a.fixture).read_text(encoding='utf-8')))
        elif a.command=='receiver':
            if not a.fixture:
                p.error('--fixture required')
            result=receiver.run(db,json.loads(Path(a.fixture).read_text(encoding='utf-8')))
        elif a.command=='checkpoint':
            if not a.fixture:
                p.error('--fixture required')
            result=checkpoint.run(db,json.loads(Path(a.fixture).read_text(encoding='utf-8')))
        elif a.command=='projections':
            if not a.fixture:p.error('--fixture required')
            result=projections.run(db,json.loads(Path(a.fixture).read_text(encoding='utf-8')))
        elif a.command=='dispatch':
            if not a.fixture:p.error('--fixture required')
            result=dispatch.run(db,json.loads(Path(a.fixture).read_text(encoding='utf-8')))
        elif a.command=='report':result=many(db,'SELECT * FROM events ORDER BY seq')
        else:
            paths=([Path('fixtures')/n for n in ['contract_normal.json','waiver_normal.json','admit_normal.json','contract_failure.json','waiver_failure.json','admit_failure.json']]
                   if a.command=='demo' else [Path(a.fixture)] if a.fixture else [])
            if not paths:p.error('--fixture required')
            result=[]
            for path in paths:
                subsystem=path.name.split('_')[0] if a.command=='demo' else a.command
                handler={'contract':contract.run,'waiver':waiver.run,'admit':admit.run}[subsystem]
                result.append({'fixture':str(path),'outcomes':[handler(db,x) for x in json.loads(path.read_text(encoding='utf8'))]})
        db.close()
    print(json.dumps(result,ensure_ascii=False,indent=2))
    if a.command=='replay' and not result['verified']:raise SystemExit(2)
if __name__=='__main__':main()
