import json
import unittest
from pathlib import Path
from admissioncourt import contract,endorse,store


class EndorseTests(unittest.TestCase):
    def test_revision_aware_packet_and_expiry(self):
        db=store.connect(':memory:'); self.addCleanup(db.close)
        for e in json.loads(Path('fixtures/contract_normal.json').read_text(encoding='utf8')):
            contract.run(db,e)
        fixture=json.loads(Path('fixtures/endorse_normal.json').read_text(encoding='utf8'))
        result=endorse.run(db,fixture)
        self.assertEqual(result['decision'],'packet_ready')
        self.assertEqual({v['actor'] for v in result['votes']},{'alice','sue'})
        self.assertEqual(endorse.run(db,{**fixture,'at':101})['decision'],'pending_approval')
        self.assertEqual(endorse.run(db,{**fixture,'revision':2})['decision'],'contract_missing')
