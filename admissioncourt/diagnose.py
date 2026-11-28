"""Attribute an executable repair order for a synthetic drill symptom.

The operator only gets an online-style first read (path and status).  This
module replays the symptom inside isolated drill instances, spends the single
sanctioned state-changing intervention, and either issues a repair order for
one team or stays unattributed with the witness that blocks attribution.
An intervention may shift one routing priority or load a known-good drill
image for one replica; every other action is read-only.  Issued orders are
verified on another instance branched from the original state, never on the
post-intervention state, and reads from two health-record generations never
form a same-baseline comparison.
"""
from copy import deepcopy
from . import drill, router

SUCCESS = 200


def online_view(observed):
    """The online-style read view: path and status only."""
    return {'path': observed['path'], 'status': observed['status']}


def drill_view(observed):
    """The drill observation: backend and image revision on top of the view."""
    return {key: observed[key]
            for key in ('path', 'status', 'backend', 'revision', 'epoch')}


def signature(outcome, observed):
    """Everything the operator could tell apart after one intervention."""
    result = {'outcome': outcome['decision'], **drill_view(observed)}
    if outcome['decision'] != 'applied':
        result['reason'] = outcome['reason']
    return result


class Instance:
    """One isolated synthetic instance branched from a world state."""

    def __init__(self, world, images, budget=1):
        self.world = deepcopy(world)
        self.images = images
        self.budget = budget
        self.calls = 0

    def observe(self, path):
        """Read-only drill observation tagged with the health-record epoch."""
        observed = drill.query(self.world['routes'], self.world['replicas'], path)
        view = {**observed, 'epoch': self.world.get('epoch', 0)}
        self.calls += 1
        every = self.world.get('rotate_after_reads')
        if every and self.calls % every == 0:
            self.world['epoch'] = self.world.get('epoch', 0) + 1
        return view

    def intervene(self, action):
        """Spend the single state-changing intervention of this instance."""
        if self.budget < 1:
            return {'decision': 'refused', 'reason': 'budget_exhausted'}
        if action.get('op') == 'shift_route':
            outcome = self._shift(action)
        elif action.get('op') == 'swap_image':
            outcome = self._swap(action)
        else:
            outcome = {'decision': 'refused', 'reason': 'unknown_op'}
        if outcome['decision'] == 'applied':
            self.budget -= 1
        return outcome

    def _shift(self, action):
        backend = action.get('backend')
        for rule in self.world['routes']:
            if rule['id'] == action.get('rule'):
                if backend not in rule['backends']:
                    return {'decision': 'refused', 'reason': 'unknown_backend'}
                rule['backends'] = [backend] + [item for item in rule['backends']
                                                if item != backend]
                return {'decision': 'applied'}
        return {'decision': 'refused', 'reason': 'unknown_rule'}

    def _swap(self, action):
        replica = self.world['replicas'].get(action.get('replica'))
        image = self.images.get(action.get('image'))
        if replica is None:
            return {'decision': 'refused', 'reason': 'unknown_replica'}
        if image is None:
            return {'decision': 'refused', 'reason': 'unknown_image'}
        if replica.get('service') != action.get('service'):
            return {'decision': 'refused', 'reason': 'foreign_service_replica'}
        if not replica.get('swappable', True):
            return {'decision': 'refused', 'reason': 'replica_not_swappable'}
        replica.update(deepcopy(image))
        return {'decision': 'applied'}


def matched_rule(world, path):
    health = {name: replica['state'] for name, replica in world['replicas'].items()}
    selection = router.winner(world['routes'], health, path)
    for rule in world['routes']:
        if rule['id'] == selection['rule']:
            return rule, selection['backend']
    return None, None


def plan_action(auth, world, path):
    """Derive the single sanctioned intervention for an authorization."""
    rule, selected = matched_rule(world, path)
    if rule is None or selected is None:
        return None
    if auth['op'] == 'shift_route':
        standby = next((backend for backend in rule['backends']
                        if backend != selected
                        and world['replicas'].get(backend, {}).get('state') == 'healthy'),
                       None)
        if standby is None:
            return None
        return {'op': 'shift_route', 'rule': rule['id'], 'backend': standby}
    if auth['op'] == 'swap_image' and auth.get('image'):
        return {'op': 'swap_image', 'replica': selected,
                'image': auth['image'], 'service': rule.get('service')}
    return None


