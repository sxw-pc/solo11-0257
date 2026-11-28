"""Build and consume offline build-handover packages.

A handover package is a self-contained directory that a fully offline receiver
can use to independently rebuild selected source edits with the same real
:mod:`admissioncourt.assembly` engine the owner used.  Byte-equal artifacts on
handover day are not treated as evidence of future maintainability: every
claimed edit is rebuilt from materials the package explicitly owns and the
rebuilt artifact is compared byte-for-byte against the owner's real build of
that same edit.

Two delivery contracts are supported for every licensed tool:

``transfer``
    The tool description and every source it consumes are packaged, so the
    receiver may independently rebuild every step that uses it.

``intermediate``
    The tool description must not be copied.  Only the real intermediate
    output of the sealed step is delivered, together with provenance naming
    the exact tool identity and the exact source role/digest bytes it was
    produced from.  Edits touching a sealed input invalidate the provenance
    and cannot be served from any shared content cache; the consumer returns
    a precise rebuild request naming the missing tool right or source.

Source identity is the source *role* (its name in the build graph), not its
current bytes: two roles may hold identical bytes and still carry different
maintenance rights.
"""
import json
from pathlib import Path

from . import assembly, build_compare
from .admit import hash_value
from .components import select as select_components

TOOL_DIR = 'tools'
SOURCE_DIR = 'sources'
SEALED_DIR = 'sealed'
TRUTH_DIR = 'truth'
MANIFEST_NAME = 'manifest.json'

VALID_GRANTS = ('transfer', 'intermediate')
SEALED_DECISION = 'rebuild_request'


# --------------------------------------------------------------------------
# small material helpers
# --------------------------------------------------------------------------

def _write_bytes(path, content):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding='utf-8')


def _read_bytes(path):
    return Path(path).read_text(encoding='utf-8')


def _tool_identity(tool):
    return {'name': tool['name'], 'edition': tool['edition'],
            'digest': hash_value(tool['exports'])}


def _selected_tools(fixture):
    return select_components(fixture['catalog'])


def _ordered_roles(fixture):
    """Every source role, with the original source names preserved."""
    return list(fixture['sources'].keys())


def _step_input_roles(step):
    return list(step['inputs'])


# --------------------------------------------------------------------------
# build graph analysis: which roles are independently maintainable
# --------------------------------------------------------------------------

def _tool_grants(fixture):
    grants = {}
    for row in fixture.get('grants', []):
        if row['mode'] not in VALID_GRANTS:
            raise ValueError('invalid grant mode: %r' % row['mode'])
        grants[row['tool']] = row['mode']
    return grants


def _classify_steps(fixture, tools, grants):
    """Return per-step provenance information from the real owner build."""
    files_out = {}
    original = assembly.run(fixture, files_out=files_out)
    if original['decision'] != 'built':
        return original, None, None
    sealed = {}
    for step in fixture['recipe']:
        tool = tools[step['tool']]
        mode = grants.get(step['tool'])
        if mode is None:
            return ({'decision': 'unlicensed_tool', 'tool': step['tool'],
                     'step': step['id']}, None, None)
        if mode == 'intermediate':
            sealed[step['id']] = {
                'step': step['id'],
                'tool': _tool_identity(tool),
                'operation': step['operation'],
                'input_roles': _step_input_roles(step),
                'input_digests': [hash_value(files_out.get(role)
                                              if role in files_out
                                              else fixture['sources'][role])
                                  for role in step['inputs']],
                'output_role': step['output'],
                'output_digest': hash_value(files_out[step['output']]),
                'bytes': files_out[step['output']],
            }
    return original, sealed, files_out


def _sealed_consumers(fixture, sealed):
    """Map every role that reaches a sealed step to the sealed step id."""
    consumed_by = {}
    for step in fixture['recipe']:
        if step['id'] not in sealed:
            continue
        for role in step['inputs']:
            consumed_by.setdefault(role, step['id'])
    return consumed_by


