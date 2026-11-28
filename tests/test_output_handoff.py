import json
from pathlib import Path
import unittest
from admissioncourt import console_output, output_handoff


def load(name):
    return json.loads(Path('fixtures', name).read_text(encoding='utf-8'))


def find(result, scenario_id):
    return next(s for s in result['scenarios'] if s['scenario'] == scenario_id)


def checkpoint(scenario, name):
    return next(c for c in scenario['checkpoints'] if c['checkpoint'] == name)


class ConsoleOutputPrefixTests(unittest.TestCase):
    def test_four_event_prefix_ready_with_only_parent_line(self):
        fixture = load('console_output_normal.json')
        fixture['events'] = fixture['events'][:4]
        self.assertEqual(console_output.run(fixture),
                         {'decision': 'ready', 'text': 'parent=2\n'})

    def test_five_event_prefix_still_one_snapshot_two_lines(self):
        fixture = load('console_output_normal.json')
        fixture['events'] = fixture['events'][:5]
        self.assertEqual(console_output.run(fixture),
                         {'decision': 'ready', 'text': 'parent=2\nworker=3\n'})

    def test_original_full_trace_behavior_unchanged(self):
        self.assertEqual(console_output.run(load('console_output_normal.json')),
                         {'decision': 'ready', 'text': 'parent=2\nworker=3\n'})


class OutputHandoffNormalTests(unittest.TestCase):
    def setUp(self):
        self.result = output_handoff.run(load('output_handoff_normal.json'))

    def test_whole_tree_download_holds_all_three_records(self):
        scenario = find(self.result, 'whole_tree')
        artifact = scenario['artifacts']['parent_download']
        self.assertEqual(artifact['decision'], 'sealed')
        self.assertEqual(artifact['text'], 'parent=2\nworker=3\nnext=4\n')
        self.assertEqual(artifact['records'], ['r1', 'r2', 'r3'])
        self.assertEqual(scenario['restarts'], 1)

    def test_download_bytes_are_immutable_after_seal(self):
        claim = find(self.result, 'whole_tree')['claims'][0]
        self.assertTrue(claim['digest_immutable'])
        self.assertTrue(claim['text_match'])

    def test_parent_exited_but_inherited_handle_open_blocks_ready(self):
        scenario = find(self.result, 'whole_tree')
        snap = checkpoint(scenario, 'parent_exited_child_open')
        artifact = snap['artifacts']['parent_download']
        self.assertEqual(artifact['decision'], 'not_sealed')
        self.assertEqual(artifact['reason'], 'waiting_writer')
        self.assertEqual(artifact['open_handles'], ['h2'])

    def test_recovery_uses_durable_journal_not_pid_or_pipe_tables(self):
        import hashlib
        scenario = find(self.result, 'whole_tree')
        # The restart happens after every handle is durably closed: the seal
        # survives and its digest matches an independent hash of the bytes.
        sealed = scenario['artifacts']['parent_download']
        expect = hashlib.sha256(
            'parent=2\nworker=3\nnext=4\n'.encode('utf-8')).hexdigest()
        self.assertEqual(sealed['digest'], expect)
        self.assertEqual(scenario['rejections'], [])
        self.assertEqual(scenario['restarts'], 1)
        # The claim after recovery carries job and writer generation, not a pid.
        self.assertEqual(sealed['job'], 'job-1')
        self.assertEqual(sealed['generation'], 'stdio-1.gen0')

    def test_saved_fragment_with_lost_ack_and_retried_lost_fragment_one_line(self):
        scenario = find(self.result, 'whole_tree')
        paths = scenario['delivery_paths']
        self.assertEqual(paths.get('ack_lost'), 1)
        self.assertEqual(paths.get('retry_persisted'), 1)
        self.assertEqual(scenario['runtime']['deduped'], 1)
        self.assertEqual(scenario['runtime']['delivery_lost'], 1)
        self.assertEqual(scenario['artifacts']['parent_download']['text'],
                         'parent=2\nworker=3\nnext=4\n')

    def test_relay_splits_at_record_boundary_only(self):
        scenario = find(self.result, 'parent_child_relay')
        parent = scenario['artifacts']['parent_download']
        child = scenario['artifacts']['child_download']
        self.assertEqual(parent['decision'], 'sealed')
        self.assertEqual(parent['text'], 'parent=2\nworker=3\n')
        self.assertEqual(parent['records'], ['r1', 'r2'])
        self.assertEqual(child['decision'], 'sealed')
        self.assertEqual(child['text'], 'next=4\n')
        self.assertEqual(child['records'], ['r3'])

    def test_relay_artifacts_seal_independently_without_redelivery(self):
        scenario = find(self.result, 'parent_child_relay')
        # Both downloads seal on their own records; the child's record is
        # never also materialized in the parent and vice versa.
        self.assertEqual(scenario['artifacts']['parent_download']['decision'], 'sealed')
        self.assertEqual(scenario['artifacts']['child_download']['decision'], 'sealed')
        self.assertEqual(scenario['claims'][0]['digest_immutable'], True)
        self.assertEqual(scenario['claims'][1]['digest_immutable'], True)
        self.assertEqual(scenario['rejections'], [])


