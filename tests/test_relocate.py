import json
from pathlib import Path
import unittest
from admissioncourt import relocate


class RelocateTests(unittest.TestCase):
    def run_case(self, suffix):
        return relocate.run(json.loads(Path(f'fixtures/relocate_{suffix}.json').read_text(encoding='utf-8')))

    def test_unoccupied_destination_relocates(self):
        out = self.run_case('normal')
        self.assertEqual(out['outcomes'], [{'rack':'wide','result':'relocated'}])
        self.assertEqual(out['racks'][0]['x'], 5)

    def test_occupied_destination_preserves_old_pose(self):
        out = self.run_case('blocked')
        self.assertEqual(out['outcomes'], [{'rack':'wide','result':'occupied'}])
        self.assertEqual(out['racks'][0]['x'], 0)
