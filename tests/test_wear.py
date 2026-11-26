import json
from pathlib import Path
import unittest
from admissioncourt import wear


class WearTests(unittest.TestCase):
    def fixture(self, suffix):
        return json.loads(Path(f'fixtures/wear_{suffix}.json').read_text(encoding='utf-8'))

    def test_normal_reading_triggers_inspection(self):
        row = wear.run(self.fixture('normal'))['equipment'][0]
        self.assertEqual(row, {'position':'rack-01/fan','used':102,'decision':'inspection_due'})

    def test_legacy_falling_reading_starts_a_new_segment(self):
        row = wear.run(self.fixture('wrap'))['equipment'][0]
        self.assertEqual(row, {'position':'rack-01/fan','used':90,'decision':'continue_service'})
