"""Prospective public export checks use the real guard without source writes."""
import copy
import json
import os
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest import mock

import test_public_export as fixtures

sync, export = fixtures.sync, fixtures.export
CONFIGURATION = 'data/software-dashboard/public/services.yaml'
PUBLIC_CONTENT = (b'- Management:\n'
                  b'  - Proxmox Backup Server:\n      href: "#"\n'
                  b'  - Nginx Proxy Manager:\n      href: "#"\n')


class ProspectivePublicExportTests(unittest.TestCase):
    setUp = fixtures.PublicExportTests.setUp
    tearDown = fixtures.PublicExportTests.tearDown
    compose = fixtures.PublicExportTests.compose
    apply = fixtures.PublicExportTests.apply
    render = fixtures.PublicExportTests.render
    honey = fixtures.PublicExportTests.honey

    def snapshot(self):
        return {path.relative_to(self.source).as_posix():
                (path.read_bytes(), path.stat().st_mode)
                for path in self.source.rglob('*') if path.is_file()}

    def software_honey(self, *, icon=None, live_icon=None):
        reviewed = sync.loads(self.published_honey)
        pbs = next(i for i, row in enumerate(reviewed['services'])
                   if row.get('icon', '').endswith('/proxmox-backup-server.svg'))
        npm = next(i for i, row in enumerate(reviewed['services'])
                   if row.get('href', '').endswith(':81'))
        if icon is not None: reviewed['services'][pbs]['icon'] = icon
        self.public[self.honey_template] = json.dumps(reviewed).encode()
        self.write = fixtures.fixture(self.source, self.public)
        name, live = self.honey()
        # The real collision is a discarded description, not the complete name.
        live['services'][pbs]['name'] = 'PBS'
        live['services'][pbs]['desc'] = 'Backup Server'
        live['services'][npm]['name'] = 'Nginx Proxy Manager'
        if live_icon is not None: live['services'][pbs]['icon'] = live_icon
        self.write(name, json.dumps(live).encode())
        return name, live, pbs, npm

    def candidate(self, content=PUBLIC_CONTENT, *, private=False):
        compose = sync.yaml_load((self.source / 'docker-compose.yml').read_bytes())
        row = {'image': 'example/dashboard:1',
               'env_file': ['./data/software-dashboard/.env'],
               'volumes': ['./data/software-dashboard/public:/config:ro',
                           './data/software-dashboard/logs:/logs']}
        if private: row['env_file'].append('./.secrets/software-dashboard.env')
        compose['services']['software-dashboard'] = row
        resources = [dict(id='software-public-config', service='software-dashboard', type='bind',
            source='data/software-dashboard/public', target='/config', read_only=True,
            role='external', recovery={'method': 'recreate'}),
            dict(id='software-logs', service='software-dashboard', type='bind',
            source='data/software-dashboard/logs', target='/logs', read_only=False,
            role='data', recovery={'method': 'stopped_archive'})]
        inventory = dict(schema=1, resources=resources, configuration_files=[dict(
            path=CONFIGURATION, mode=0o644, sha256=sync.digest(content))])
        return {'docker-compose.yml': sync.yaml.safe_dump(compose, sort_keys=False).encode(),
                'data/software-dashboard/.env': b'TZ=Europe/Rome\n',
                CONFIGURATION: content,
                export.SERVICE_RESOURCES: (json.dumps(inventory) + '\n').encode()}

    def check(self, files, **kwargs):
        with mock.patch.object(export, 'dynamic_images', side_effect=AssertionError('Docker proof is separate')):
            return export.preflight(self.source, self.public, sync, files=files,
                directories={'data/software-dashboard/logs': {'uid': 1000, 'gid': 1000, 'mode': 0o750}}, **kwargs)

    def test_prospective_public_files_are_checked_without_writes_or_image_proofs(self):
        self.software_honey()
        before = self.snapshot()
        files = self.candidate(private=True)
        result = self.check(files, private_keys={'.secrets/software-dashboard.env': ['PRIVATE_TOKEN']})
        self.assertEqual(result['status'], 'PASS')
        self.assertEqual(result['stage'], 'PUBLIC_EXPORT')
        self.assertEqual(len(result['public_fingerprint']), 64)
        self.assertIn(CONFIGURATION, result['public_paths'])
        self.assertNotIn('.secrets/software-dashboard.env', result['public_paths'])
        self.assertEqual(self.snapshot(), before)
        self.assertFalse((self.source / CONFIGURATION).exists())
        self.assertFalse((self.source / 'data/software-dashboard/logs').exists())
        self.assertFalse((self.source / '.secrets/software-dashboard.env').exists())
        self.assertEqual(self.check(files, private_keys={'.secrets/software-dashboard.env': ['PRIVATE_TOKEN']}), result)

    def test_actual_honey_description_and_name_project_only_complete_software_captions(self):
        name, live, _, _ = self.software_honey()
        files = self.candidate()
        self.check(files)
        for path, data in files.items():
            self.write(path, data)
        (self.source / CONFIGURATION).chmod(0o644)
        (self.source / 'data/software-dashboard/logs').mkdir(parents=True)
        changes = self.render()
        self.assertEqual(changes[CONFIGURATION], PUBLIC_CONTENT)
        self.assertEqual(changes[export.SERVICE_RESOURCES], files[export.SERVICE_RESOURCES])
        self.assertEqual(sync.loads(changes.get(self.honey_template, self.public[self.honey_template])),
                         sync.loads(self.published_honey))
        self.apply(changes)
        self.assertEqual(self.render(), {})
        self.assertEqual((self.source / name).read_bytes(), json.dumps(live).encode())

    def test_private_corpus_is_not_globally_approved_by_a_software_title(self):
        self.software_honey()
        for extra in (b'      description: Backup Server\n', b'      href: "Backup Server"\n',
                      b'# Backup Server\n'):
            with self.subTest(extra=extra.split(b':')[0]):
                data = b'- Proxmox Backup Server:\n      icon: mdi-server\n' + extra
                before = self.snapshot()
                with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
                    self.check(self.candidate(data))
                self.assertEqual(self.snapshot(), before)
        self.write('docs/new.md', b'A copy of Backup Server outside a caption.\n')
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE docs/new.md'):
            self.check(self.candidate())

    def test_personal_prefix_suffix_and_partial_caption_do_not_get_projected(self):
        self.software_honey()
        for label in ('Personal Proxmox Backup Server', 'Proxmox Backup Server Personal', 'Backup Server'):
            with self.subTest(label=label):
                with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
                    self.check(self.candidate(('- ' + label + ':\n    href: "#"\n').encode()))

    def test_an_unmanaged_public_yaml_file_has_no_caption_exception(self):
        self.software_honey()
        self.write('data/software-dashboard/public/unmanaged.yaml', PUBLIC_CONTENT)
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE .*unmanaged.yaml'):
            self.check(self.candidate())

    def test_private_env_and_later_explicit_binding_override_caption_provenance(self):
        self.software_honey()
        for value in ('Backup Server', 'Nginx Proxy Manager'):
            with self.subTest(value_length=len(value)):
                self.write('.secrets/brand.env', ('PRIVATE_TOKEN=' + value + '\n').encode())
                with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
                    self.check(self.candidate())
                (self.source / '.secrets/brand.env').unlink()
        files = self.candidate()
        # Existing real private targets can never be hidden by key-only stubs.
        secret = next(iter(sync.env_values((self.source / '.secrets/aiomanager.env').read_bytes()).values()))
        data = ('- Example:\n    description: ' + secret + '\n').encode()
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
            self.check(self.candidate(data), private_keys={'.secrets/aiomanager.env': ['OTHER_KEY']})

    def test_caption_exception_does_not_survive_a_later_private_binding(self):
        name, live, _, _ = self.software_honey()
        guard = export.Guard(self.source, self.public, sync, lambda path: (self.source / path).read_bytes())
        item = next(row for row in guard.plan['files'] if row.get('destination') == name)
        export.json_template_trees(item, self.public[item['template']], json.dumps(live).encode(), sync,
            public_services=tuple(sync.yaml_load(self.public['docker-compose.yml'])['services']), guard=guard)
        guard.managed_public_files.add(CONFIGURATION)
        self.assertEqual(guard.scan(PUBLIC_CONTENT, CONFIGURATION), PUBLIC_CONTENT)
        for value in ('Backup Server', 'Nginx Proxy Manager'):
            with self.subTest(value_length=len(value)):
                guard.bindings('@@INFISICAL:/synthetic/PRIVATE_TOKEN@@', value)
                self.assertNotIn(value, guard.discarded_honey_alias_values)
                with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
                    guard.scan(PUBLIC_CONTENT, CONFIGURATION)

    def test_invalid_public_yaml_reports_position_without_parser_contents(self):
        content = b'title: [unterminated\n'
        files = self.candidate(content)
        with self.assertRaises(sync.Refused) as caught:
            self.check(files)
        diagnostic = export.safe_preflight_diagnostic(caught.exception, prospective_paths=files, baseline=self.public)
        self.assertEqual(diagnostic['code'], 'PUBLIC_DECLARATION_INVALID')
        self.assertEqual(diagnostic['path'], CONFIGURATION)
        self.assertTrue(diagnostic['lines'])
        self.assertNotIn('unterminated', json.dumps(diagnostic))

    def test_reviewed_catalog_provenance_is_same_row_and_has_no_live_or_url_escape(self):
        prefix = 'https://cdn.jsdelivr.net/gh/homarr-labs/dashboard-icons/svg/'
        invalid = ('https://example.invalid/proxmox-backup-server.svg',
                   prefix + '../proxmox-backup-server.svg', prefix + '%70roxmox-backup-server.svg',
                   prefix + 'proxmox-backup-server.svg?private=1', prefix + 'proxmox-backup-server.svg#fragment',
                   prefix.replace('cdn.jsdelivr.net', 'cdn.jsdelivr.net.evil.invalid') + 'proxmox-backup-server.svg',
                   prefix.replace('https://', 'https://user@') + 'proxmox-backup-server.svg',
                   prefix + '12345678.svg')
        for icon in invalid:
            with self.subTest(icon_length=len(icon)):
                self.software_honey(icon=icon)
                with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
                    self.check(self.candidate())
        self.software_honey(icon='https://example.invalid/opaque.svg',
                            live_icon=prefix + 'proxmox-backup-server.svg')
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
            self.check(self.candidate())
        _, live, _, _ = self.software_honey(icon='https://example.invalid/opaque.svg')
        reviewed = sync.loads(self.public[self.honey_template])
        reviewed['services'][0]['icon'] = prefix + 'proxmox-backup-server.svg'
        self.public[self.honey_template] = json.dumps(reviewed).encode()
        live['services'][0]['icon'] = reviewed['services'][0]['icon']
        name = 'data/honey/data/config/config.json'
        self.write(name, json.dumps(live).encode())
        with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
            self.check(self.candidate())

    def test_live_only_image_and_registry_or_tag_names_are_not_public_provenance(self):
        name, live, pbs, _ = self.software_honey(icon='https://example.invalid/opaque.svg')
        for label, image in (('Private New App', 'example/private-new-app:1'),
                             ('Public Registry', 'public-registry/other-app:1'),
                             ('Private Version', 'example/other-app:private-version')):
            with self.subTest(label=label):
                live['services'][pbs]['name'] = label
                live['services'][pbs]['desc'] = ''
                self.write(name, json.dumps(live).encode())
                files = self.candidate(('- ' + label + ':\n    href: "#"\n').encode())
                compose = sync.yaml_load(files['docker-compose.yml'])
                compose['services']['software-dashboard']['image'] = image
                files['docker-compose.yml'] = sync.yaml.safe_dump(compose, sort_keys=False).encode()
                with self.assertRaisesRegex(sync.Refused, 'PRIVATE_VALUE_IN_PUBLIC_FILE'):
                    self.check(files)

    def test_candidate_public_env_must_not_need_silent_private_rewriting(self):
        files = self.candidate(b'title: Safe\n')
        path = 'data/software-dashboard/.env'
        files[path] = b'TZ=Europe/Rome\nAPI_KEY=never-publish-this-literal\n'
        with self.assertRaises(sync.Refused) as caught:
            self.check(files)
        diagnostic = export.safe_preflight_diagnostic(caught.exception, prospective_paths=files, baseline=self.public)
        self.assertEqual(diagnostic['code'], 'CANDIDATE_PUBLIC_ENV_REQUIRES_PRIVATE_CLASSIFICATION')
        self.assertEqual(diagnostic['path'], path)
        self.assertNotIn('never-publish-this-literal', json.dumps(diagnostic))

    def test_candidate_does_not_override_private_or_tooling_paths(self):
        for path in ('.env', '.secrets/aiomanager.env', 'scripts/public_export.py', '../outside'):
            with self.subTest(path=path):
                with self.assertRaisesRegex(sync.Refused, 'CANDIDATE_FILES_INVALID'):
                    self.check({path: b'never written'})

    def test_candidate_detects_file_directory_conflict_and_config_hash_mismatch(self):
        self.write('data/software-dashboard', b'existing file')
        with self.assertRaisesRegex(sync.Refused, 'CANDIDATE_PATH_CONFLICT'):
            self.check(self.candidate())
        (self.source / 'data/software-dashboard').unlink()
        files = self.candidate(b'title: Safe\n')
        files[CONFIGURATION] += b'# changed\n'
        with self.assertRaisesRegex(sync.Refused, 'SERVICE_CONFIGURATION_HASH_MISMATCH'):
            self.check(files)

    def test_overlay_candidates_still_obey_existing_git_exclusions(self):
        files = self.candidate(b'title: Safe\n')
        (self.source / '.git').mkdir()
        def ignored(args, **kwargs):
            self.assertIn('check-ignore', args)
            return (CONFIGURATION + '\0').encode()
        with mock.patch.object(sync, 'run', side_effect=ignored):
            with self.assertRaisesRegex(sync.Refused, 'SERVICE_CONFIGURATION_NOT_PUBLIC'):
                self.check(files)

    def test_restore_declarations_are_checked_before_image_proofs(self):
        files = self.candidate(b'title: Safe\n')
        compose = sync.yaml_load(files['docker-compose.yml'])
        compose['services']['software-dashboard']['depends_on'] = ['missing-service']
        files['docker-compose.yml'] = sync.yaml.safe_dump(compose, sort_keys=False).encode()
        with self.assertRaisesRegex(sync.Refused, 'MISSING_SERVICE_DEPENDENCY'):
            self.check(files)
        files = self.candidate(b'title: Safe\n')
        compose = sync.yaml_load(files['docker-compose.yml'])
        compose['services']['postgres']['volumes'] = list(reversed(compose['services']['postgres']['volumes']))
        files['docker-compose.yml'] = sync.yaml.safe_dump(compose, sort_keys=False).encode()
        with self.assertRaisesRegex(sync.Refused, 'RECOVERY_VOLUME_OVERRIDE_REVIEW_REQUIRED'):
            self.check(files)

    def test_diagnostic_lines_exclude_the_legitimate_projected_caption(self):
        self.software_honey()
        content = b'- Proxmox Backup Server:\n    icon: mdi-server\n    description: Backup Server\n'
        files = self.candidate(content)
        with self.assertRaises(sync.Refused) as caught:
            self.check(files)
        diagnostic = export.safe_preflight_diagnostic(caught.exception, prospective_paths=files, baseline=self.public)
        self.assertEqual(diagnostic['lines'], [3])

    def test_private_refusal_attaches_only_safe_gui_diagnostic_metadata(self):
        self.software_honey(icon='https://example.invalid/opaque.svg')
        files = self.candidate()
        with self.assertRaises(sync.Refused) as caught:
            self.check(files)
        diagnostic = export.safe_preflight_diagnostic(caught.exception,
            prospective_paths=files, baseline=self.public)
        self.assertEqual(diagnostic['code'], 'PRIVATE_VALUE_IN_PUBLIC_FILE')
        self.assertEqual(diagnostic['path'], CONFIGURATION)
        self.assertEqual(diagnostic['lines'], [2])
        self.assertEqual(diagnostic['references'], [])
        self.assertNotIn('Backup Server', json.dumps(diagnostic))
        error = sync.Refused('UNREVIEWED_REASON PRIVATE_CONTENT_MUST_NOT_LOG')
        error.public_preflight_diagnostic = dict(path='.secrets/private.env',
            lines=[True, -1, 0, 2, '3'], references=['/private/UNPUBLISHED'])
        diagnostic = export.safe_preflight_diagnostic(error, prospective_paths=files, baseline=self.public)
        self.assertEqual(diagnostic['code'], 'PUBLIC_DECLARATION_INVALID')
        self.assertNotIn('path', diagnostic)
        self.assertEqual(diagnostic['lines'], [2])
        self.assertEqual(diagnostic['references'], [])
        self.assertNotIn('PRIVATE_CONTENT', json.dumps(diagnostic))


if __name__ == '__main__':
    unittest.main()
