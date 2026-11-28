import json
import subprocess
import sys
import unittest
from copy import deepcopy
from pathlib import Path
from admissioncourt import diagnose

IMAGES = {'q-hotfix': {'status': 200, 'revision': 'v1.1'}}


def fixture(name):
    return json.loads((Path('fixtures') / f'{name}.json').read_text(encoding='utf8'))


def by_world_and_team(result):
    return {(entry['world'], entry['team']): entry for entry in result['evaluations']}


def replica(status, revision, service='job', swappable=True):
    return {'state': 'healthy', 'status': status, 'revision': revision,
            'service': service, 'swappable': swappable}


def make_world(name, q, p, **extra):
    return {'name': name, 'epoch': 1,
            'routes': [{'id': 'service-a', 'prefix': '/job', 'service': 'job',
                        'backends': ['Q', 'P']}],
            'replicas': {'Q': q, 'P': p}, **extra}


class NormalFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.result = diagnose.run(fixture('diagnose_normal'))
        cls.entries = by_world_and_team(cls.result)

    def test_routing_team_gets_cutover_order_to_standby(self):
        entry = self.entries[('D', 'routing')]
        self.assertEqual(entry['decision'], 'issue_order')
        order = entry['order']
        self.assertEqual(order['team'], 'routing')
        self.assertEqual(order['action'],
                         {'op': 'shift_route', 'rule': 'service-a', 'backend': 'P'})
        verification = order['verification']
        self.assertEqual(verification['before']['status'], 500)
        self.assertEqual(verification['after']['status'], 200)
        self.assertEqual(verification['serving'], 'P')

    def test_image_team_gets_in_place_swap_order(self):
        entry = self.entries[('D', 'image')]
        self.assertEqual(entry['decision'], 'issue_order')
        order = entry['order']
        self.assertEqual(order['team'], 'image')
        self.assertEqual(order['action'], {'op': 'swap_image', 'replica': 'Q',
                                           'image': 'q-hotfix', 'service': 'job'})
        verification = order['verification']
        self.assertEqual(verification['after']['status'], 200)
        self.assertEqual(verification['serving'], 'Q')

    def test_verification_replays_the_original_baseline(self):
        verification = self.entries[('D', 'routing')]['order']['verification']
        self.assertEqual(verification['before']['backend'], 'Q')
        self.assertEqual(verification['before']['epoch'], 1)
        self.assertEqual(verification['after']['epoch'], 1)
        self.assertEqual(verification['violations'], [])

    def test_rebound_label_refuses_in_place_swap_on_foreign_q(self):
        entry = self.entries[('R', 'image')]
        self.assertEqual(entry['decision'], 'refused')
        self.assertEqual(entry['witness']['kind'], 'intervention_refused')
        self.assertEqual(entry['witness']['reason'], 'foreign_service_replica')

    def test_sick_standby_refuses_invalid_cutover(self):
        entry = self.entries[('I', 'routing')]
        self.assertEqual(entry['decision'], 'refused')
        self.assertEqual(entry['witness']['kind'], 'not_restored')
        self.assertEqual(entry['witness']['after']['status'], 500)

    def test_cutover_order_never_touches_q_service(self):
        entry = self.entries[('R', 'routing')]
        self.assertEqual(entry['decision'], 'issue_order')
        self.assertEqual(entry['order']['action']['op'], 'shift_route')

    def test_image_order_ignores_sick_standby(self):
        entry = self.entries[('I', 'image')]
        self.assertEqual(entry['decision'], 'issue_order')
        self.assertEqual(entry['order']['verification']['serving'], 'Q')

    def test_same_observation_worlds_are_excluded_by_evidence(self):
        routing = self.entries[('D', 'routing')]['evidence']
        self.assertEqual([item['world'] for item in routing['excluded']], ['I'])
        self.assertEqual(routing['excluded'][0]['after']['status'], 500)
        self.assertEqual(routing['consistent'], ['R'])
        image = self.entries[('D', 'image')]['evidence']
        self.assertEqual([item['world'] for item in image['excluded']], ['R'])
        self.assertEqual(image['excluded'][0]['outcome'], 'refused')
        self.assertEqual(image['consistent'], ['I'])


class AmbiguousFixtureTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.entries = by_world_and_team(diagnose.run(fixture('diagnose_ambiguous')))

    def test_indistinguishable_pair_stays_unattributed(self):
        entry = self.entries[('A', 'routing')]
        self.assertEqual(entry['decision'], 'unattributed')
        witness = entry['witness']
        self.assertEqual(witness['kind'], 'indistinguishable_worlds')
        self.assertEqual(witness['worlds'], ['A', 'B'])
        self.assertEqual(witness['repair']['op'], 'shift_route')
        self.assertEqual(witness['violations'][0]['kind'], 'foreign_serving_backend')
        self.assertEqual(witness['shared_observations']['after']['status'], 200)

    def test_image_team_testimony_is_robust_to_the_same_pair(self):
        self.assertEqual(self.entries[('A', 'image')]['decision'], 'issue_order')
        self.assertEqual(self.entries[('B', 'image')]['decision'], 'issue_order')

    def test_foreign_standby_world_refuses_its_own_cutover(self):
        entry = self.entries[('B', 'routing')]
        self.assertEqual(entry['decision'], 'refused')
        self.assertEqual(entry['witness']['kind'], 'verification_failed')
        self.assertEqual(entry['witness']['violations'][0]['kind'],
                         'foreign_serving_backend')


