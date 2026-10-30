"""Archive evidence, not directories or checkpoints alone, proves readiness."""
import argparse
import copy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest

from fresh_service_state import (ASSET, RUN, Refused, snapshot, synthetic_archive,
                                 configuration_files, restore_synthetic, verified_preflight)

COMMIT = 'b' * 40
IMAGE = 'example/service@sha256:' + 'c' * 64


class Legacy:
    class Refused(ValueError):
        pass

    def __init__(self, failures):
        self.failures = failures

    def preflight(self, args):
        if self.failures:
            print('\n'.join(self.failures))
            raise self.Refused('DR_PREFLIGHT_BLOCKED')


class FreshServiceState(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.base = Path(self.temporary.name)
        self.root = self.base / 'fresh'
        (self.root / 'config').mkdir(parents=True)
        public = self.root / 'data/demo/public/settings.yaml'
        public.parent.mkdir(parents=True)
        public.write_bytes(b'title: Synthetic\n')
        public.chmod(0o644)
        self.resources = [
            dict(id='first', service='demo', type='bind', source='data/demo/logs', target='/logs',
                 read_only=False, role='data', recovery={'method': 'stopped_archive'}),
            dict(id='second', service='sidecar', type='bind', source='data/sidecar/state.bin', target='/state',
                 read_only=False, role='data', recovery={'method': 'stopped_archive'}),
        ]
        self.value = dict(schema=1, resources=self.resources,
            configuration_files=[dict(path='data/demo/public/settings.yaml', mode=0o644,
                                      sha256=hashlib.sha256(public.read_bytes()).hexdigest())])
        (self.root / snapshot.INVENTORY).write_bytes(snapshot.canonical(self.value))
        self.model = {'services': {row['service']: {'image': IMAGE,
            'volumes': [dict(type='bind', source=str(self.root / row['source']), target=row['target'], read_only=False)]}
            for row in self.resources}}
        self.manifest = {'state_units': [dict(id='service-' + row['id'], resource_id=row['id'],
            services=[row['service']], classification='REQUIRED_USER_STATE', recovery_asset=ASSET)
            for row in self.resources]}
        self.failures = [row['service'] + ' service-' + row['id'] + ':REQUIRED_USER_CONFIGURATION_NOT_RECONSTRUCTED'
                         for row in self.resources]
        self.args = argparse.Namespace(target=str(self.root), mode='fresh', synthetic=False)
        self.archive = self.base / 'services-state.synthetic.tar.gz'
        synthetic_archive(self.root, self.archive, self.model, COMMIT, kinds={'second': 'file'})
        self.evidence = restore_synthetic(self.root, self.archive, self.model, COMMIT, self.runner)

    def runner(self, command, code):
        # These tests never contact Docker, start containers or read real state.
        self.assertEqual(command, ['docker', 'ps', '-aq'])
        return b''

    def check(self, *, failures=None, manifest=None, evidence=None, model=None):
        return verified_preflight(Legacy(self.failures if failures is None else failures), self.args,
            self.manifest if manifest is None else manifest,
            self.model if model is None else model, self.archive,
            self.evidence if evidence is None else evidence, COMMIT, self.runner)

    def test_real_restore_verifies_both_directory_and_file_before_legacy_allowance(self):
        self.assertEqual((self.root / 'data/demo/logs/synthetic-state.bin').read_bytes(),
                         b'synthetic generic service state\x00first')
        self.assertEqual((self.root / 'data/sidecar/state.bin').read_bytes(),
                         b'synthetic generic service state\x00second')
        self.check()
        self.check(failures=[])  # A future legacy verifier may learn this protocol.

    def test_missing_archive_or_receipt_never_proves_readiness(self):
        with self.assertRaisesRegex(Refused, 'SERVICES_STATE_CHECKPOINT_REQUIRED'):
            verified_preflight(Legacy(self.failures), self.args, self.manifest, self.model,
                               self.archive, None, COMMIT, self.runner)
        self.archive.unlink()
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_SOURCE_MISSING'):
            self.check()

    def test_changed_restored_bytes_extra_or_missing_file_are_refused(self):
        path = self.root / 'data/demo/logs/synthetic-state.bin'
        saved = path.read_bytes()
        path.write_bytes(b'changed')
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_RESTORED_DATA_CHANGED'):
            self.check()
        path.write_bytes(saved)
        extra = path.parent / 'extra'
        extra.write_bytes(b'not archived')
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_RESTORED_DATA_CHANGED'):
            self.check()
        extra.unlink()
        path.unlink()
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_RESTORED_DATA_CHANGED'):
            self.check()

    def test_changed_archive_inventory_receipt_or_images_are_refused(self):
        original = self.archive.read_bytes()
        self.archive.write_bytes(original + b'changed')
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_CHECKPOINT_CHANGED'):
            self.check()
        self.archive.write_bytes(original)
        path = self.root / snapshot.INVENTORY
        saved = path.read_bytes()
        path.write_bytes(saved + b'\n')
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_CHECKPOINT_CHANGED'):
            self.check()
        path.write_bytes(saved)
        evidence = dict(self.evidence, stack_commit='d' * 40)
        with self.assertRaisesRegex(Refused, 'SERVICES_STATE_SCOPE_MISMATCH'):
            self.check(evidence=evidence)
        model = copy.deepcopy(self.model)
        model['services']['demo']['image'] = 'example/service@sha256:' + 'd' * 64
        with self.assertRaisesRegex(snapshot.Refused, 'SERVICES_STATE_IMAGES_CHANGED'):
            self.check(model=model)

    def test_public_configuration_hash_must_still_match(self):
        (self.root / 'data/demo/public/settings.yaml').write_bytes(b'title: changed\n')
        with self.assertRaisesRegex(Refused, 'SERVICES_STATE_CONFIGURATION_CHANGED'):
            self.check()

    def test_public_configuration_mode_is_materialized_only_for_matching_bytes(self):
        path = self.root / 'data/demo/public/settings.yaml'
        path.chmod(0o600)
        if os.name != 'nt':
            with self.assertRaisesRegex(Refused, 'SERVICES_STATE_CONFIGURATION_CHANGED'):
                self.check()
        configuration_files(self.root, self.value, apply_modes=True)
        self.check()
        path.write_bytes(b'changed public configuration')
        with self.assertRaisesRegex(Refused, 'SERVICES_STATE_CONFIGURATION_CHANGED'):
            configuration_files(self.root, self.value, apply_modes=True)

    def test_unit_id_service_classification_and_asset_must_match_verified_resource(self):
        for key, value in (('id', 'service-other'), ('resource_id', 'other'),
                           ('services', ['other']), ('classification', 'DISPOSABLE_STATE'),
                           ('recovery_asset', 'unrelated.age')):
            manifest = copy.deepcopy(self.manifest)
            manifest['state_units'][0][key] = value
            with self.subTest(key=key):
                with self.assertRaisesRegex(Refused, 'SERVICES_STATE_UNIT_MISMATCH'):
                    self.check(manifest=manifest)

    def test_other_missing_sources_and_required_state_errors_cannot_be_excused(self):
        for failure in ('npm REQUIRED_SOURCE_MISSING', 'headscale LEGACY_STATE_MISSING',
                        'unknown service-unknown:REQUIRED_USER_CONFIGURATION_NOT_RECONSTRUCTED'):
            with self.subTest(failure=failure):
                with self.assertRaisesRegex(Refused, 'DR_PREFLIGHT_UNVERIFIED_FAILURE'):
                    self.check(failures=self.failures + [failure])


if __name__ == '__main__':
    unittest.main()
