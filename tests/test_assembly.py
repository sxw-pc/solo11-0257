import json
from pathlib import Path
import unittest
from admissioncourt import assembly, build_compare


class AssemblyTests(unittest.TestCase):
    def test_actual_assembly_and_trace(self):
        case = json.loads(Path('fixtures/assembly_normal.json').read_text(encoding='utf-8'))
        result = assembly.run(case)
        self.assertEqual(result['artifact'], 'header|body')
        self.assertEqual(result['trace'][0]['edition'], 1)
        case['catalog']['providers'][0]['available'] = False
        self.assertEqual(assembly.run(case)['decision'], 'tool_missing')

    def test_source_edit_affects_real_comparison(self):
        case = json.loads(Path('fixtures/build_compare_normal.json').read_text(encoding='utf-8'))
        self.assertEqual(build_compare.run(case)['decision'], 'matched')
        case['observed']['sources']['back'] = 'next'
        self.assertEqual(build_compare.run(case)['decision'], 'different')
