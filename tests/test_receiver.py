import json
import unittest
from pathlib import Path
from admissioncourt import admit,contract,receiver,store,waiver


class ReceiverTests(unittest.TestCase):
    def test_confirm_requires_dispatch_and_retry_preserves_receipt(self):
        db=store.connect(':memory:'); self.addCleanup(db.close)
        for module in (contract,waiver,admit):
            name=module.__name__.split('.')[-1]
            for event in json.loads(Path(f'fixtures/{name}_normal.json').read_text(encoding='utf-8')):
                module.run(db,event)
        fixture=json.loads(Path('fixtures/receiver_normal.json').read_text(encoding='utf-8'))
        outcomes=receiver.run(db,fixture)['receipts']
        self.assertEqual([x['decision'] for x in outcomes],['applied','already_applied','reservation_missing'])
        self.assertEqual(receiver.run(db,fixture)['receipts'][0]['decision'],'already_applied')
