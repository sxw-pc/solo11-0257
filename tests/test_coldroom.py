import json
from pathlib import Path
import unittest
from admissioncourt import coldroom, thermal


def fixture():
    return json.loads(Path('fixtures/coldroom_normal.json').read_text(encoding='utf8'))


class ColdRoomTests(unittest.TestCase):
    def test_neighbor_heat_arrives_in_occupied_bay(self):
        setup = fixture()
        history = thermal.run(setup['model'])['timeline']
        self.assertEqual(history[1], {'A':22, 'B':19})
        self.assertEqual(coldroom.run(setup)['decision'],'kept_in_range')

    def test_missing_cooling_leaves_earlier_excursion(self):
        setup = fixture()
        setup['model']['steps'][0]['A'] = 0
        out = coldroom.run(setup)
        self.assertEqual(out['decision'],'excursion')
        self.assertEqual(out['steps_above_limit'],[1])
