import argparse,json
from pathlib import Path
from . import assembly, build_compare, component_host, components, endorse, settlement
from . import receiver
from . import checkpoint
from . import projections
from . import dispatch
from . import handover_build
from .store import connect,replay,many
from . import contract,waiver,admit

def main():
    p=argparse.ArgumentParser();p.add_argument('command',choices=['contract','waiver','admit','demo','replay','report','dispatch','projections','checkpoint','receiver','endorse','settlement','components','component-host','assembly','build-compare','handover-build','handover-consume']);p.add_argument('--db',default='demo.sqlite');p.add_argument('--fixture');p.add_argument('--package');p.add_argument('--edit');p.add_argument('--set',dest='overrides',action='append',default=[]);a=p.parse_args()
    if a.command=='replay':
        result=replay(a.db)
    elif a.command in ('handover-build','handover-consume'):
        if not a.package:
            p.error('--package required')
        if a.command=='handover-build':
            if not a.fixture:
                p.error('--fixture required')
            fixture=json.loads(Path(a.fixture).read_text(encoding='utf-8'))
            result=handover_build.build_package(fixture,a.package)
        else:
            overrides={}
            for item in a.overrides:
                if '=' not in item:
                    p.error('--set expects role=value')
                role,value=item.split('=',1)
                overrides[role]=value
            result=handover_build.consume_package(a.package,edit_id=a.edit,
                                                  overrides=overrides or None)
    else:
        db=connect(a.db)
        if a.command in ('assembly','build-compare'):
            if not a.fixture:
                p.error('--fixture required')
            fixture=json.loads(Path(a.fixture).read_text(encoding='utf-8'))
            result=assembly.run(fixture) if a.command=='assembly' else build_compare.run(fixture)
        elif a.command in ('components','component-host'):
            if not a.fixture:
                p.error('--fixture required')
            fixture=json.loads(Path(a.fixture).read_text(encoding='utf-8'))
            result=components.manifest(fixture) if a.command=='components' else component_host.run(fixture)
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
    if a.command=='handover-build' and result.get('decision')!='packaged':
        raise SystemExit(2)
    if a.command=='handover-consume' and result.get('decision')!='delivered':
        raise SystemExit(2)
if __name__=='__main__':main()