def _packaged_roles(fixture, sealed, files_out):
    """Roles whose bytes the package owns.

    Original sources that feed a sealed step stay with the owner (the tool
    that consumes them is restricted).  Every other source role, and every
    derived intermediate a sealed step reads, is packaged so its provenance
    digest can be checked on the receiving side.
    """
    original_sealed_inputs = set()
    derived_sealed_inputs = set()
    for provenance in sealed.values():
        for role in provenance['input_roles']:
            if role in fixture['sources']:
                original_sealed_inputs.add(role)
            else:
                derived_sealed_inputs.add(role)
    roles = [role for role in _ordered_roles(fixture)
             if role not in original_sealed_inputs]
    roles.extend(sorted(derived_sealed_inputs, key=list(files_out).index))
    return roles


def _maintainable_roles(fixture, sealed):
    """Roles the receiver may edit and still rebuild without the owner.

    A role is maintainable when no sealed step consumes it.  Roles feeding
    only transferred tools (or public/common tools that every build owns) can
    always be rebuilt from packaged materials.
    """
    blocked = set()
    for provenance in sealed.values():
        blocked.update(provenance['input_roles'])
    return [role for role in _ordered_roles(fixture) if role not in blocked]


# --------------------------------------------------------------------------
# owner-side truth builds for each declared edit
# --------------------------------------------------------------------------

def _apply_changes(sources, changes):
    edited = dict(sources)
    for change in changes:
        if change['role'] not in edited:
            raise KeyError(change['role'])
        edited[change['role']] = change['value']
    return edited


def _changes_map(changes):
    return {change['role']: change['value'] for change in changes}


def _owner_build(fixture, changes):
    """Run the real assembly engine on an edited copy of the owner fixture."""
    edited = dict(fixture)
    edited['sources'] = _apply_changes(fixture['sources'], changes)
    return assembly.run(edited)


def _edit_pair(fixture, edit):
    """The same build_compare byte check, against the owner's own tool chain."""
    observed = _owner_build(fixture, edit['changes'])
    pair_fixture = {'reference': fixture,
                    'observed': {**fixture,
                                 'sources': _apply_changes(fixture['sources'],
                                                           edit['changes'])}}
    return assembly.run(fixture), observed, build_compare.run(pair_fixture)


# --------------------------------------------------------------------------
# package construction
# --------------------------------------------------------------------------

def build_package(fixture, out_dir):
    """Materialize a handover package at ``out_dir`` (which must not exist)."""
    out = Path(out_dir)
    if out.exists():
        return {'decision': 'output_exists', 'path': str(out)}

    tools = _selected_tools(fixture)
    grants = _tool_grants(fixture)
    for name in grants:
        if name not in tools:
            return {'decision': 'tool_missing', 'tool': name}

    original, sealed, files_out = _classify_steps(fixture, tools, grants)
    if sealed is None:
        return original
    consumed_by = _sealed_consumers(fixture, sealed)
    maintainable = _maintainable_roles(fixture, sealed)
    packaged_roles = _packaged_roles(fixture, sealed, files_out)

    transferred_tools = sorted(name for name, mode in grants.items()
                               if mode == 'transfer')
    recipe_tools = {step['tool'] for step in fixture['recipe']}
    if recipe_tools - set(grants):
        return {'decision': 'unlicensed_tool',
                'tool': sorted(recipe_tools - set(grants))[0]}

    # A sealed step whose intermediate is not actually consumed later, or a
    # restricted tool with no sealed step, would mean the restriction has no
    # enforceable boundary — refuse rather than ship an ambiguous package.
    if sealed:
        produced = {p['output_role'] for p in sealed.values()}
        later_inputs = {role for step in fixture['recipe']
                        for role in step['inputs']}
        if not produced & later_inputs:
            return {'decision': 'unsealed_restricted_output'}

    out.mkdir(parents=True)
    try:
        manifest = _write_material(out, fixture, tools, grants, sealed,
                                   consumed_by, maintainable, packaged_roles,
                                   files_out, original, transferred_tools)
        result = _self_check(out, manifest, fixture)
    except Exception:
        _remove_tree(out)
        raise
    if result['decision'] != 'packaged':
        _remove_tree(out)
    return result


