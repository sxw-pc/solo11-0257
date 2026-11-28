import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from admissioncourt import assembly, build_compare, handover_build
from admissioncourt.admit import hash_value

FIXTURES = Path('fixtures').resolve()
RUNTIME = Path(__file__).resolve().parents[1]


def load(name):
    return json.loads((FIXTURES / name).read_text(encoding='utf-8'))


def swap_aliases(fixture):
    """Exchange the two equal-byte source roles, keeping every initial value.

    ``left`` keeps the bytes that ``right`` had and vice versa, so the
    handover-day artifact is unchanged while the provenance roles move.
    """
    swapped = copy.deepcopy(fixture)
    sources = swapped['sources']
    sources['left'], sources['right'] = sources['right'], sources['left']
    return swapped


class HandoverTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pkg = Path(self.tmp.name) / 'pkg'
        self.fixture = load('handover_transfer_normal.json')

    def test_both_independent_edits_rebuild_to_owner_truth(self):
        result = handover_build.build_package(self.fixture, self.pkg)
        self.assertEqual(result['decision'], 'packaged')
        self.assertEqual(result['maintainable_roles'],
                         ['theme', 'left', 'right'])
        for edit_id in ('edit-right', 'edit-left'):
            consumed = handover_build.consume_package(
                self.pkg, edit_id=edit_id)
            self.assertEqual(consumed['decision'], 'delivered')
            self.assertTrue(consumed['matches_owner_truth'])
            owner = handover_build._owner_build(
                self.fixture,
                next(e for e in self.fixture['edits']
                     if e['id'] == edit_id)['changes'])
            self.assertEqual(consumed['artifact'], owner['artifact'])

    def test_package_owns_full_tool_chain_and_sources(self):
        handover_build.build_package(self.fixture, self.pkg)
        names = {p.name for p in (self.pkg / 'tools').iterdir()}
        self.assertEqual(names, {'common.json', 'markup.json'})
        source_names = {p.name for p in (self.pkg / 'sources').iterdir()}
        self.assertEqual(source_names, {'theme.txt', 'left.txt',
                                        'right.txt'})
        self.assertFalse((self.pkg / 'sealed').exists())

    def test_baseline_rebuild_matches_handover_day_artifact(self):
        handover_build.build_package(self.fixture, self.pkg)
        baseline = handover_build.consume_package(self.pkg)
        original = assembly.run(self.fixture)
        self.assertEqual(baseline['artifact'], original['artifact'])
        self.assertTrue(baseline['matches_owner_truth'])


class HandoverIntermediateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.pkg = Path(self.tmp.name) / 'pkg'
        self.fixture = load('handover_intermediate_normal.json')

    def test_right_edit_delivered_left_edit_returns_rebuild_request(self):
        result = handover_build.build_package(self.fixture, self.pkg)
        self.assertEqual(result['decision'], 'packaged')
        self.assertEqual(result['maintainable_roles'], ['right'])
        self.assertEqual(result['blocked_roles'], ['theme', 'left'])

        right = handover_build.consume_package(self.pkg, edit_id='edit-right')
        self.assertEqual(right['decision'], 'delivered')
        self.assertTrue(right['matches_owner_truth'])
        owner_right = handover_build._owner_build(
            self.fixture,
            next(e for e in self.fixture['edits']
                 if e['id'] == 'edit-right')['changes'])
        self.assertEqual(right['artifact'], owner_right['artifact'])

        left = handover_build.consume_package(self.pkg, edit_id='edit-left')
        self.assertEqual(left['decision'], 'rebuild_request')
        request = left['request']
        self.assertEqual(request['kind'], 'missing_right')
        self.assertEqual(request['sealed_step'], 'markup-step')
        self.assertEqual(request['missing_tool']['name'], 'markup')
        self.assertIn('left', request['changed_input_roles'])

    def test_restricted_tool_description_and_sources_never_copied(self):
        handover_build.build_package(self.fixture, self.pkg)
        # The markup separator must appear nowhere but nowhere usable.
        for path in self.pkg.rglob('*'):
            if path.is_file() and path.name != 'manifest.json':
                self.assertNotIn('"separator": "|"',
                                 path.read_text(encoding='utf-8'))
        self.assertFalse((self.pkg / 'tools' / 'markup.json').exists())
        self.assertFalse((self.pkg / 'sources' / 'left.txt').exists())
        self.assertFalse((self.pkg / 'sources' / 'theme.txt').exists())
        # Restricted edit truth is digest-only: no full artifact for left.
        self.assertFalse(
            (self.pkg / 'truth' / 'edit-edit-left.artifact').exists())
        manifest = json.loads(
            (self.pkg / 'manifest.json').read_text(encoding='utf-8'))
        self.assertNotIn('path', manifest['truth']['edit-left'])
        self.assertIn('digest', manifest['truth']['edit-left'])

    def test_sealed_intermediate_carries_real_provenance(self):
        original = assembly.run(self.fixture)
        handover_build.build_package(self.fixture, self.pkg)
        manifest = json.loads(
            (self.pkg / 'manifest.json').read_text(encoding='utf-8'))
        sealed = manifest['sealed']['markup-step']
        captured = {}
        assembly.run(self.fixture, captured)
        self.assertEqual(sealed['output_digest'],
                         hash_value(captured['stage-x']))
        self.assertEqual(
            (self.pkg / sealed['path']).read_text(encoding='utf-8'),
            captured['stage-x'])
        self.assertEqual(sealed['input_roles'], ['theme', 'left'])
        self.assertEqual(sealed['tool']['name'], 'markup')
        self.assertEqual(sealed['tool']['edition'], 1)
        # Baseline still rebuilds through the sealed intermediate.
        baseline = handover_build.consume_package(self.pkg)
        self.assertEqual(baseline['artifact'], original['artifact'])

    def test_same_value_edit_still_rebuilds_and_does_not_reuse_cache(self):
        # right is set to bytes equal to another present input ('same').
        # The receiver must still run prepare-right for real and must not
        # treat equal content as a cacheable sealed output.
        handover_build.build_package(self.fixture, self.pkg)
        same_value = handover_build.consume_package(
            self.pkg, overrides={'right': self.fixture['sources']['left']})
        self.assertEqual(same_value['decision'], 'delivered')
        rebuilt_steps = {row['step'] for row in same_value['trace']}
        self.assertIn('prepare-right', rebuilt_steps)
        self.assertNotIn('markup-step', rebuilt_steps)
        owner = handover_build._owner_build(
            self.fixture, [{'role': 'right',
                            'value': self.fixture['sources']['left']}])
        self.assertEqual(same_value['artifact'], owner['artifact'])

    def test_alias_swap_keeps_artifact_but_moves_maintenance_boundary(self):
        swapped = swap_aliases(self.fixture)
        # Initial artifacts are byte-identical.
        self.assertEqual(assembly.run(swapped)['artifact'],
                         assembly.run(self.fixture)['artifact'])
        swapped_pkg = Path(self.tmp.name) / 'swapped'
        result = handover_build.build_package(swapped, swapped_pkg)
        self.assertEqual(result['decision'], 'packaged')
        # The editable role is still the role named right, whatever bytes it
        # holds; left remains sealed by provenance, not by content.
        self.assertEqual(result['maintainable_roles'], ['right'])
        edit_left = handover_build.consume_package(
            swapped_pkg, overrides={'left': 'next'})
        self.assertEqual(edit_left['decision'], 'rebuild_request')
        edit_right = handover_build.consume_package(
            swapped_pkg, overrides={'right': 'next'})
        self.assertEqual(edit_right['decision'], 'delivered')
        owner = handover_build._owner_build(
            swapped, [{'role': 'right', 'value': 'next'}])
        self.assertEqual(edit_right['artifact'], owner['artifact'])

    def test_package_without_serviceable_edit_is_rejected_and_removed(self):
        broken = load('handover_intermediate_failure.json')
        target = Path(self.tmp.name) / 'broken'
        result = handover_build.build_package(broken, target)
        self.assertEqual(result['decision'], 'no_independent_maintenance')
        self.assertFalse(target.exists())

    def test_sealed_provenance_names_exact_missing_inputs_and_digests(self):
        # A sealed step that reads a derived, packaged role (stage-y is built
        # from right by the common tool before markup seals it).  The request
        # names the exact replaced role and the owner's expected digests.
        fixture = copy.deepcopy(self.fixture)
        fixture['recipe'] = [
            {'id': 'prepare-right', 'tool': 'common', 'operation': 'join',
             'inputs': ['right'], 'output': 'stage-y'},
            {'id': 'markup-step', 'tool': 'markup', 'operation': 'join',
             'inputs': ['theme', 'stage-y'], 'output': 'stage-x'},
            {'id': 'merge', 'tool': 'common', 'operation': 'join',
             'inputs': ['stage-x', 'left'], 'output': 'page'},
        ]
        fixture['edits'] = [
            {'id': 'edit-left', 'changes': [{'role': 'left',
                                             'value': 'next'}]},
        ]
        pkg = Path(self.tmp.name) / 'drift'
        result = handover_build.build_package(fixture, pkg)
        self.assertEqual(result['decision'], 'packaged')
        manifest = json.loads(
            (pkg / 'manifest.json').read_text(encoding='utf-8'))
        sealed = manifest['sealed']['markup-step']
        self.assertEqual(sealed['input_roles'], ['theme', 'stage-y'])
        self.assertIn('stage-y', manifest['sources'])

        # Replacing the derived sealed input returns the precise request.
        drifted = handover_build.consume_package(
            pkg, overrides={'stage-y': 'forged-bytes'})
        self.assertEqual(drifted['decision'], 'rebuild_request')
        request = drifted['request']
        self.assertEqual(request['kind'], 'missing_right')
        self.assertEqual(request['sealed_step'], 'markup-step')
        self.assertEqual(request['changed_input_roles'], ['stage-y'])
        self.assertEqual(request['missing_tool']['name'], 'markup')
        self.assertEqual(
            set(request['expected_input_digests']), {'theme', 'stage-y'})

        # Direct file forgery of the same packaged material is caught earlier
        # by the package's own digest verification.
        target = pkg / manifest['sources']['stage-y']['path']
        target.write_text('forged-bytes', encoding='utf-8')
        self.assertEqual(
            handover_build.consume_package(pkg)['decision'],
            'material_tampered')


class HandoverToolVersionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.fixture = load('handover_transfer_normal.json')
        self.pkg = Path(self.tmp.name) / 'pkg'
        handover_build.build_package(self.fixture, self.pkg)

    def test_engine_flips_when_markup_separator_changes(self):
        changed = copy.deepcopy(self.fixture)
        for provider in changed['catalog']['providers']:
            if provider['name'] == 'markup':
                provider['exports']['join']['separator'] = ':'
        pair = {'reference': self.fixture, 'observed': changed}
        self.assertEqual(build_compare.run(pair)['decision'], 'different')
        self.assertNotEqual(assembly.run(self.fixture)['artifact'],
                            assembly.run(changed)['artifact'])

    def test_same_named_tool_edition_swap_is_detected(self):
        tool_path = self.pkg / 'tools' / 'markup.json'
        tool = json.loads(tool_path.read_text(encoding='utf-8'))
        # Same export bytes (same digest), but the edition mapping changes:
        # identical summary must never be accepted as the same tool identity.
        tool['edition'] = 2
        tool_path.write_text(json.dumps(tool, sort_keys=True),
                             encoding='utf-8')
        result = handover_build.consume_package(self.pkg)
        self.assertEqual(result['decision'], 'material_tampered')
        self.assertEqual(result['tool'], 'markup')
        manifest = json.loads(
            (self.pkg / 'manifest.json').read_text(encoding='utf-8'))
        self.assertEqual(manifest['tools']['markup']['edition'], 1)
        self.assertEqual(
            manifest['tools']['markup']['digest'],
            handover_build._tool_identity(tool)['digest'])

    def test_unavailable_tool_at_build_time_fails_build(self):
        broken = copy.deepcopy(self.fixture)
        for provider in broken['catalog']['providers']:
            if provider['name'] == 'markup':
                provider['available'] = False
        target = Path(self.tmp.name) / 'missing'
        result = handover_build.build_package(broken, target)
        self.assertEqual(result['decision'], 'tool_missing')
        self.assertFalse(target.exists())

    def test_changed_input_cannot_be_backfilled_from_sealed_cache(self):
        inter = load('handover_intermediate_normal.json')
        pkg = Path(self.tmp.name) / 'inter'
        handover_build.build_package(inter, pkg)
        # Editing right to the exact bytes of sealed stage-x must not unlock
        # anything: roles are distinct and sealed provenance still binds left.
        captured = {}
        assembly.run(inter, captured)
        forged = handover_build.consume_package(
            pkg, overrides={'right': captured['stage-x']})
        self.assertEqual(forged['decision'], 'delivered')
        owner = handover_build._owner_build(
            inter, [{'role': 'right', 'value': captured['stage-x']}])
        self.assertEqual(forged['artifact'], owner['artifact'])
        sealed_left = handover_build.consume_package(
            pkg, overrides={'left': captured['stage-x']})
        self.assertEqual(sealed_left['decision'], 'rebuild_request')


class HandoverCliTests(unittest.TestCase):
    def run_cli(self, *args, cwd):
        env = dict(os.environ, PYTHONPATH=str(RUNTIME))
        return subprocess.run(
            [sys.executable, '-B', '-m', 'admissioncourt', *args],
            cwd=cwd, env=env, text=True, capture_output=True, check=False)

    def test_offline_cli_build_and_consume_in_empty_directory(self):
        with tempfile.TemporaryDirectory() as workspace:
            workspace = Path(workspace)
            # Receiver starts with none of the owner's fixture files.
            self.assertEqual(list(workspace.iterdir()), [])
            for fixture_name, package in (
                    ('handover_transfer_normal.json', 'transfer'),
                    ('handover_intermediate_normal.json', 'intermediate')):
                target = workspace / package
                build = self.run_cli(
                    'handover-build',
                    '--fixture', str(FIXTURES / fixture_name),
                    '--package', str(target), cwd=workspace)
                self.assertEqual(build.returncode, 0, build.stderr)
                self.assertIn('"packaged"', build.stdout)

                right = self.run_cli(
                    'handover-consume', '--package', str(target),
                    '--edit', 'edit-right', cwd=workspace)
                self.assertEqual(right.returncode, 0, right.stderr)
                self.assertIn('"delivered"', right.stdout)
                self.assertIn('"matches_owner_truth": true', right.stdout)

                left = self.run_cli(
                    'handover-consume', '--package', str(target),
                    '--edit', 'edit-left', cwd=workspace)
                if package == 'transfer':
                    self.assertIn('"delivered"', left.stdout)
                    self.assertIn('"matches_owner_truth": true', left.stdout)
                else:
                    self.assertIn('"rebuild_request"', left.stdout)
                    self.assertIn('"missing_right"', left.stdout)


if __name__ == '__main__':
    unittest.main()
