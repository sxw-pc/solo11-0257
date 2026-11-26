import json
from pathlib import Path
import unittest
from admissioncourt import console_output


class ConsoleOutputTests(unittest.TestCase):
    def fixture(self):
        return json.loads(Path('fixtures/console_output_normal.json').read_text(encoding='utf-8'))

    def test_live_process_gates_baseline(self):
        fixture = self.fixture()
        fixture['events'] = fixture['events'][:3]
        self.assertEqual(console_output.run(fixture), {'decision':'still_running'})

    def test_full_trace_includes_late_worker_text(self):
        self.assertEqual(console_output.run(self.fixture()), {'decision':'ready','text':'parent=2\nworker=3\n'})