def _remove_tree(path):
    for child in sorted(path.rglob('*'), reverse=True):
        if child.is_file() or child.is_symlink():
            child.unlink()
        else:
            child.rmdir()
    path.rmdir()


def _write_material(out, fixture, tools, grants, sealed, consumed_by,
                    maintainable, packaged_roles, files_out, original,
                    transferred_tools):
    tool_files = {}
    for name in transferred_tools:
        tool = tools[name]
        record = {'name': name, 'edition': tool['edition'],
                 'available': True, 'exports': tool['exports']}
        path = out / TOOL_DIR / (name + '.json')
        _write_bytes(path, json.dumps(record, ensure_ascii=False,
                                      sort_keys=True))
        tool_files[name] = {'path': str(path.relative_to(out)),
                            **_tool_identity(tool)}

    source_files = {}
    for role in packaged_roles:
        path = out / SOURCE_DIR / (role + '.txt')
        if role in fixture['sources']:
            content = fixture['sources'][role]
        else:
            content = files_out[role]
        _write_bytes(path, content)
        source_files[role] = {'path': str(path.relative_to(out)),
                              'digest': hash_value(content)}

    sealed_files = {}
    for provenance in sealed.values():
        path = out / SEALED_DIR / (provenance['step'] + '.txt')
        _write_bytes(path, provenance['bytes'])
        sealed_files[provenance['step']] = {
            'path': str(path.relative_to(out)),
            'tool': provenance['tool'],
            'operation': provenance['operation'],
            'input_roles': provenance['input_roles'],
            'input_digests': provenance['input_digests'],
            'output_role': provenance['output_role'],
            'output_digest': provenance['output_digest'],
        }

    # Restricted tool descriptions never leave the owner: assert no bytes of
    # an intermediate-mode export landed anywhere in the package.
    restricted_exports = [
        json.dumps(tools[name]['exports'], sort_keys=True)
        for name, mode in grants.items() if mode == 'intermediate'
    ]
    for written in out.rglob('*'):
        if written.is_file() and written.name != MANIFEST_NAME:
            text = written.read_text(encoding='utf-8')
            for export_text in restricted_exports:
                if export_text in text:
                    raise RuntimeError('restricted export leaked into package')

    truth_files = {}
    path = out / TRUTH_DIR / 'baseline.artifact'
    _write_bytes(path, original['artifact'])
    truth_files['baseline'] = {'path': str(path.relative_to(out)),
                               'digest': hash_value(original['artifact'])}

    manifest = {
        'artifact': fixture['artifact'],
        'recipe': fixture['recipe'],
        'grants': [{'tool': name, 'mode': mode}
                   for name, mode in sorted(grants.items())],
        'tools': tool_files,
        'sources': source_files,
        'sealed': sealed_files,
        'packaged_roles': packaged_roles,
        'maintainable_roles': maintainable,
        'blocked_roles': sorted(
            consumed_by,
            key=lambda r: list(fixture['sources']).index(r)
            if r in fixture['sources'] else len(fixture['sources'])
            + list(files_out).index(r)),
        'baseline': {'artifact_digest': hash_value(original['artifact']),
                     'trace': original['trace']},
        'edits': [],
        'truth': truth_files,
    }
    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8')
    return manifest


