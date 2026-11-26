import json
import tempfile
import unittest
from pathlib import Path
import admissioncourt
from admissioncourt import contract,waiver,admit
from admissioncourt.store import connect,one,replay

ROOT=Path(__file__).resolve().parents[1]
def run_fixture(db,subsystem,name):
    module={'contract':contract,'waiver':waiver,'admit':admit}[subsystem]
    return [module.run(db,x) for x in json.loads((ROOT/'fixtures'/name).read_text(encoding='utf8'))]

class AdmissionBusiness(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.path=Path(self.tmp.name)/'db.sqlite';self.db=connect(self.path)
    def tearDown(self):self.db.close();self.tmp.cleanup()
    def contracts(self):run_fixture(self.db,'contract','contract_normal.json')
    def waiver(self):self.contracts();run_fixture(self.db,'waiver','waiver_normal.json')
    def base(self):self.waiver();run_fixture(self.db,'admit','admit_normal.json')

    def test_three_subsystem_fixtures_and_replay(self):
        self.base()
        run_fixture(self.db,'contract','contract_failure.json')
        run_fixture(self.db,'waiver','waiver_failure.json')
        run_fixture(self.db,'admit','admit_failure.json')
        self.assertEqual(one(self.db,"SELECT decision FROM admission_verdicts WHERE request_id='r1'")['decision'],'allowed')
        self.assertEqual(one(self.db,"SELECT decision FROM admission_verdicts WHERE request_id='r3'")['decision'],'denied')
        self.assertTrue(replay(self.path)['verified'])

    def test_contract_requires_two_distinct_roles(self):
        self.contracts()
        self.assertEqual(one(self.db,"SELECT state FROM contracts WHERE id='c1'")['state'],'active')
        self.assertEqual(contract.run(self.db,{'op':'propose','event_id':'new','id':'c1','revision':2,'mode':'reject','mandatory':True,'owner':'alice'})['decision'],'drafted')
        self.assertEqual(one(self.db,"SELECT revision FROM contracts WHERE id='c1'")['revision'],1)
        self.assertEqual(contract.run(self.db,{'op':'approve','event_id':'new-a','id':'c1','revision':2,'role':'security','actor':'alice','expires':50,'at':10})['decision'],'approval_recorded')
        self.assertEqual(contract.run(self.db,{'op':'approve','event_id':'new-b','id':'c1','revision':2,'role':'owner','actor':'alice','expires':50,'at':10})['decision'],'approval_recorded')
        self.assertEqual(contract.run(self.db,{'op':'activate','event_id':'new-on','id':'c1','revision':2,'at':11})['decision'],'activation_denied')

    def test_waiver_bound_to_tenant_uid_path_and_time(self):
        self.waiver()
        for id,tenant,uid,path,at in [('a','tenant-b','pod-1','/spec/replicas',10),('b','tenant-a','pod-new','/spec/replicas',10),('c','tenant-a','pod-1','/spec/other',10),('d','tenant-a','pod-1','/spec/replicas',31)]:
            admit.run(self.db,{'op':'submit','event_id':id+'s','id':id,'tenant':tenant,'uid':uid,'body':{'n':1},'at':at})
            admit.run(self.db,{'op':'call','event_id':id+'1','id':id+'1','request':id,'contract':'c1','outcome':'allow','at':at})
            admit.run(self.db,{'op':'call','event_id':id+'2','id':id+'2','request':id,'contract':'c2','outcome':'deny','path':path,'at':at})
            self.assertEqual(admit.run(self.db,{'op':'decide','event_id':id+'d','id':id,'path':path,'at':at})['decision'],'denied')

    def test_contract_revision_invalidates_waiver_and_pending_request(self):
        self.waiver()
        admit.run(self.db,{'op':'submit','event_id':'pending','id':'pending','tenant':'tenant-a','uid':'pod-1','body':{},'at':10})
        contract.run(self.db,{'op':'propose','event_id':'revision','id':'c2','revision':2,'mode':'reject','mandatory':False,'owner':'bob'})
        self.assertEqual(one(self.db,"SELECT state FROM waiver_requests WHERE id='w1'")['state'],'active')
        contract.run(self.db,{'op':'approve','event_id':'rev-a1','id':'c2','revision':2,'role':'security','actor':'sue','expires':50,'at':10})
        contract.run(self.db,{'op':'approve','event_id':'rev-a2','id':'c2','revision':2,'role':'owner','actor':'bob','expires':50,'at':10})
        contract.run(self.db,{'op':'activate','event_id':'rev-on','id':'c2','revision':2,'at':11})
        self.assertEqual(one(self.db,"SELECT state FROM waiver_requests WHERE id='w1'")['state'],'stale')
        self.assertEqual(admit.run(self.db,{'op':'decide','event_id':'pending-d','id':'pending','at':11})['decision'],'contract_changed')

    def test_revocation_blocks_pending_denial_waiver(self):
        self.waiver()
        self.assertEqual(waiver.run(self.db,{'op':'revoke','event_id':'rev-w','id':'w1'})['decision'],'waiver_revoked')
        admit.run(self.db,{'op':'submit','event_id':'req-x','id':'x','tenant':'tenant-a','uid':'pod-1','body':{},'at':10})
        for cid in ['c1','c2']:
            admit.run(self.db,{'op':'call','event_id':'x-'+cid,'id':'x-'+cid,'request':'x','contract':cid,'outcome':'allow' if cid=='c1' else 'deny','path':'/spec/replicas','at':11})
        self.assertEqual(admit.run(self.db,{'op':'decide','event_id':'x-d','id':'x','path':'/spec/replicas','at':12})['decision'],'denied')

    def test_mandatory_contract_remains_active_during_upgrade_draft(self):
        self.contracts()
        self.assertEqual(contract.run(self.db,{'op':'propose','event_id':'upgrade','id':'c1','revision':2,'mode':'degrade','mandatory':True,'owner':'alice'})['decision'],'drafted')
        self.assertEqual(one(self.db,"SELECT revision FROM contracts WHERE id='c1'")['revision'],1)
        result=admit.run(self.db,{'op':'submit','event_id':'submit','id':'request','tenant':'tenant-a','uid':'pod-1','body':{},'at':10})
        self.assertEqual(result['contracts'],['c1','c2'])
        admit.run(self.db,{'op':'call','event_id':'call1','id':'call1','request':'request','contract':'c1','outcome':'timeout','at':11})
        admit.run(self.db,{'op':'call','event_id':'call2','id':'call2','request':'request','contract':'c2','outcome':'allow','at':11})
        self.assertEqual(admit.run(self.db,{'op':'decide','event_id':'decision','id':'request','at':12})['reason'],'mandatory_timeout')

    def test_denial_path_comes_from_webhook_not_decider(self):
        self.waiver()
        admit.run(self.db,{'op':'submit','event_id':'rq','id':'rq','tenant':'tenant-a','uid':'pod-1','body':{},'at':10})
        admit.run(self.db,{'op':'call','event_id':'rq-c1','id':'rq-c1','request':'rq','contract':'c1','outcome':'allow','at':11})
        admit.run(self.db,{'op':'call','event_id':'rq-c2','id':'rq-c2','request':'rq','contract':'c2','outcome':'deny','path':'/spec/other','at':11})
        result=admit.run(self.db,{'op':'decide','event_id':'rq-d','id':'rq','path':'/spec/replicas','at':12})
        self.assertEqual(result['decision'],'denied')

    def test_unresolved_call_cannot_be_construed_as_timeout(self):
        self.contracts()
        admit.run(self.db,{'op':'submit','event_id':'rq','id':'rq','tenant':'t','uid':'u','body':{},'at':1})
        admit.run(self.db,{'op':'call','event_id':'rq-c1','id':'rq-c1','request':'rq','contract':'c1','outcome':'allow','at':2})
        self.assertEqual(admit.run(self.db,{'op':'decide','event_id':'rq-d','id':'rq','at':3})['decision'],'incomplete_calls')

    def test_mandatory_timeout_refuses_even_with_other_allow(self):
        self.contracts()
        admit.run(self.db,{'op':'submit','event_id':'rq','id':'rq','tenant':'t','uid':'u','body':{},'at':1})
        for cid,outcome in [('c1','timeout'),('c2','allow')]:
            admit.run(self.db,{'op':'call','event_id':'rq-'+cid,'id':'rq-'+cid,'request':'rq','contract':cid,'outcome':outcome,'at':2})
        self.assertEqual(admit.run(self.db,{'op':'decide','event_id':'rq-d','id':'rq','at':3})['reason'],'mandatory_timeout')

    def test_declared_mutation_and_conflicting_retry(self):
        self.contracts()
        admit.run(self.db,{'op':'submit','event_id':'rq','id':'rq','tenant':'t','uid':'u','body':{},'at':1})
        self.assertEqual(admit.run(self.db,{'op':'call','event_id':'bad','id':'bad','request':'rq','contract':'c1','outcome':'patch','path':'/other','value':'x','at':2})['decision'],'undeclared_patch')
        command={'op':'call','event_id':'good','id':'good','request':'rq','contract':'c1','outcome':'patch','path':'/spec/image','value':'v2','at':2}
        self.assertEqual(admit.run(self.db,command)['decision'],'call_recorded')
        self.assertEqual(admit.run(self.db,dict(command,value='v3'))['decision'],'event_identity_conflict')

    def test_request_identity_conflict_does_not_replace_original(self):
        self.contracts()
        command={'op':'submit','event_id':'r1','id':'business-r','tenant':'t','uid':'pod-u','body':{'image':'v1'},'at':1}
        self.assertEqual(admit.run(self.db,command)['decision'],'pending')
        self.assertEqual(admit.run(self.db,dict(command,event_id='r2',body={'image':'v2'}))['decision'],'request_identity_conflict')
        self.assertEqual(one(self.db,"SELECT state FROM admission_requests WHERE id='business-r'")['state'],'pending')

    def test_withdraw_does_not_reopen_final_verdict(self):
        self.base()
        contract.run(self.db,{'op':'withdraw','event_id':'wd','id':'c2','revision':1})
        self.assertEqual(one(self.db,"SELECT decision FROM admission_verdicts WHERE request_id='r1'")['decision'],'allowed')
        self.assertEqual(one(self.db,"SELECT state FROM waiver_requests WHERE id='w1'")['state'],'stale')

    def test_module_is_this_repo(self):
        self.assertEqual(Path(admissioncourt.__file__).resolve().parents[1],ROOT)

print('module_file',admissioncourt.__file__)