def verify_repair(world, images, symptom, action):
    """Replay a repair on another instance branched from the original state."""
    instance = Instance(world, images)
    path = symptom['path']
    before = instance.observe(path)
    routes = deepcopy(instance.world['routes'])
    replicas = deepcopy(instance.world['replicas'])
    outcome = instance.intervene(action)
    after = instance.observe(path)
    violations = []
    if before['status'] != symptom['status']:
        violations.append({'kind': 'baseline_mismatch', 'status': before['status']})
    if before['epoch'] != after['epoch']:
        violations.append({'kind': 'epoch_crossing', 'before_epoch': before['epoch'],
                           'after_epoch': after['epoch']})
    if outcome['decision'] != 'applied':
        violations.append({'kind': 'intervention_refused', 'reason': outcome['reason']})
    if after['status'] != SUCCESS:
        violations.append({'kind': 'not_restored', 'status': after['status']})
    if action['op'] == 'shift_route' and instance.world['replicas'] != replicas:
        violations.append({'kind': 'replica_touched'})
    if action['op'] == 'swap_image':
        if instance.world['routes'] != routes:
            violations.append({'kind': 'routing_changed'})
        if after['backend'] != before['backend']:
            violations.append({'kind': 'traffic_moved', 'backend': after['backend']})
    rule, _ = matched_rule(instance.world, path)
    serving = instance.world['replicas'].get(after['backend'], {})
    if rule is not None and after['backend'] is not None \
            and rule.get('service') is not None \
            and serving.get('service') != rule['service']:
        violations.append({'kind': 'foreign_serving_backend', 'backend': after['backend']})
    return {'before': drill_view(before), 'after': drill_view(after),
            'serving': after['backend'], 'violations': violations}


def evaluate(fixture, world, auth, candidates):
    """Deliver or refuse one authorization's repair order for one world."""
    symptom = fixture['symptom']
    images = fixture.get('images', {})
    path = symptom['path']
    instance = Instance(world, images)
    first = instance.observe(path)
    evidence = {'first_read': online_view(first)}
    base = {'world': world['name'], 'team': auth['team'], 'evidence': evidence}
    if first['status'] != symptom['status']:
        return {**base, 'decision': 'symptom_mismatch'}
    action = plan_action(auth, instance.world, path)
    if action is None:
        return {**base, 'decision': 'no_sanctioned_plan'}
    outcome = instance.intervene(action)
    after = instance.observe(path)
    evidence['intervention'] = {'action': action, **outcome}
    evidence['after'] = drill_view(after)
    if first['epoch'] != after['epoch']:
        return {**base, 'decision': 'unattributed', 'reason': 'epoch_crossing',
                'witness': {'kind': 'epoch_crossing', 'before_epoch': first['epoch'],
                            'after_epoch': after['epoch'],
                            'note': 'health records moved to the next generation; reads from '
                                    'two epochs cannot form a same-baseline comparison'}}
    if outcome['decision'] != 'applied':
        return {**base, 'decision': 'refused', 'reason': outcome['reason'],
                'witness': {'kind': 'intervention_refused', 'action': action,
                            'reason': outcome['reason']}}
    if after['status'] != SUCCESS:
        return {**base, 'decision': 'refused', 'reason': 'not_restored',
                'witness': {'kind': 'not_restored', 'after': drill_view(after)}}
    excluded, consistent = [], []
    own = signature(outcome, after)
    for other in candidates:
        if other['name'] == world['name']:
            continue
        probe = Instance(other, images)
        probe.observe(path)
        probe_outcome = probe.intervene(action)
        probe_after = probe.observe(path)
        if signature(probe_outcome, probe_after) != own:
            excluded.append({'world': other['name'], 'after': drill_view(probe_after),
                             'outcome': probe_outcome['decision']})
            continue
        replay = verify_repair(other, images, symptom, action)
        if replay['violations']:
            return {**base, 'decision': 'unattributed',
                    'reason': 'indistinguishable_worlds',
                    'witness': {'kind': 'indistinguishable_worlds',
                                'worlds': [world['name'], other['name']],
                                'shared_observations': {'first_read': online_view(first),
                                                        'after': drill_view(after)},
                                'repair': action,
                                'violations': replay['violations'],
                                'note': 'intervention budget is spent and no sanctioned plan '
                                        'distinguishes this pair on the symptom path'}}
        consistent.append(other['name'])
    evidence['excluded'] = excluded
    evidence['consistent'] = consistent
    verification = verify_repair(world, images, symptom, action)
    if verification['violations']:
        return {**base, 'decision': 'refused', 'reason': 'verification_failed',
                'witness': {'kind': 'verification_failed',
                            'violations': verification['violations']}}
    return {**base, 'decision': 'issue_order',
            'order': {'team': auth['team'], 'action': action, 'verification': verification}}


def run(fixture):
    """Evaluate every signed authorization against each same-symptom world."""
    symptom = fixture['symptom']
    images = fixture.get('images', {})
    candidates = [world for world in fixture['worlds']
                  if Instance(world, images).observe(symptom['path'])['status']
                  == symptom['status']]
    evaluations = [evaluate(fixture, world, auth, candidates)
                   for world in fixture['worlds'] for auth in fixture['authorizations']]
    return {'decision': 'diagnosed', 'symptom': symptom, 'evaluations': evaluations}