def _self_check(out, manifest, fixture):
    """Rebuild every declared edit through the consumer and check owner truth.

    The self check deliberately goes through :func:`consume_package`, so the
    shipped capability is exactly what an offline receiver can exercise.
    """
    outcomes = []
    delivered = 0
    for edit in fixture.get('edits', []):
        _reference, observed, comparison = _edit_pair(fixture, edit)
        if observed['decision'] != 'built':
            return {'decision': 'owner_build_failed', 'edit': edit['id'],
                    'detail': observed}

        result = consume_package(out, overrides=_changes_map(edit['changes']))
        record = {'id': edit['id'], 'changes': edit['changes'],
                  'owner_comparison': comparison['decision'],
                  'consumer_decision': result['decision']}
        if result['decision'] == 'delivered':
            delivered += 1
            record['artifact_digest'] = hash_value(result['artifact'])
            if result['artifact'] != observed['artifact']:
                return {'decision': 'handover_diverged', 'edit': edit['id']}
            if comparison['decision'] != 'different':
                return {'decision': 'edit_without_effect', 'edit': edit['id']}
            # Only independently deliverable edits get byte-identical owner
            # truth on disk; a restricted edit's full artifact must not ship.
            truth_name = 'edit-%s.artifact' % edit['id']
            truth_path = out / TRUTH_DIR / truth_name
            _write_bytes(truth_path, observed['artifact'])
            manifest['truth'][edit['id']] = {
                'path': str(truth_path.relative_to(out)),
                'digest': hash_value(observed['artifact'])}
        elif result['decision'] == SEALED_DECISION:
            record['request'] = result['request']
            if comparison['decision'] != 'different':
                return {'decision': 'edit_without_effect', 'edit': edit['id']}
            # Evidence stays digest-only: the receiver can prove a later
            # rebuild wrong without ever receiving the restricted artifact.
            manifest['truth'][edit['id']] = {
                'digest': hash_value(observed['artifact'])}
        else:
            return {'decision': 'self_check_failed', 'edit': edit['id'],
                    'detail': result}
        outcomes.append(record)
        manifest['edits'].append(record)

    if not delivered:
        return {'decision': 'no_independent_maintenance', 'outcomes': outcomes}

    # Baseline rebuild with no edits must reproduce the handover-day artifact.
    baseline = consume_package(out)
    if baseline['decision'] != 'delivered' or \
            baseline['artifact'] != _read_bytes(
                out / manifest['truth']['baseline']['path']):
        return {'decision': 'baseline_not_reproducible', 'detail': baseline}

    (out / MANIFEST_NAME).write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True),
        encoding='utf-8')
    return {'decision': 'packaged', 'path': str(out),
            'maintainable_roles': manifest['maintainable_roles'],
            'blocked_roles': manifest['blocked_roles'],
            'edits': outcomes,
            'baseline_digest': manifest['baseline']['artifact_digest']}


# --------------------------------------------------------------------------
# receiver-side consumption (fully offline, package directory only)
# --------------------------------------------------------------------------

def _load_manifest(package_dir):
    path = Path(package_dir) / MANIFEST_NAME
    return json.loads(path.read_text(encoding='utf-8'))


def _verify_digests(package_dir, manifest):
    """Every material the package claims to own must match its digest."""
    root = Path(package_dir)
    for role, record in manifest['sources'].items():
        if hash_value(_read_bytes(root / record['path'])) != record['digest']:
            return {'decision': 'material_tampered', 'role': role}
    for step_id, record in manifest['sealed'].items():
        if hash_value(_read_bytes(root / record['path'])) != \
                record['output_digest']:
            return {'decision': 'material_tampered', 'sealed_step': step_id}
    for name, record in manifest['tools'].items():
        tool = json.loads(_read_bytes(root / record['path']))
        identity = _tool_identity(tool)
        if identity['digest'] != record['digest'] or \
                identity['edition'] != record['edition'] or \
                identity['name'] != record['name']:
            return {'decision': 'material_tampered', 'tool': name}
    return None


