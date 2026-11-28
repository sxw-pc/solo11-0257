"""Seal immutable user-facing output artifacts from an offline process journal.

``console_output`` only snapshots the log prefix it is handed: it writes no
file and never declares the input stream sealed.  This executor publishes
immutable handoff downloads without spawning native processes.

Write access is an inheritable handle bound to a pipe generation, so a reused
pid number or pipe name grants nothing.  Lines are records assembled from
durable fragments with idempotent fragment ids: a fragment the receiver saved
but whose console ack was lost, and a fragment that never arrived and was
retried, both end up as exactly one line.  An artifact seals only once every
record it owns is complete and every write capability is closed or was
explicitly transferred by a confirmed relay.  A restart rebuilds capability
state from the durable journal alone -- never from an exit table, a pid
lookup or a pipe name shared by two logs.
"""
import hashlib

# Policies that own the default write stream of a pipe generation.
STREAM_OWNER_POLICIES = ('job_tree', 'relay_source')


class HandoffExecutor:
    """Replay a synthetic process/transport trace and seal downloads."""

    def __init__(self, scenario):
        self.scenario = scenario
        self.default_generation = scenario.get('generation')
        self.journal = []
        self.artifacts = {}
        for consumer in scenario.get('consumers', []):
            self.artifacts[consumer['id']] = {
                'id': consumer['id'], 'policy': consumer['policy'],
                'relay': consumer.get('relay'),
                'job': consumer.get('job', scenario.get('job')),
                'generation': consumer.get('generation', self.default_generation),
                'sealed': False, 'text': None, 'digest': None,
                'records': [], 'status': 'waiting'}
        # Console-side observations; nothing here is authoritative after restart.
        self.runtime = {'deduped': 0, 'delivery_lost': 0, 'rejected': 0}
        self.restarts = 0
        self.rejections = []
        self.checkpoints = []
        self._reset_views()

    # ------------------------------------------------------------------ views
    def _reset_views(self):
        self.procs = {}         # proc instance -> {pid, job, ppid, exited}
        self.generations = {}   # generation id -> {pipe, job, creator}
        self.handles = {}       # handle id -> capability view
        self.fragments = {}     # fragment id -> durable delivery fact
        self.records = {}       # record id -> {gen, owner, fragments, complete}
        self.record_order = {}  # generation -> record ids in stream order
        self.relays = {}        # relay id -> authorization view
        self.owner_by_gen = {}  # generation -> artifact owning new records

    # --------------------------------------------------------------- journaling
    def _commit(self, fact):
        self.journal.append(fact)
        self._ingest(fact)

    def _reject(self, event, reason):
        self.runtime['rejected'] += 1
        note = {'op': event['op'], 'reason': reason}
        for key in ('proc', 'handle', 'generation', 'fragment', 'record', 'relay'):
            if key in event:
                note[key] = event[key]
        self.rejections.append(note)
        return {'decision': reason}

    def _ingest(self, fact):
        kind = fact['kind']
        if kind == 'proc_started':
            self.procs[fact['proc']] = {'pid': fact['pid'], 'job': fact['job'],
                                        'ppid': fact.get('ppid'), 'exited': False}
        elif kind == 'generation_declared':
            self.generations.setdefault(fact['generation'],
                                        {'pipe': fact['pipe'], 'job': fact['job'],
                                         'creator': fact['creator']})
        elif kind == 'handle_opened':
            self.handles[fact['handle']] = {'handle': fact['handle'],
                'gen': fact['generation'], 'proc': fact['proc'],
                'state': 'open', 'basis': None, 'origin': 'open'}
        elif kind == 'handle_inherited':
            self.handles[fact['handle']] = {'handle': fact['handle'],
                'gen': fact['generation'], 'proc': fact['proc'],
                'state': 'open', 'basis': None,
                'origin': 'inherited:' + fact['source']}
        elif kind == 'handle_closed':
            self.handles[fact['handle']]['state'] = 'closed'
            self.handles[fact['handle']]['basis'] = fact['basis']
        elif kind == 'proc_exited':
            self.procs[fact['proc']]['exited'] = True
        elif kind == 'record_owner':
            self.records[fact['record']] = {'id': fact['record'],
                'gen': fact['generation'], 'owner': fact['artifact'],
                'fragments': [], 'complete': False}
            self.record_order.setdefault(fact['generation'], []).append(fact['record'])
        elif kind == 'fragment_delivered':
            record = self.records[fact['record']]
            record['fragments'].append(fact['fragment'])
            self.fragments[fact['fragment']] = dict(fact)
            if fact['text'].endswith('\n'):
                record['complete'] = True
        elif kind == 'relay_requested':
            self.relays[fact['relay']] = {'relay': fact['relay'],
                'gen': fact['generation'], 'after_record': fact['after_record'],
                'status': 'requested', 'activated': False}
        elif kind == 'relay_resolved':
            self.relays[fact['relay']]['status'] = fact['status']
        elif kind == 'relay_activated':
            relay = self.relays[fact['relay']]
            relay['activated'] = True
            self.owner_by_gen[relay['gen']] = fact['to_artifact']
        elif kind == 'artifact_sealed':
            self.artifacts[fact['artifact']].update(
                sealed=True, text=fact['text'], digest=fact['digest'],
                records=list(fact['records']), status='sealed')

    def _rebuild(self):
        """Recover all capability state from the durable journal only."""
        self._reset_views()
        for art in self.artifacts.values():
            art.update(sealed=False, text=None, digest=None,
                       records=[], status='waiting')
        for fact in self.journal:
            self._ingest(fact)
        self._evaluate()

    # ------------------------------------------------------------------ runtime
    def run_events(self, events):
        trace = []
        for position, event in enumerate(events, 1):
            if event['op'] == 'checkpoint':
                snapshot = {
                    'seq': position, 'checkpoint': event['id'],
                    'artifacts': {aid: self.claim(aid) for aid in self.artifacts}}
                self.checkpoints.append(snapshot)
                trace.append({'decision': 'checkpoint', 'checkpoint': event['id']})
                continue
            decision = self._apply(event)
            trace.append(decision)
        return trace

    def _open_handle_views(self, gen):
        return [{'handle': h, 'generation': v['gen'], 'proc': v['proc'],
                 'origin': v['origin']}
                for h, v in self.handles.items()
                if v['state'] == 'open' and (gen is None or v['gen'] == gen)]

    def _apply(self, event):
        if event['op'] == 'restart':
            self._rebuild()
            self.restarts += 1
            return {'decision': 'restarted_from_journal'}
        handler = getattr(self, '_op_' + event['op'], None)
        if handler is None:
            return self._reject(event, 'unknown_op')
        return handler(event)

    def _live(self, proc):
        view = self.procs.get(proc)
        return view is not None and not view['exited']

    def _op_start(self, e):
        if e['proc'] in self.procs and not self.procs[e['proc']]['exited']:
            return self._reject(e, 'proc_already_started')
        if any(not v['exited'] and v['pid'] == e['pid'] for v in self.procs.values()):
            return self._reject(e, 'pid_number_in_use')
        self._commit({'kind': 'proc_started', 'proc': e['proc'], 'pid': e['pid'],
                      'job': e.get('job'), 'ppid': e.get('ppid')})
        return {'decision': 'started', 'proc': e['proc'], 'pid': e['pid']}

    def _op_open(self, e):
        if not self._live(e['proc']):
            return self._reject(e, 'proc_unknown_or_exited')
        if e['generation'] not in self.generations:
            self._commit({'kind': 'generation_declared',
                          'generation': e['generation'], 'pipe': e['pipe'],
                          'job': e.get('job', self.scenario.get('job')),
                          'creator': e['proc']})
        elif self.generations[e['generation']]['pipe'] != e['pipe']:
            return self._reject(e, 'generation_name_mismatch')
        if e['handle'] in self.handles:
            return self._reject(e, 'handle_already_exists')
        self._commit({'kind': 'handle_opened', 'handle': e['handle'],
                      'generation': e['generation'], 'proc': e['proc']})
        return {'decision': 'handle_opened', 'handle': e['handle'],
                'generation': e['generation']}

    def _op_inherit(self, e):
        source = self.handles.get(e['handle'])
        if source is None or source['state'] != 'open' or source['proc'] != e['from']:
            return self._reject(e, 'source_handle_not_held')
        if not self._live(e['to']):
            return self._reject(e, 'proc_unknown_or_exited')
        if e['as'] in self.handles:
            return self._reject(e, 'handle_already_exists')
        self._commit({'kind': 'handle_inherited', 'handle': e['as'],
                      'source': e['handle'], 'generation': source['gen'],
                      'proc': e['to']})
        return {'decision': 'handle_inherited', 'handle': e['as'],
                'generation': source['gen']}

    def _capability(self, e):
        """Prove real write access: live proc holding an open handle for this
        exact pipe generation. Pid numbers and pipe names are never consulted."""
        if not self._live(e['proc']):
            return None
        handle = self.handles.get(e['handle'])
        if handle is None or handle['proc'] != e['proc'] or handle['state'] != 'open':
            return None
        if handle['gen'] != e['generation']:
            return None
        return handle

    def _op_write(self, e):
        return self._deliver(e, retry=False)

    def _op_retry(self, e):
        return self._deliver(e, retry=True)

    def _deliver(self, e, retry):
        if self._capability(e) is None:
            return self._reject(e, 'no_write_capability')
        fragment = e['fragment']
        if fragment in self.fragments:
            # Idempotent redelivery: the receiver already durably has these bytes.
            self.runtime['deduped'] += 1
            return {'decision': 'duplicate_fragment_deduped', 'fragment': fragment}
        gen = e['generation']
        if any(a['sealed'] for a in self.artifacts.values()
               if a['generation'] == gen):
            return self._reject(e, 'artifact_immutable')
        if not retry and e.get('delivery') == 'lost':
            # Receiver never got this; nothing durable exists until the retry.
            self.runtime['delivery_lost'] += 1
            return {'decision': 'delivery_lost', 'fragment': fragment}
        if retry:
            path = 'retry_persisted'
        elif e.get('delivery') == 'ack_lost':
            path = 'ack_lost'
        else:
            path = 'persisted'
        rejected = self._persist_fragment(e, path)
        if rejected is not None:
            return rejected
        self._evaluate()
        return {'decision': 'delivery_ack_lost' if path == 'ack_lost'
                else 'fragment_persisted', 'fragment': fragment, 'path': path}

    def _persist_fragment(self, e, path):
        gen = e['generation']
        try:
            stream_owner = self.owner_by_gen.get(gen) or self._default_owner(gen)
        except KeyError:
            # A real handle on a generation no authorized consumer downloads.
            return self._reject(e, 'unowned_generation')
        if e['record'] in self.records:
            owner = self.records[e['record']]['owner']
        else:
            owner = stream_owner
        # A record belongs to the artifact that owned the stream when it
        # started; a fragment cannot be forced into a different download.
        if e.get('target_artifact') and e['target_artifact'] != owner:
            return self._reject(e, 'cross_artifact_fragment')
        if e['record'] not in self.records:
            self._commit({'kind': 'record_owner', 'record': e['record'],
                          'generation': gen, 'artifact': owner})
        self._commit({'kind': 'fragment_delivered', 'fragment': e['fragment'],
                      'generation': gen, 'proc': e['proc'], 'record': e['record'],
                      'seq': e['seq'], 'text': e['text'], 'path': path})
        if self.records[e['record']]['complete']:
            self._sync_relays(gen)
        return None

    def _op_close(self, e):
        handle = self.handles.get(e['handle'])
        if handle is None or handle['proc'] != e['proc']:
            return self._reject(e, 'handle_not_held')
        if handle['state'] == 'closed':
            return {'decision': 'already_closed', 'handle': e['handle'],
                    'basis': handle['basis']}
        # The close is durable even when the console never receives its ack.
        self._commit({'kind': 'handle_closed', 'handle': e['handle'], 'basis': 'close'})
        self._evaluate()
        return {'decision': 'close_ack_lost' if e.get('ack') == 'lost'
                else 'handle_closed', 'handle': e['handle']}

    def _op_exit(self, e):
        if not self._live(e['proc']):
            return self._reject(e, 'proc_unknown_or_exited')
        for hid, view in list(self.handles.items()):
            if view['proc'] == e['proc'] and view['state'] == 'open':
                # An exit closes this proc's copies only; inherited copies
                # elsewhere stay open and keep blocking the seal.
                self._commit({'kind': 'handle_closed', 'handle': hid, 'basis': 'exit'})
        self._commit({'kind': 'proc_exited', 'proc': e['proc']})
        self._evaluate()
        return {'decision': 'exited', 'proc': e['proc']}

    def _relay_pair(self, relay_id):
        source = next((a for a in self.artifacts.values()
                       if a['policy'] == 'relay_source' and a['relay'] == relay_id), None)
        target = next((a for a in self.artifacts.values()
                       if a['policy'] == 'relay_target' and a['relay'] == relay_id), None)
        return source, target

    def _op_relay_request(self, e):
        source, target = self._relay_pair(e['relay'])
        if source is None or target is None:
            return self._reject(e, 'relay_not_authorized')
        if e['relay'] in self.relays:
            return self._reject(e, 'relay_already_requested')
        self._commit({'kind': 'relay_requested', 'relay': e['relay'],
                      'generation': source['generation'],
                      'after_record': e['after_record']})
        after = self.records.get(e['after_record'])
        if after is not None and not after['complete']:
            # The handoff waits for the newline; a line in progress is not cut.
            return {'decision': 'relay_deferred_mid_record', 'relay': e['relay']}
        return {'decision': 'relay_requested', 'relay': e['relay']}

    def _op_relay_confirm(self, e):
        relay = self.relays.get(e['relay'])
        if relay is None:
            return self._reject(e, 'relay_unknown')
        if relay['status'] != 'requested':
            return self._reject(e, 'relay_already_resolved')
        self._commit({'kind': 'relay_resolved', 'relay': e['relay'],
                      'status': e['status']})
        if e['status'] == 'confirmed':
            self._maybe_activate(e['relay'])
        self._evaluate()
        return {'decision': 'relay_' + e['status'], 'relay': e['relay']}

    def _maybe_activate(self, relay_id):
        relay = self.relays[relay_id]
        if relay['status'] != 'confirmed' or relay['activated']:
            return
        after = self.records.get(relay['after_record'])
        if after is None or not after['complete']:
            return  # boundary not reached yet
        source, target = self._relay_pair(relay_id)
        self._commit({'kind': 'relay_activated', 'relay': relay_id,
                      'boundary_record': relay['after_record'],
                      'from_artifact': source['id'], 'to_artifact': target['id']})

    def _sync_relays(self, gen):
        # A relay whose boundary record just finished may have been waiting
        # since a mid-record request; try activation again on the newline.
        for rid, relay in self.relays.items():
            if relay['gen'] == gen and relay['status'] == 'confirmed' \
                    and not relay['activated']:
                self._maybe_activate(rid)

    # ------------------------------------------------------------------ sealing
    def _open_handles(self, gen):
        return [h for h, v in self.handles.items()
                if v['state'] == 'open' and v['gen'] == gen]

    def _default_owner(self, gen):
        owner = next((a['id'] for a in self.artifacts.values()
                      if a['generation'] == gen and a['policy'] in STREAM_OWNER_POLICIES), None)
        if owner is None:
            raise KeyError('no consumer owns generation %r' % gen)
        return owner

    def _sealability(self, art):
        gen = art['generation']
        owned = [r for r in self.record_order.get(gen, [])
                 if self.records[r]['owner'] == art['id']]
        if any(not self.records[r]['complete'] for r in owned):
            return False, 'record_incomplete'
        open_handles = self._open_handles(gen)
        if art['policy'] == 'relay_target':
            relay = self.relays.get(art['relay'])
            if relay is None or relay['status'] == 'lost':
                return False, 'relay_unconfirmed'
            if not relay['activated']:
                return False, 'waiting_relay_boundary'
            if open_handles:
                return False, 'waiting_writer'
            return True, 'sealable'
        # Both job_tree and relay_source seal only once every write capability
        # for this generation is closed.  A confirmed relay reassigns *future*
        # responsibility so the target may seal independently; it never lets the
        # source drop bytes still arriving on inherited handles.
        if open_handles:
            return False, 'waiting_writer'
        return True, 'sealable'

    def _record_text(self, record):
        ordered = sorted(record['fragments'],
                         key=lambda fid: self.fragments[fid]['seq'])
        return ''.join(self.fragments[fid]['text'] for fid in ordered)

    def _evaluate(self):
        for art in self.artifacts.values():
            if art['sealed']:
                continue
            ok, _ = self._sealability(art)
            if not ok:
                continue
            gen = art['generation']
            record_ids = [rid for rid in self.record_order.get(gen, [])
                          if self.records[rid]['owner'] == art['id']]
            text = ''.join(self._record_text(self.records[rid]) for rid in record_ids)
            digest = hashlib.sha256(text.encode('utf-8')).hexdigest()
            self._commit({'kind': 'artifact_sealed', 'artifact': art['id'],
                          'generation': gen, 'text': text, 'digest': digest,
                          'records': record_ids})

    def claim(self, artifact_id):
        art = self.artifacts.get(artifact_id)
        if art is None:
            return {'decision': 'unknown_artifact'}
        if art['sealed']:
            return {'decision': 'sealed', 'artifact': art['id'], 'job': art['job'],
                    'generation': art['generation'], 'records': list(art['records']),
                    'text': art['text'], 'digest': art['digest']}
        _, reason = self._sealability(art)
        return {'decision': 'not_sealed', 'artifact': art['id'], 'reason': reason,
                'open_handles': self._open_handles(art['generation'])}

    def delivery_paths(self):
        paths = {}
        for fragment in self.fragments.values():
            paths[fragment['path']] = paths.get(fragment['path'], 0) + 1
        return paths


