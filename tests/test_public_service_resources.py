"""Public service resource metadata, with synthetic offline inputs."""
import copy
import hashlib
import json
import os
from pathlib import Path
import shutil
import stat
import struct
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

import test_public_export as fixtures

sync, export = fixtures.sync, fixtures.export


UNDEFINED_ID = 0xffffffff


def access_acl(*, owner=6, group=4, mask=7, other=4, named=((2, 7, 12345),)):
    entries = [(1, owner, UNDEFINED_ID), *named, (4, group, UNDEFINED_ID),
               (16, mask, UNDEFINED_ID), (32, other, UNDEFINED_ID)]
    return struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in entries)


class PublicConfigurationModeTests(unittest.TestCase):
    def matches(self, observed=0o674, declared=0o644, acl=None, failure=None):
        target = mock.Mock()
        target.stat.return_value = SimpleNamespace(st_mode=stat.S_IFREG | observed)
        with mock.patch.object(export.os, 'getxattr', create=True,
                               return_value=access_acl() if acl is None else acl,
                               side_effect=failure) as read_acl:
            result = export.public_configuration_mode_matches(target, declared)
        return result, read_acl

    def test_exact_mode_does_not_need_acl_support(self):
        result, read_acl = self.matches(observed=0o644, failure=OSError('unsupported'))
        self.assertTrue(result)
        read_acl.assert_not_called()

    def test_only_extended_acl_mask_widening_is_accepted(self):
        for acl in (access_acl(), access_acl(group=7),
                    access_acl(named=((8, 7, 12345),)),
                    access_acl(named=((2, 7, 12345), (8, 6, 12345)))):
            with self.subTest(acl=acl):
                result, read_acl = self.matches(acl=acl)
                self.assertTrue(result)
                self.assertEqual(read_acl.call_args.args[1], 'system.posix_acl_access')
                self.assertEqual(read_acl.call_args.kwargs, {'follow_symlinks': False})

    def test_owning_group_keeps_declared_access_even_with_a_wide_mask(self):
        for owning_group in range(8):
            with self.subTest(owning_group=owning_group):
                result, _ = self.matches(acl=access_acl(group=owning_group, mask=7))
                self.assertEqual(result, bool(owning_group & 4))

    def test_mode_changes_outside_group_and_lost_group_access_are_refused(self):
        for observed in (0o774, 0o670, 0o604, 0o4674):
            with self.subTest(observed=oct(observed)):
                result, read_acl = self.matches(observed=observed)
                self.assertFalse(result)
                read_acl.assert_not_called()

    def test_group_difference_without_access_acl_is_refused(self):
        for failure in (OSError('no ACL'), AttributeError('no getxattr'), TypeError('unsupported')):
            with self.subTest(failure=type(failure).__name__):
                self.assertFalse(self.matches(failure=failure)[0])

    def test_invalid_or_inconsistent_acl_is_refused(self):
        simple = struct.pack('<I', 2) + b''.join(struct.pack('<HHI', *entry) for entry in
            ((1, 6, UNDEFINED_ID), (4, 7, UNDEFINED_ID), (32, 4, UNDEFINED_ID)))
        invalid = [b'', b'\x02\x00\x00', access_acl()[:-1],
                   struct.pack('<I', 3) + access_acl()[4:], simple,
                   access_acl(mask=6), access_acl(owner=7), access_acl(other=0),
                   access_acl(named=()), access_acl(named=((2, 7, UNDEFINED_ID),)),
                   access_acl(named=((2, 7, 12345), (2, 4, 12345))),
                   access_acl(named=((64, 7, 12345),)),
                   access_acl(named=((2, 8, 12345),)),
                   access_acl() + struct.pack('<HHI', 4, 4, UNDEFINED_ID),
                   access_acl().replace(struct.pack('<HHI', 4, 4, UNDEFINED_ID),
                                        struct.pack('<HHI', 4, 4, 1))]
        for acl in invalid:
            with self.subTest(acl=acl):
                self.assertFalse(self.matches(acl=acl)[0])

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'getxattr') and shutil.which('setfacl'),
                         'Linux POSIX ACL tools required')
    def test_real_inherited_acl_mask_widening_preserves_file_and_acl(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            named_uid = os.getuid() + 1
            subprocess.run(['setfacl', '-d', '-m',
                'u::rwx,u:' + str(named_uid) + ':rwx,g::rwx,m::rwx,o::r-x', str(folder)],
                check=True, capture_output=True)
            target = folder / 'settings.yaml'
            target.write_bytes(b'title: Public dashboard\n')
            target.chmod(0o644)
            subprocess.run(['setfacl', '-m', 'u:' + str(named_uid) + ':rwx', str(target)],
                           check=True, capture_output=True)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o674)
            before_data = target.read_bytes()
            before_acl = os.getxattr(target, 'system.posix_acl_access')
            group_obj = [permissions for tag, permissions, _ in
                         struct.iter_unpack('<HHI', before_acl[4:]) if tag == 4]
            self.assertEqual(group_obj, [7])
            self.assertTrue(export.public_configuration_mode_matches(target, 0o644))
            self.assertEqual(target.read_bytes(), before_data)
            self.assertEqual(os.getxattr(target, 'system.posix_acl_access'), before_acl)
            subprocess.run(['setfacl', '-m', 'g::---,m::rwx', str(target)],
                           check=True, capture_output=True)
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o674)
            self.assertFalse(export.public_configuration_mode_matches(target, 0o644))
            subprocess.run(['setfacl', '-m', 'g::rwx', str(target)],
                           check=True, capture_output=True)
            self.assertTrue(export.public_configuration_mode_matches(target, 0o644))
            target.chmod(0o604)
            self.assertFalse(export.public_configuration_mode_matches(target, 0o644))
            subprocess.run(['setfacl', '-b', str(target)], check=True, capture_output=True)
            target.chmod(0o674)
            self.assertFalse(export.public_configuration_mode_matches(target, 0o644))


