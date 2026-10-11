"""Deterministic provenance using synthetic templates and ordinary parameters."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import agent_template_transform as transform
import dr

spec = importlib.util.spec_from_file_location(
    'local_mapping_check', ROOT / 'scripts/verify-agent-dr-mappings.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)


def original(folder):
    return ('{{ range secret "synthetic-project" "prod" "' + folder
            + '" }}{{ .Key }}={{ printf "%q" .Value }}\n{{ end }}')


class LocalAgentMappings(unittest.TestCase):
    def setUp(self):
        self.area = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parent)
        self.addCleanup(self.area.cleanup)
        self.root = Path(self.area.name)
        self.config_path = self.root / 'local-stack-config.json'
        self.source_path = self.root / 'local-stack-agent-source.json'
        self.addCleanup(patch.stopall)
        patch.object(m, '_LOCAL_CONFIG_PATH', self.config_path).start()
        patch.object(m, '_LOCAL_SOURCE_PATH', self.source_path).start()
        self.values = {'/aiostreams/PORT': '8000', '/aiometadata/PORT': '8001'}
        self.sources = {f'.secrets/{name}.env': original('/' + name)
                        for name in ('aiostreams', 'aiometadata')}
        self.plan = {
            'required_values': list(self.values),
            'service_folder_exports': {
                f'.secrets/{name}.env': {'path': '/' + name, 'overrides': {}}
                for name in ('aiostreams', 'aiometadata')},
        }

    @staticmethod
    def write(path, document):
        path.write_text(json.dumps(document), encoding='utf-8')
        path.chmod(0o600)

    def valid(self):
        self.write(self.config_path, {'schema': 1, 'values': self.values})
        self.write(self.source_path, {'schema': 1, 'templates': self.sources})
        return m._mapping_sources(self.plan)

    def cli(self, contents):
        (self.root / 'config').mkdir(exist_ok=True)
        self.write(self.root / 'config/dr-render-plan.json', self.plan)
        config = self.root / 'agent.json'
        self.write(config, {'templates': [
            {'destination-path': str(self.root / destination), 'template-content': text}
            for destination, text in contents.items()]})
        output = io.StringIO()
        with patch.object(sys, 'argv', ['verify', '--target', str(self.root),
                                       '--agent-config', str(config)]):
            with contextlib.redirect_stdout(output):
                result = m.main()
        return result, output.getvalue()

    def test_absent_map_retains_legacy_mapping_and_ignores_unrelated_source(self):
        self.write(self.source_path, {'not': 'provenance'})
        self.assertIsNone(m._mapping_sources(self.plan))
        self.assertEqual(self.cli(self.sources)[0], 0)

    def test_valid_all_twenty_sources_and_transformed_aio_pass(self):
        self.sources.update({f'data/service{i}/config.yaml': '# synthetic\n'
                             for i in range(18)})
        proof = self.valid()
        generated = {destination: transform.transform_template(text, self.values, m)
                     for destination, text in self.sources.items()}
        self.assertEqual(len(proof[1]), 20)
        self.assertNotEqual(generated['.secrets/aiostreams.env'],
                            self.sources['.secrets/aiostreams.env'])
        result, output = self.cli(generated)
        self.assertEqual(result, 0)
        self.assertTrue(output.endswith('TOTAL PASS\n'))
        self.assertNotIn('8000', output)

    def test_generated_literal_tampering_is_blocked(self):
        proof = self.valid()
        name = '.secrets/aiostreams.env'
        generated = transform.transform_template(self.sources[name], self.values, m)
        with self.assertRaises(dr.Refused) as failure:
            m._proven_content(generated + '\n# altered', name, proof)
        self.assertEqual(str(failure.exception), 'LOCAL_AGENT_TEMPLATE_MISMATCH')

    def test_altered_credential_expression_is_blocked(self):
        proof = self.valid()
        name = '.secrets/aiostreams.env'
        generated = transform.transform_template(self.sources[name], self.values, m)
        with self.assertRaises(dr.Refused):
            m._proven_content(generated.replace('"/aiostreams"', '"/other"'), name, proof)

    def test_proven_original_still_has_to_match_the_dr_plan(self):
        self.sources['.secrets/aiostreams.env'] = original('/other')
        self.valid()
        generated = {destination: transform.transform_template(text, self.values, m)
                     for destination, text in self.sources.items()}
        result, output = self.cli(generated)
        self.assertEqual(result, 1)
        self.assertIn('aiostreams AGENT_MAPPING DIFFERENT', output)

    def test_map_without_source_or_either_aio_template_is_blocked(self):
        self.write(self.config_path, {'schema': 1, 'values': self.values})
        with self.assertRaises(dr.Refused):
            m._mapping_sources(self.plan)
        for name in self.sources:
            self.write(self.source_path, {'schema': 1, 'templates': {
                key: text for key, text in self.sources.items() if key != name}})
            with self.subTest(destination=name), self.assertRaises(dr.Refused):
                m._mapping_sources(self.plan)

    def test_exact_schema_forbids_auth_or_filename_arguments(self):
        self.valid()
        for document in ({'schema': True, 'templates': self.sources},
                         {'schema': 2, 'templates': self.sources},
                         {'schema': 1, 'templates': self.sources, 'auth': {}},
                         {'schema': 1, 'templates': self.sources, 'file': '/outside'}):
            self.write(self.source_path, document)
            with self.subTest(document_keys=list(document)), self.assertRaises(dr.Refused):
                m._mapping_sources(self.plan)

    def test_unsafe_relative_destination_is_blocked(self):
        self.valid()
        for destination in ('/etc/outside', '../outside', 'data/../outside',
                            './data/file', 'data//file', 'C:\\outside', 'data\\file', '.'):
            self.write(self.source_path, {'schema': 1, 'templates': {
                **self.sources, destination: '# synthetic\n'}})
            with self.subTest(destination=destination), self.assertRaises(dr.Refused):
                m._mapping_sources(self.plan)

    def test_duplicate_json_keys_are_blocked(self):
        self.valid()
        self.source_path.write_text('{"schema":1,"schema":1,"templates":{}}')
        with self.assertRaises(dr.Refused):
            m._mapping_sources(self.plan)

    def test_unknown_local_reference_is_blocked_without_value_disclosure(self):
        self.valid()
        self.write(self.config_path, {'schema': 1, 'values': {
            '/shared/PRIVATE_PASSWORD': 'synthetic-canary'}})
        with self.assertRaises(dr.Refused) as failure:
            m._mapping_sources(self.plan)
        self.assertEqual(str(failure.exception), 'LOCAL_AGENT_PROVENANCE_INVALID')
        self.assertNotIn('canary', str(failure.exception))

    def test_oversize_or_control_character_source_is_blocked(self):
        self.valid()
        for content in ('x' * (1024**2 + 1), 'bad\0source'):
            self.write(self.source_path, {'schema': 1, 'templates': {
                **self.sources, 'data/service/config.yaml': content}})
            with self.subTest(oversize=len(content) > 1024**2), self.assertRaises(dr.Refused):
                m._mapping_sources(self.plan)

    @unittest.skipIf(os.name == 'nt', 'Windows ACLs are checked outside Unix mode rules')
    def test_private_documents_require_mode_600(self):
        self.valid()
        for path in (self.config_path, self.source_path):
            path.chmod(0o640)
            with self.subTest(filename=path.name), self.assertRaises(dr.Refused):
                m._mapping_sources(self.plan)
            path.chmod(0o600)

    def test_hardlinks_are_blocked(self):
        self.valid()
        linked = self.root / 'linked.json'
        try:
            os.link(self.source_path, linked)
        except OSError:
            self.skipTest('Hardlinks unavailable in this workspace')
        with self.assertRaises(dr.Refused):
            m._mapping_sources(self.plan)

    def test_symlinks_are_blocked(self):
        self.valid()
        moved = self.root / 'source-copy.json'
        self.source_path.rename(moved)
        try:
            self.source_path.symlink_to(moved)
        except OSError:
            moved.rename(self.source_path)
            self.skipTest('Symlinks unavailable in this workspace')
        with self.assertRaises(dr.Refused):
            m._mapping_sources(self.plan)


if __name__ == '__main__':
    unittest.main()