class OutputHandoffRegressionTests(unittest.TestCase):
    def setUp(self):
        self.result = output_handoff.run(load('output_handoff_failure.json'))

    def test_relay_confirm_lost_keeps_every_line_on_parent(self):
        scenario = find(self.result, 'relay_confirm_lost')
        parent = scenario['artifacts']['parent_download']
        child = scenario['artifacts']['child_download']
        self.assertEqual(parent['decision'], 'sealed')
        self.assertEqual(parent['text'], 'parent=2\nworker=3\nnext=4\n')
        self.assertEqual(child['decision'], 'not_sealed')
        self.assertEqual(child['reason'], 'relay_unconfirmed')
        snap = checkpoint(scenario, 'relay_lost_child_still_writing')
        self.assertEqual(
            snap['artifacts']['parent_download']['decision'], 'not_sealed')
        self.assertEqual(
            snap['artifacts']['child_download']['decision'], 'not_sealed')

    def test_mid_record_relay_waits_for_newline_then_hands_over(self):
        scenario = find(self.result, 'relay_requested_mid_record')
        # A relay requested while r2 is mid-line is deferred, not applied at once.
        parent = scenario['artifacts']['parent_download']
        child = scenario['artifacts']['child_download']
        self.assertEqual(parent['text'], 'worker=3\n')
        self.assertEqual(parent['records'], ['r2'])
        self.assertEqual(child['text'], 'next=4\n')
        self.assertEqual(child['records'], ['r3'])
        held = checkpoint(scenario, 'line_in_progress_relay_held')
        self.assertEqual(held['artifacts']['child_download']['reason'],
                         'waiting_relay_boundary')
        # r2 is still open, so the parent cannot seal either; the relay has
        # not transferred ownership of the in-progress line.
        self.assertEqual(held['artifacts']['parent_download']['reason'],
                         'record_incomplete')

    def test_cross_artifact_fragment_is_rejected_not_glued(self):
        scenario = find(self.result, 'cross_artifact_fragment_rejected')
        self.assertTrue(any(r['reason'] == 'cross_artifact_fragment'
                            for r in scenario['rejections']))
        # While the record is open it cannot ship in any download.
        held = checkpoint(scenario, 'fragment_refused_record_open')
        self.assertEqual(held['artifacts']['parent_download']['reason'],
                         'record_incomplete')
        # The valid replacement completes r2 on the parent; the forged bytes
        # never reach either download, and records are never split.
        parent = scenario['artifacts']['parent_download']
        child = scenario['artifacts']['child_download']
        self.assertEqual(parent['text'], 'parent=2\nworker=3\n')
        self.assertEqual(parent['records'], ['r1', 'r2'])
        self.assertEqual(child['text'], 'next=4\n')
        self.assertEqual(child['records'], ['r3'])

    def test_reused_pid_and_pipe_name_cannot_touch_job_artifact(self):
        scenario = find(self.result, 'pid_and_pipe_name_reuse_by_unrelated_job')
        artifact = scenario['artifacts']['parent_download']
        self.assertEqual(artifact['text'], 'parent=2\nworker=3\n')
        self.assertEqual(artifact['records'], ['r1', 'r2'])
        reasons = {r['reason'] for r in scenario['rejections']}
        self.assertIn('no_write_capability', reasons)
        self.assertIn('unowned_generation', reasons)

    def test_lost_close_ack_without_copy_still_seals_after_restart(self):
        scenario = find(self.result, 'close_acknowledgement_lost_no_inherited_copy')
        artifact = scenario['artifacts']['parent_download']
        self.assertEqual(artifact['decision'], 'sealed')
        self.assertEqual(artifact['text'], 'parent=2\n')
        self.assertEqual(scenario['restarts'], 1)
        self.assertEqual(scenario['rejections'], [])

    def test_inherited_copy_distinguished_from_lost_close_acknowledgement(self):
        scenario = find(self.result, 'inherited_copy_still_writing_blocks_seal')
        snap = checkpoint(scenario, 'parent_exited_inherited_handle_open')
        artifact = snap['artifacts']['parent_download']
        self.assertEqual(artifact['decision'], 'not_sealed')
        self.assertEqual(artifact['reason'], 'waiting_writer')
        self.assertEqual(artifact['open_handles'], ['h2'])
        # Late bytes honoring the original promise still land on the real job.
        sealed = scenario['artifacts']['parent_download']
        self.assertEqual(sealed['text'], 'parent=2\nworker=3\n')
        self.assertEqual(scenario['restarts'], 1)