class EpochFixtureTests(unittest.TestCase):
    def test_generation_crossing_stays_unattributed_despite_observed_200(self):
        entries = by_world_and_team(diagnose.run(fixture('diagnose_epoch')))
        for team in ('routing', 'image'):
            entry = entries[('E', team)]
            self.assertEqual(entry['decision'], 'unattributed')
            self.assertEqual(entry['witness']['kind'], 'epoch_crossing')
            self.assertEqual((entry['witness']['before_epoch'],
                              entry['witness']['after_epoch']), (1, 2))
            self.assertEqual(entry['evidence']['after']['status'], 200)

    def test_identical_status_across_epochs_forms_no_causal_pair(self):
        world = make_world('E2', replica(500, 'v1'), replica(500, 'v2'),
                           rotate_after_reads=1)
        plan = {'symptom': {'path': '/job/x', 'status': 500}, 'images': IMAGES,
                'worlds': [world],
                'authorizations': [{'team': 'routing', 'op': 'shift_route'}]}
        (entry,) = diagnose.run(plan)['evaluations']
        self.assertEqual(entry['decision'], 'unattributed')
        self.assertEqual(entry['witness']['kind'], 'epoch_crossing')
        self.assertEqual(entry['evidence']['after']['status'], 500)


class InstanceTests(unittest.TestCase):
    def test_budget_allows_a_single_state_changing_intervention(self):
        instance = diagnose.Instance(make_world('D', replica(500, 'v1'),
                                                replica(200, 'v2')), IMAGES)
        action = {'op': 'shift_route', 'rule': 'service-a', 'backend': 'P'}
        self.assertEqual(instance.intervene(action)['decision'], 'applied')
        self.assertEqual(instance.intervene(action),
                         {'decision': 'refused', 'reason': 'budget_exhausted'})

    def test_read_only_calls_do_not_spend_budget(self):
        instance = diagnose.Instance(make_world('D', replica(500, 'v1'),
                                                replica(200, 'v2')), IMAGES)
        for _ in range(3):
            self.assertEqual(instance.observe('/job/x')['status'], 500)
        action = {'op': 'shift_route', 'rule': 'service-a', 'backend': 'P'}
        self.assertEqual(instance.intervene(action)['decision'], 'applied')
        self.assertEqual(instance.observe('/job/x')['status'], 200)

    def test_refused_intervention_keeps_budget_and_state(self):
        world = make_world('R', replica(500, 'v1', service='billing', swappable=False),
                           replica(200, 'v2'))
        instance = diagnose.Instance(world, IMAGES)
        swap = {'op': 'swap_image', 'replica': 'Q', 'image': 'q-hotfix', 'service': 'job'}
        self.assertEqual(instance.intervene(swap),
                         {'decision': 'refused', 'reason': 'foreign_service_replica'})
        shift = {'op': 'shift_route', 'rule': 'service-a', 'backend': 'P'}
        self.assertEqual(instance.intervene(shift)['decision'], 'applied')

    def test_unknown_operations_are_read_only(self):
        instance = diagnose.Instance(make_world('D', replica(500, 'v1'),
                                                replica(200, 'v2')), IMAGES)
        before = deepcopy(instance.world)
        self.assertEqual(instance.intervene({'op': 'restart', 'replica': 'Q'}),
                         {'decision': 'refused', 'reason': 'unknown_op'})
        self.assertEqual(instance.world, before)

    def test_health_record_rotation_advances_the_epoch(self):
        world = make_world('E', replica(500, 'v1'), replica(200, 'v2'),
                           rotate_after_reads=1)
        instance = diagnose.Instance(world, IMAGES)
        self.assertEqual(instance.observe('/job/x')['epoch'], 1)
        self.assertEqual(instance.observe('/job/x')['epoch'], 2)


class SymptomRelevanceTests(unittest.TestCase):
    def test_witnesses_only_cover_the_symptom_path(self):
        extra_route = {'id': 'other', 'prefix': '/other', 'service': 'other',
                       'backends': ['X']}
        worlds = []
        for name, x_status in (('W1', 200), ('W2', 500)):
            world = make_world(name, replica(500, 'v1'), replica(200, 'v2'))
            world['routes'].append(extra_route)
            world['replicas']['X'] = replica(x_status, 'x1', service='other')
            worlds.append(world)
        plan = {'symptom': {'path': '/job/x', 'status': 500}, 'images': IMAGES,
                'worlds': worlds,
                'authorizations': [{'team': 'routing', 'op': 'shift_route'},
                                   {'team': 'image', 'op': 'swap_image',
                                    'image': 'q-hotfix'}]}
        result = diagnose.run(plan)
        for entry in result['evaluations']:
            self.assertEqual(entry['decision'], 'issue_order')
        self.assertNotIn('/other', json.dumps(result))


class CliTests(unittest.TestCase):
    def run_cli(self, *args):
        proc = subprocess.run([sys.executable, '-B', '-m', 'admissioncourt', *args],
                              capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        return json.loads(proc.stdout)

    def test_drill_baseline_is_unchanged(self):
        result = self.run_cli('drill', '--fixture', 'fixtures/drill_normal.json')
        self.assertEqual(result['counts'], {'200': 4, '503': 1})

    def test_diagnose_command_delivers_both_orders(self):
        result = self.run_cli('diagnose', '--fixture', 'fixtures/diagnose_normal.json')
        self.assertEqual(result['decision'], 'diagnosed')
        entries = by_world_and_team(result)
        self.assertEqual(entries[('D', 'routing')]['order']['action']['backend'], 'P')
        self.assertEqual(entries[('D', 'image')]['order']['action']['replica'], 'Q')
        self.assertEqual(entries[('R', 'image')]['decision'], 'refused')
        self.assertEqual(entries[('I', 'routing')]['decision'], 'refused')


if __name__ == '__main__':
    unittest.main()
