import json
import unittest
from pathlib import Path

from admissioncourt import admit, contract, dispatch, store, waiver


class DispatchTests(unittest.TestCase):
    def setUp(self):
        self.db = store.connect(':memory:')
        self.addCleanup(self.db.close)
        for sub, handler in [('contract',contract.run),('waiver',waiver.run),('admit',admit.run)]:
            for command in json.loads(Path(f'fixtures/{sub}_normal.json').read_text(encoding='utf-8')):
                handler(self.db,command)
        self.fixture = json.loads(Path('fixtures/dispatch_normal.json').read_text(encoding='utf-8'))

    def test_fixture_protects_admission_and_channel_limit(self):
        result = dispatch.run(self.db,self.fixture)
        self.assertEqual([row['request'] for row in result['assignments']],['r1'])
        self.assertEqual(result['deferred'],[{'request':'r2','reason':'delivery_capacity'}])

    def test_only_current_proof_for_the_named_finalized_request_qualifies(self):
        self.fixture['manifests'][0]['digest'] = 'other'
        result = dispatch.run(self.db,self.fixture)
        self.assertEqual([row['request'] for row in result['assignments']],['r2'])
        self.assertEqual(result['deferred'][0]['reason'],'admission_unproven')