class PublicServiceResourcesTests(unittest.TestCase):
    setUp = fixtures.PublicExportTests.setUp
    tearDown = fixtures.PublicExportTests.tearDown
    render = fixtures.PublicExportTests.render
    compose = fixtures.PublicExportTests.compose
    apply = fixtures.PublicExportTests.apply

    def managed(self, *, filename='settings.yaml', content=b'title: Synthetic dashboard\n'):
        name = 'data/added/public/config/' + filename
        self.write(name, content)
        self.write('data/added/.env', b'PORT=8080\n')
        self.write('data/added/state/runtime.db', b'private application data never published')
        def service(rows):
            rows['added'] = {'image': 'example/new:1', 'env_file': ['./data/added/.env'], 'volumes': [
                {'type': 'bind', 'source': './data/added/public/config', 'target': '/config', 'read_only': True},
                {'type': 'bind', 'source': './data/added/state', 'target': '/state', 'read_only': False},
                {'type': 'bind', 'source': '/var/run/docker.sock', 'target': '/socket', 'read_only': True},
                {'type': 'volume', 'source': 'added-data', 'target': '/volume', 'read_only': False}]}
        self.compose(service)
        document = sync.yaml_load((self.source / 'docker-compose.yml').read_bytes())
        document.setdefault('volumes', {})['added-data'] = {}
        self.write('docker-compose.yml', sync.yaml.safe_dump(document, sort_keys=False).encode())
        value = {'schema': 1, 'resources': [
            dict(id='public-config', service='added', type='bind', source='data/added/public/config', target='/config',
                 read_only=True, role='external', recovery={'method': 'recreate'}),
            dict(id='persistent-state', service='added', type='bind', source='data/added/state', target='/state',
                 read_only=False, role='data', recovery={'method': 'stopped_archive'}),
            dict(id='runtime-socket', service='added', type='bind', source='/var/run/docker.sock', target='/socket',
                 read_only=True, role='runtime', recovery={'method': 'recreate'}),
            dict(id='named-data', service='added', type='volume', source='added-data', target='/volume',
                 read_only=False, role='data', recovery={'method': 'stopped_archive'})],
            'configuration_files': [dict(path=name, mode=0o644, sha256=hashlib.sha256(content).hexdigest())]}
        raw = (json.dumps(value, indent=4) + '\n').encode()
        self.write(export.SERVICE_RESOURCES, raw)
        return value, raw, name

    def test_resources_and_hashes_are_public_but_runtime_bytes_are_private(self):
        value, raw, name = self.managed()
        result = self.render()
        self.assertEqual(result[export.SERVICE_RESOURCES], raw)
        self.assertEqual(result[name], b'title: Synthetic dashboard\n')
        self.assertNotIn('data/added/state/runtime.db', result)
        self.assertTrue(export.allowed(export.SERVICE_RESOURCES))
        layout = sync.loads(result[export.LAYOUT])
        self.assertEqual(layout['service_resources_inventory'],
                         {'path': export.SERVICE_RESOURCES, 'sha256': sync.digest(raw)})
        manifest = sync.loads(result['config/dr-manifest.yaml'])
        refs = [row for row in manifest['references'] if row['SERVICE'] == 'added']
        data_refs = [row for row in refs if row['RESTORE_METHOD'] == 'ENCRYPTED_SERVICE_STATE']
        self.assertEqual({row['RESOURCE_ID'] for row in data_refs}, {'persistent-state', 'named-data'})
        self.assertTrue(all(row['RECOVERY_ASSET'] == 'services-state.tar.gz.age' for row in data_refs))
        units = [row for row in manifest['state_units'] if row.get('resource_id')]
        self.assertEqual(len(units), 2)
        self.assertTrue(all(row['backup_required'] and row['recovery_asset'] == 'services-state.tar.gz.age' for row in units))
        self.assertFalse(any(row['path'] == 'data/added/state' for row in layout['empty_directories']))
        self.apply(result)
        self.assertEqual(self.render(), {})

    def test_dashboard_stylesheet_is_published_with_its_exact_recovery_hash(self):
        content = (b'/* Public dashboard background and compact cards. */\n'
                   b'body { background: url("data:image/png;base64,aW1hZ2U="); }\n'
                   b'.service-icon { width: 24px; }\n')
        _, inventory, name = self.managed(filename='custom.css', content=content)
        result = self.render()
        self.assertEqual(result[name], content)
        self.assertEqual(result[export.SERVICE_RESOURCES], inventory)
        manifest = sync.loads(result['config/dr-manifest.yaml'])
        references = [row for row in manifest['references']
                      if row['SERVICE'] == 'added' and row['TYPE'] == 'CONFIGURATION_FILE']
        self.assertEqual([(row['SOURCE'], row['SHA256'], row['MODE']) for row in references],
                         [(name, sync.digest(content), 0o644)])
        self.apply(result)
        self.assertEqual(self.render(), {})

    @unittest.skipUnless(os.name == 'posix' and hasattr(os, 'getxattr') and shutil.which('setfacl'),
                         'Linux POSIX ACL tools required')
    def test_public_acl_mask_does_not_change_exported_inventory_or_hash(self):
        _, inventory, name = self.managed()
        target = self.source / name
        target.chmod(0o644)
        subprocess.run(['setfacl', '-m', 'u:' + str(os.getuid() + 1) + ':rwx', str(target)],
                       check=True, capture_output=True)
        self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o674)
        result = self.render()
        self.assertEqual(result[export.SERVICE_RESOURCES], inventory)
        self.assertEqual(result[name], b'title: Synthetic dashboard\n')
        references = [row for row in sync.loads(result['config/dr-manifest.yaml'])['references']
                      if row['TYPE'] == 'CONFIGURATION_FILE' and row['SOURCE'] == name]
        self.assertEqual([(row['MODE'], row['SHA256']) for row in references],
                         [(0o644, sync.digest(result[name]))])
        self.apply(result)
        self.assertEqual(self.render(), {})
        target.write_bytes(b'title: Changed despite valid ACL\n')
        with self.assertRaisesRegex(sync.Refused, 'SERVICE_CONFIGURATION_HASH_MISMATCH'):
            self.render()

    def test_stylesheet_still_refuses_known_private_values(self):
        secret = next(iter(sync.env_values((self.source / '.secrets/comet.env').read_bytes()).values()))
        content = ('/* ' + secret + ' */\nbody { color: white; }\n').encode()
        self.managed(filename='custom.css', content=content)
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE') as error:
            self.render()
        self.assertNotIn(secret, str(error.exception))

    def test_stylesheet_still_refuses_binary_content_and_credential_patterns(self):
        for content, code in ((b'body { color: white; }\x00', 'BINARY_FILE_REFUSED'),
                              (b'/* ghp_' + b'a' * 36 + b' */\n', 'CREDENTIAL_PATTERN_REFUSED')):
            with self.subTest(code=code):
                self.managed(filename='custom.css', content=content)
                with self.assertRaisesRegex(sync.Refused, code):
                    self.render()

    def test_stale_mount_and_runtime_archive_are_closed(self):
        value, _, _ = self.managed()
        value['resources'][1]['source'] = 'data/added/other'
        with self.assertRaisesRegex(sync.Refused, '^SERVICE_RESOURCES_COMPOSE_MISMATCH$'):
            export.service_inventory(value, sync.yaml_load((self.source / 'docker-compose.yml').read_bytes()), sync)
        value, _, _ = self.managed()
        value['resources'][2]['recovery']['method'] = 'stopped_archive'
        self.write(export.SERVICE_RESOURCES, json.dumps(value).encode())
        with self.assertRaisesRegex(sync.Refused, 'SERVICE_RUNTIME_ARCHIVE_REFUSED'):
            self.render()

    def test_configuration_hash_and_missing_file_are_refused(self):
        value, _, name = self.managed()
        self.write(name, b'title: changed\n')
        with self.assertRaisesRegex(sync.Refused, 'SERVICE_CONFIGURATION_HASH_MISMATCH'):
            self.render()
        (self.source / name).unlink()
        with self.assertRaisesRegex(sync.Refused, 'SOURCE_FILE_MISSING_OR_OVERSIZED'):
            self.render()

    def test_extra_metadata_and_nonpublic_configuration_are_refused(self):
        value, _, _ = self.managed()
        compose = sync.yaml_load((self.source / 'docker-compose.yml').read_bytes())
        bad = copy.deepcopy(value)
        bad['configuration_files'][0]['content'] = 'never a metadata field'
        with self.assertRaisesRegex(sync.Refused, 'SERVICE_CONFIGURATION_METADATA_INVALID'):
            export.service_inventory(bad, compose, sync)
        value['configuration_files'][0]['path'] = 'data/added/config/settings.yaml'
        with self.assertRaisesRegex(sync.Refused, 'SERVICE_CONFIGURATION_METADATA_INVALID'):
            export.service_inventory(value, compose, sync)

    def test_public_configuration_still_passes_the_complete_private_guard(self):
        value, _, name = self.managed()
        secret = next(iter(sync.env_values((self.source / '.secrets/comet.env').read_bytes()).values()))
        data = ('label: ' + secret + '\n').encode()
        self.write(name, data)
        value['configuration_files'][0]['sha256'] = sync.digest(data)
        self.write(export.SERVICE_RESOURCES, json.dumps(value).encode())
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE') as error:
            self.render()
        self.assertNotIn(secret, str(error.exception))


if __name__ == '__main__':
    unittest.main()
