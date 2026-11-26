import unittest
import json
from contextlib import closing
from pathlib import Path
from admissioncourt import bulletin, drill
from admissioncourt.store import connect


def fixture(name):
    return json.loads((Path('fixtures') / f'{name}_normal.json').read_text(encoding='utf8'))


class DrillTests(unittest.TestCase):
    def test_default_and_failed_over_observations(self):
        synthetic = fixture('drill')
        self.assertEqual(drill.run(synthetic)['counts'], {'200': 4, '503': 1})
        synthetic['replicas']['new']['state'] = 'unavailable'
        self.assertEqual(drill.run(synthetic)['counts'], {'500': 4, '503': 1})

    def test_bulletin_idempotency_and_actual_readers(self):
        db = self.enterContext(closing(connect(':memory:')))
        item = fixture('bulletin')
        published = bulletin.publish(db, item)
        self.assertEqual(published['counts'], {'200': 4, '503': 1})
        self.assertEqual(bulletin.read(db, 'exercise-1', 'service-a'), published)
        self.assertEqual(bulletin.read(db, 'exercise-1', 'other')['decision'], 'bulletin_unavailable')
        self.assertEqual(bulletin.publish(db, item), published)
        item['readers'].append('other')
        self.assertEqual(bulletin.publish(db, item)['decision'], 'bulletin_identity_conflict')
