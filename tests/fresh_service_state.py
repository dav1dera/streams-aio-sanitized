"""Synthetic generic-state recovery using the real, pinned archive verifier.

The legacy dr.py deliberately cannot verify a generic state archive. The GUI
checks that archive independently and accepts only its precisely attributed
legacy error. This harness mirrors that boundary, without changing dr.py or
turning off its other readiness checks.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import tarfile

FIXTURES = Path(__file__).resolve().parent / 'fixtures'
provenance = json.loads((FIXTURES / 'service-snapshot-provenance.json').read_bytes())
fixture = FIXTURES / provenance['fixture']
if hashlib.sha256(fixture.read_bytes()).hexdigest() != provenance['sha256']:
    raise RuntimeError('SERVICE_SNAPSHOT_FIXTURE_CHANGED')
spec = importlib.util.spec_from_file_location('pinned_service_snapshot', fixture)
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
ASSET = 'services-state.tar.gz.age'
RUN = '20000101T000000Z'


class Refused(ValueError):
    pass


def need(ok, code):
    if not ok:
        raise Refused(code)


def configuration_files(root, value, *, apply_modes=False):
    rows = value.get('configuration_files', [])
    need(isinstance(rows, list), 'SERVICES_STATE_CONFIGURATION_INVALID')
    for row in rows:
        need(isinstance(row, dict) and set(row) == {'path', 'mode', 'sha256'}
             and isinstance(row['path'], str)
             and re.fullmatch(r'data/[a-z0-9][a-z0-9_-]*/public/.+', row['path'])
             and '..' not in Path(row['path']).parts and '\\' not in row['path']
             and not any(ord(char) < 32 or ord(char) == 127 for char in row['path'])
             and type(row['mode']) is int and 0 <= row['mode'] <= 0o777
             and isinstance(row['sha256'], str) and re.fullmatch(r'[0-9a-f]{64}', row['sha256']),
             'SERVICES_STATE_CONFIGURATION_INVALID')
        path = snapshot.checked(Path(root) / row['path'])
        need(path.is_file() and snapshot.digest(path) == row['sha256'], 'SERVICES_STATE_CONFIGURATION_CHANGED')
        if apply_modes:
            # Git records the executable bit, not all modes. Like GUI restore,
            # apply the bound inventory mode only after checking public bytes.
            path.chmod(row['mode'])
        need(os.name == 'nt' or path.stat().st_mode & 0o777 == row['mode'],
             'SERVICES_STATE_CONFIGURATION_CHANGED')


def synthetic_archive(root, archive, model, source_commit, *, kinds=None):
    """Write only invented bytes; real service data is never read or captured."""
    root, archive = Path(root), Path(archive)
    value = snapshot.inventory(root)
    selected = snapshot.selected(value)
    need(selected and not archive.exists() and not archive.is_relative_to(root),
         'SYNTHETIC_SERVICES_ARCHIVE_INVALID')
    kinds = kinds or {}
    need(set(kinds) <= {row['id'] for row in selected}, 'SYNTHETIC_RESOURCE_UNKNOWN')
    entries = []
    images = {}
    with tarfile.open(archive, 'x:gz') as bundle:
        for row in selected:
            image = model['services'][row['service']]['image']
            need(isinstance(image, str) and re.fullmatch(r'.+@sha256:[0-9a-f]{64}', image),
                 'SYNTHETIC_SERVICES_IMMUTABLE_IMAGE_REQUIRED')
            images[row['service']] = image
            kind = kinds.get(row['id'], 'directory')
            need(kind in ('file', 'directory'), 'SYNTHETIC_RESOURCE_KIND_INVALID')
            entries.append({'id': row['id'], 'kind': kind})
            payload = b'synthetic generic service state\x00' + row['id'].encode()
            member = tarfile.TarInfo('data/' + row['id'])
            member.uid = os.getuid() if hasattr(os, 'getuid') else 0
            member.gid = os.getgid() if hasattr(os, 'getgid') else 0
            member.mode = 0o700 if kind == 'directory' else 0o600
            if kind == 'directory':
                member.type = tarfile.DIRTYPE
                bundle.addfile(member)
                child = tarfile.TarInfo(member.name + '/synthetic-state.bin')
                child.uid, child.gid, child.mode = member.uid, member.gid, 0o600
                child.size = len(payload)
                bundle.addfile(child, io.BytesIO(payload))
            else:
                member.size = len(payload)
                bundle.addfile(member, io.BytesIO(payload))
        manifest = {'schema': 1, 'format': snapshot.FORMAT, 'run': RUN,
                    'stack_commit': source_commit,
                    'inventory_sha256': snapshot.digest(root / snapshot.INVENTORY),
                    'images': images, 'entries': entries}
        data = snapshot.canonical(manifest)
        member = tarfile.TarInfo('manifest.json')
        member.mode, member.size = 0o600, len(data)
        bundle.addfile(member, io.BytesIO(data))
    archive.chmod(0o600)
    return manifest


def restore_synthetic(root, archive, model, source_commit, runner):
    """The full restore/verification path is the reviewed GUI implementation."""
    value = snapshot.inventory(root)
    configuration_files(root, value, apply_modes=True)
    evidence = snapshot.restore(archive, root, run_id=RUN, source_commit=source_commit,
                                model=model, runner=runner)
    evidence.pop('checkpoint')
    snapshot.verify_restored(root, archive, evidence, model, runner=runner)
    return evidence


def verified_preflight(dr, args, manifest, model, archive, evidence, source_commit, runner):
    root = Path(args.target)
    inventory = root / snapshot.INVENTORY
    if not inventory.exists():
        need(archive is None and evidence is None, 'SERVICES_STATE_INVENTORY_REQUIRED')
        dr.preflight(args)
        return
    value = snapshot.inventory(root)
    configuration_files(root, value)
    selected = {row['id']: row for row in snapshot.selected(value)}
    units = manifest.get('state_units')
    need(isinstance(units, list) and all(isinstance(unit, dict) for unit in units),
         'SERVICES_STATE_UNIT_INVALID')
    generic = [unit for unit in units if unit.get('resource_id') is not None
               or unit.get('recovery_asset') == ASSET or str(unit.get('id', '')).startswith('service-')]
    need(len(generic) == len(selected)
         and {unit.get('resource_id') for unit in generic} == set(selected),
         'SERVICES_STATE_UNIT_MISMATCH')
    allowed = set()
    for unit in generic:
        row = selected[unit['resource_id']]
        need(unit.get('id') == 'service-' + row['id']
             and unit.get('services') == [row['service']]
             and unit.get('classification') == 'REQUIRED_USER_STATE'
             and unit.get('recovery_asset') == ASSET,
             'SERVICES_STATE_UNIT_MISMATCH')
        allowed.add(row['service'] + ' ' + unit['id'] + ':REQUIRED_USER_CONFIGURATION_NOT_RECONSTRUCTED')
    if selected:
        need(archive is not None and isinstance(evidence, dict), 'SERVICES_STATE_CHECKPOINT_REQUIRED')
        need(evidence.get('run') == RUN and evidence.get('stack_commit') == source_commit,
             'SERVICES_STATE_SCOPE_MISMATCH')
        snapshot.verify_restored(root, archive, evidence, model, runner=runner)
    else:
        need(archive is None and evidence is None, 'SERVICES_STATE_UNEXPECTED_ARCHIVE')
    captured = io.StringIO()
    try:
        with contextlib.redirect_stdout(captured):
            dr.preflight(args)
    except dr.Refused as error:
        failures = captured.getvalue().splitlines()
        need(str(error) == 'DR_PREFLIGHT_BLOCKED' and failures
             and all(line in allowed for line in failures), 'DR_PREFLIGHT_UNVERIFIED_FAILURE')
    print('VERIFIED_GENERIC_PREFLIGHT PASS resources=' + str(len(selected)))