def _build_receiver_fixture(package_dir, manifest, changes):
    """Construct an assembly fixture using only package-owned materials."""
    root = Path(package_dir)
    providers = []
    for record in manifest['tools'].values():
        tool = json.loads(_read_bytes(root / record['path']))
        providers.append(tool)
    catalog = {'providers': providers}

    sources = {role: _read_bytes(root / record['path'])
               for role, record in manifest['sources'].items()}
    sealed_outputs = {}
    for record in manifest['sealed'].values():
        sealed_outputs[record['output_role']] = \
            _read_bytes(root / record['path'])

    changed_roles = {change['role'] for change in changes}
    known_roles = set(manifest['maintainable_roles']) | \
        set(manifest['blocked_roles']) | set(sources)
    unknown = changed_roles - known_roles
    if unknown:
        return None, {'decision': 'unknown_role', 'roles': sorted(unknown)}

    # Provenance check: a sealed intermediate stays valid only while every
    # source role actually consumed by the sealed step is untouched.  The
    # sealed inputs themselves are not packaged (the tool that consumes them
    # is restricted), so their recorded digests are carried only as evidence
    # in the request; equal bytes elsewhere never satisfy provenance, which
    # names roles, not content hashes.  File-level drift of any packaged
    # material is rejected earlier by _verify_digests.
    for step_id, record in manifest['sealed'].items():
        touched = changed_roles & set(record['input_roles'])
        if touched:
            return None, {'decision': SEALED_DECISION, 'request': {
                'kind': 'missing_right',
                'sealed_step': step_id,
                'missing_tool': record['tool'],
                'changed_input_roles': sorted(touched),
                'expected_input_digests': dict(zip(record['input_roles'],
                                                    record['input_digests'])),
                'reason': 'sealed input replaced; shared content cache may '
                          'not be reused',
            }}

    # Seed the sealed intermediates as pre-built files and drop sealed steps.
    sources.update(sealed_outputs)
    sealed_step_ids = set(manifest['sealed'])
    recipe = [step for step in manifest['recipe']
              if step['id'] not in sealed_step_ids]

    # All remaining steps must resolve through transferred/package tools.
    owned_tools = {record['name'] for record in manifest['tools'].values()}
    for step in recipe:
        if step['tool'] not in owned_tools:
            return None, {'decision': SEALED_DECISION, 'request': {
                'kind': 'missing_tool',
                'missing_tool': step['tool'], 'step': step['id']}}

    for change in changes:
        sources[change['role']] = change['value']
    return {'catalog': catalog, 'sources': sources, 'recipe': recipe,
            'artifact': manifest['artifact']}, None


def consume_package(package_dir, edit_id=None, overrides=None):
    """Rebuild inside an offline package; return delivered bytes or a request.

    ``edit_id`` selects a declared edit from the manifest; ``overrides`` is an
    optional mapping of role -> new bytes for ad-hoc maintenance requests.
    The function reads only files inside ``package_dir``.
    """
    manifest = _load_manifest(package_dir)
    tamper = _verify_digests(package_dir, manifest)
    if tamper:
        return tamper

    changes = []
    if edit_id is not None:
        record = next((row for row in manifest['edits']
                       if row['id'] == edit_id), None)
        if record is None:
            return {'decision': 'unknown_edit', 'edit': edit_id}
        changes = list(record['changes'])
    if overrides:
        seen = {change['role'] for change in changes}
        for role, value in overrides.items():
            if role in seen:
                changes = [c for c in changes if c['role'] != role]
            changes.append({'role': role, 'value': value})

    fixture, error = _build_receiver_fixture(package_dir, manifest, changes)
    if error:
        return error

    result = assembly.run(fixture)
    if result['decision'] != 'built':
        return {'decision': 'receiver_build_failed', 'detail': result}

    truth = manifest['truth'].get(edit_id) if edit_id else \
        manifest['truth'].get('baseline')
    response = {'decision': 'delivered', 'artifact': result['artifact'],
                'artifact_digest': hash_value(result['artifact']),
                'trace': result['trace'], 'applied_changes': changes}
    if truth is not None:
        response['matches_owner_truth'] = \
            hash_value(result['artifact']) == truth['digest']
    return response
