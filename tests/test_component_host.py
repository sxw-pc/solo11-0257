import json
from pathlib import Path
import unittest
from admissioncourt import component_host, components


class ComponentHostTests(unittest.TestCase):
    def fixture(self):
        return json.loads(Path('fixtures/component_host_normal.json').read_text(encoding='utf8'))

    def test_v2_selection_changes_compatibility(self):
        data = self.fixture()
        index = components.manifest(data['catalog'])['components']
        self.assertEqual(index[0]['edition'], 2)
        self.assertEqual([r['decision'] for r in component_host.run(data)['results']],
                         ['incompatible_call','returned'])
        data['catalog']['providers'][1]['available'] = False
        self.assertEqual([r['decision'] for r in component_host.run(data)['results']],
                         ['returned','incompatible_call'])

    def test_unavailable_component_returns_explicit_error(self):
        data = self.fixture()
        data['catalog']['providers'].clear()
        self.assertEqual([r['decision'] for r in component_host.run(data)['results']],
                         ['component_missing','component_missing'])
