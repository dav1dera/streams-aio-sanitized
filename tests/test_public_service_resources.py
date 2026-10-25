"""Public service resource metadata, with synthetic offline inputs."""
import copy
import hashlib
import json
import unittest

import test_public_export as fixtures

sync, export = fixtures.sync, fixtures.export


class PublicServiceResourcesTests(unittest.TestCase):
    setUp = fixtures.PublicExportTests.setUp
    tearDown = fixtures.PublicExportTests.tearDown
    render = fixtures.PublicExportTests.render
    compose = fixtures.PublicExportTests.compose
    apply = fixtures.PublicExportTests.apply

    def managed(self):
        content = b'title: Synthetic dashboard\n'
        name = 'data/added/public/config/settings.yaml'
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
