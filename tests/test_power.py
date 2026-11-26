import json
import unittest
from pathlib import Path
from admissioncourt import power, shift


def fixture(name):
    return json.loads((Path('fixtures') / (name + '.json')).read_text(encoding='utf-8'))


class RackPowerTests(unittest.TestCase):
    def test_feed_transfer_uses_actual_alternate_limit(self):
        samples = power.observe(fixture('power_shift'))['snapshots']
        self.assertEqual(samples[0]['watts'], {'north': 550, 'south': 350})
        self.assertEqual(samples[1]['watts'], {'north': 0, 'south': 900})
        self.assertEqual(samples[1]['overloads'], ['south'])

    def test_work_dispatch_tracks_the_fed_load(self):
        self.assertEqual(len(shift.run(fixture('shift_normal'))['ready']), 2)
        self.assertEqual([row['reason'] for row in shift.run(fixture('shift_failed'))['held']],
                         ['overload', 'overload'])
