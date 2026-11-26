import json
import unittest
from pathlib import Path
from admissioncourt import admit, checkpoint, contract, store, waiver


class CheckpointTests(unittest.TestCase):
    def test_intermediate_cut_and_later_verdict(self):
        db=store.connect(':memory:')
        self.addCleanup(db.close)
        handlers={'contract':contract.run,'waiver':waiver.run,'admit':admit.run}
        for n, handler in handlers.items():
            for event in json.loads(Path(f'fixtures/{n}_normal.json').read_text(encoding='utf-8')):
                handler(db,event)
        before=store.one(db,"SELECT seq FROM events WHERE event_id='w1on'")['seq']
        snapshot=checkpoint.run(db,{'through':before})
        self.assertEqual(snapshot['tables']['admission_verdicts'],[])
        self.assertEqual(snapshot['tables']['waiver_requests'][0]['state'],'active')
        latest=store.one(db,'SELECT max(seq) AS seq FROM events')['seq']
        self.assertEqual(len(checkpoint.run(db,{'through':latest})['tables']['admission_verdicts']),2)
        self.assertEqual(checkpoint.run(db,{'through':latest+1})['decision'],'missing_watermark')