class HandoffExecutorDirectTests(unittest.TestCase):
    def scenario(self):
        return {'id': 'direct', 'job': 'job-1',
                'consumers': [{'id': 'dl', 'policy': 'job_tree',
                               'job': 'job-1', 'generation': 'g0'}]}

    def test_late_bytes_from_old_writer_still_belong_to_job(self):
        ex = output_handoff.HandoffExecutor(self.scenario())
        events = [
            {'op': 'start', 'proc': 'p1', 'pid': 1, 'job': 'job-1'},
            {'op': 'open', 'proc': 'p1', 'handle': 'h1', 'pipe': 'p',
             'generation': 'g0', 'job': 'job-1'},
            {'op': 'write', 'proc': 'p1', 'handle': 'h1', 'generation': 'g0',
             'fragment': 'f1', 'record': 'r1', 'seq': 1, 'text': 'a\n'},
            {'op': 'start', 'proc': 'p2', 'pid': 2, 'job': 'job-1', 'ppid': 'p1'},
            {'op': 'inherit', 'handle': 'h1', 'from': 'p1', 'to': 'p2', 'as': 'h2'},
            {'op': 'exit', 'proc': 'p1'},
            # Late write on the inherited copy honoring the original promise.
            {'op': 'write', 'proc': 'p2', 'handle': 'h2', 'generation': 'g0',
             'fragment': 'f2', 'record': 'r2', 'seq': 1, 'text': 'b\n'},
            {'op': 'exit', 'proc': 'p2'},
        ]
        trace = ex.run_events(events)
        self.assertEqual(ex.claim('dl')['text'], 'a\nb\n')
        self.assertTrue(all(t['decision'] != 'no_write_capability' for t in trace))

    def test_bytes_after_seal_are_rejected_and_artifact_unchanged(self):
        ex = output_handoff.HandoffExecutor(self.scenario())
        ex.run_events([
            {'op': 'start', 'proc': 'p1', 'pid': 1, 'job': 'job-1'},
            {'op': 'open', 'proc': 'p1', 'handle': 'h1', 'pipe': 'p',
             'generation': 'g0', 'job': 'job-1'},
            {'op': 'write', 'proc': 'p1', 'handle': 'h1', 'generation': 'g0',
             'fragment': 'f1', 'record': 'r1', 'seq': 1, 'text': 'a\n'},
            {'op': 'close', 'proc': 'p1', 'handle': 'h1'},
            {'op': 'exit', 'proc': 'p1'},
            # A later process opens a brand new handle on the same generation.
            {'op': 'start', 'proc': 'p3', 'pid': 3, 'job': 'job-1'},
            {'op': 'open', 'proc': 'p3', 'handle': 'h3', 'pipe': 'p',
             'generation': 'g0', 'job': 'job-1'},
        ])
        sealed = ex.claim('dl')
        self.assertEqual(sealed['decision'], 'sealed')
        decision = ex.run_events([
            {'op': 'write', 'proc': 'p3', 'handle': 'h3', 'generation': 'g0',
             'fragment': 'f2', 'record': 'r2', 'seq': 1, 'text': 'late\n'}])
        self.assertEqual(decision[0]['decision'], 'artifact_immutable')
        again = ex.claim('dl')
        self.assertEqual(again['digest'], sealed['digest'])
        self.assertEqual(again['text'], 'a\n')


if __name__ == '__main__':
    unittest.main()