def _scenario_events(fixture, scenario):
    """Merge a scenario's injections into the shared producer timeline."""
    if scenario.get('events'):
        return scenario['events']
    injections = scenario.get('inject_at', {})
    merged = []
    for event in fixture.get('events', []):
        merged.append(event)
        if event['op'] == 'checkpoint' and event['id'] in injections:
            merged.extend(injections[event['id']])
    return merged


def run(fixture):
    """Drive every scenario of a producer/reader fixture and verify claims."""
    results = []
    for scenario in fixture['scenarios']:
        executor = HandoffExecutor(scenario)
        executor.run_events(_scenario_events(fixture, scenario))
        claims = []
        for claim in scenario.get('claims', []):
            got = executor.claim(claim['artifact'])
            row = {'artifact': claim['artifact'], 'decision': got['decision']}
            if got['decision'] == 'sealed':
                row['digest'] = got['digest']
                if 'expect_text' in claim:
                    row['text_match'] = got['text'] == claim['expect_text']
                if 'expect_records' in claim:
                    row['records_match'] = got['records'] == claim['expect_records']
                if claim.get('immutable'):
                    again = executor.claim(claim['artifact'])
                    row['digest_immutable'] = (again['digest'] == got['digest']
                                               and again['text'] == got['text'])
                if 'must_not_contain' in claim:
                    row['excludes'] = claim['must_not_contain'] not in got['text']
            elif 'expect_reason' in claim:
                row['reason_match'] = got.get('reason') == claim['expect_reason']
            claims.append(row)
        results.append({
            'decision': 'completed', 'scenario': scenario['id'],
            'artifacts': {aid: executor.claim(aid) for aid in executor.artifacts},
            'delivery_paths': executor.delivery_paths(),
            'runtime': executor.runtime, 'restarts': executor.restarts,
            'rejections': executor.rejections,
            'checkpoints': executor.checkpoints, 'claims': claims})
    return {'decision': 'completed', 'scenarios': results}
