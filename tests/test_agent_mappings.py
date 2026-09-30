"""Real template forms with synthetic project names; no secrets or Agent reads."""
import importlib.util
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import dr
spec = importlib.util.spec_from_file_location(
    'mapping_check', ROOT / 'scripts/verify-agent-dr-mappings.py')
m = importlib.util.module_from_spec(spec)
spec.loader.exec_module(m)

AIOMETADATA = r'''{{- with secret "synthetic-project" "prod" "/aiometadata" }}
{{- range . }}
{{ printf "%s=%s\n" .Key .Value }}
{{- end }}
{{- end }}
'''
AIOSTREAMS = r'''{{- with secret "synthetic-project" "prod" "/aiostreams" }}
{{- range . }}
{{- if eq .Key "CONFIG_ACCESS_KEY" }}
{{ printf "%s='%s'\n" .Key .Value }}
{{- else }}
{{ printf "%s=%s\n" .Key .Value }}
{{- end }}
{{- end }}
{{- end }}
{{ with secret "synthetic-project" "prod" "/shared" }}
{{ range . }}
{{ if eq .Key "JACKETT_API_KEY" }}BUILTIN_JACKETT_API_KEY={{ .Value }}
{{ else if eq .Key "TMDB_API_KEY" }}TMDB_API_KEY={{ .Value }}
{{ else if eq .Key "COMET_PUBLIC_API_TOKEN" }}COMET_PUBLIC_API_TOKEN={{ .Value }}
{{ else if eq .Key "ZILEAN_URL" }}BUILTIN_ZILEAN_URL={{ .Value }}
{{ end }}
{{ end }}
{{ end }}
'''
EXPECTED = {
    'folders': ['/aiostreams'],
    'overrides': {
        'BUILTIN_JACKETT_API_KEY': {'path': '/shared', 'key': 'JACKETT_API_KEY'},
        'TMDB_API_KEY': {'path': '/shared', 'key': 'TMDB_API_KEY'},
        'COMET_PUBLIC_API_TOKEN': {'path': '/shared', 'key': 'COMET_PUBLIC_API_TOKEN'},
        'BUILTIN_ZILEAN_URL': {'path': '/shared', 'key': 'ZILEAN_URL'},
    },
}


def generic(body, path='/aiometadata'):
    return '{{ range secret "synthetic-project" "prod" "' + path + '" }}' + body + '{{ end }}'


class MappingTests(unittest.TestCase):
    def test_real_aiometadata_form(self):
        self.assertEqual(m.extract(AIOMETADATA), {'folders': ['/aiometadata'], 'overrides': {}})

    def test_real_aiostreams_form(self):
        self.assertEqual(m.extract(AIOSTREAMS), EXPECTED)
        self.assertNotIn('CONFIG_ACCESS_KEY', m.extract(AIOSTREAMS)['overrides'])

    def test_existing_direct_range_and_quoted_value(self):
        self.assertEqual(m.extract(generic('{{ .Key }}={{ printf "%q" .Value }}\n')),
                         {'folders': ['/aiometadata'], 'overrides': {}})

    def test_printf_without_embedded_newline(self):
        for fmt in ('%s=%s', "%s='%s'"):
            with self.subTest(fmt=fmt):
                self.assertEqual(m.extract(generic('{{ printf ' + json.dumps(fmt) + ' .Key .Value }}\n')),
                                 {'folders': ['/aiometadata'], 'overrides': {}})

    def test_all_formatting_branches_remain_generic(self):
        body = r'''{{ if eq .Key "FIRST_KEY" }}{{ printf "%s='%s'\n" .Key .Value }}{{ else if eq .Key "SECOND_KEY" }}{{ .Key }}={{ printf "%q" .Value }}
{{ else }}{{ .Key }}={{ .Value }}
{{ end }}'''
        self.assertEqual(m.extract(generic(body)), {'folders': ['/aiometadata'], 'overrides': {}})

    def test_explicit_identity_assignment_with_generic_fallback(self):
        body = r'''{{ if eq .Key "CONFIG_ACCESS_KEY" }}CONFIG_ACCESS_KEY='{{ .Value }}'
{{ else }}{{ .Key }}={{ .Value }}
{{ end }}'''
        self.assertEqual(m.extract(generic(body, '/aiostreams')),
                         {'folders': ['/aiostreams'], 'overrides': {}})

    def test_equivalent_nested_formatting_condition(self):
        body = r'''{{ if eq .Key "FIRST_KEY" }}{{ if eq .Key "FIRST_KEY" }}{{ .Key }}={{ .Value }}
{{ end }}{{ else }}{{ printf "%s=%s\n" .Key .Value }}{{ end }}'''
        self.assertEqual(m.extract(generic(body)), {'folders': ['/aiometadata'], 'overrides': {}})

    def test_comments_and_trim_markers(self):
        template = '# Public description\n{{ /* public comment */ }}' + AIOMETADATA
        self.assertEqual(m.extract(template), {'folders': ['/aiometadata'], 'overrides': {}})

    def test_explicit_single_shared_override(self):
        body = '{{ if eq .Key "JACKETT_API_KEY" }}BUILTIN_JACKETT_API_KEY={{ printf "%q" .Value }}\n{{ end }}'
        self.assertEqual(m.extract(generic(body, '/shared')),
                         {'folders': [], 'overrides': {'BUILTIN_JACKETT_API_KEY': {'path': '/shared', 'key': 'JACKETT_API_KEY'}}})

    def test_unknown_expression_refused_even_in_branch(self):
        for text in ('{{ unsupportedFunction .Value }}',
                     generic('{{ if eq .Key "FIRST_KEY" }}{{ unknown .Value }}{{ else }}{{ .Key }}={{ .Value }}\n{{ end }}')):
            with self.subTest():
                with self.assertRaises(dr.Refused):
                    m.extract(text)

    def test_ambiguous_or_unsupported_templates_blocked(self):
        templates = [
            '{{ range . }}{{ .Key }}={{ .Value }}\n{{ end }}',
            '{{ .Value }}',
            '{{ end }}',
            '{{ with secret "synthetic-project" "prod" "/aiometadata" }}',
            generic('{{ if eq .Key "ONLY_ONE" }}{{ .Key }}={{ .Value }}\n{{ end }}'),
            generic('DEST={{ .Value }}\n', '/shared'),
            generic('{{ .Key }}=prefix{{ .Value }}\n'),
            generic('{{ .Key }}={{ printf "%s" .Key }}\n'),
            generic('{{ printf "%s=%s" .Value .Key }}\n'),
            generic('{{ .Key }}={{ .Value | unknown }}\n'),
            generic('{{ if ne .Key "ONLY_ONE" }}{{ .Key }}={{ .Value }}\n{{ end }}'),
            generic('{{ if eq .Key "FIRST_KEY" }}FIRST={{ .Value }}\n{{ else }}OTHER={{ .Value }}\n{{ end }}', '/shared'),
            generic('{{ if eq .Key "FIRST_KEY" }}FIRST={{ .Value }}\n{{ else if eq .Key "FIRST_KEY" }}SECOND={{ .Value }}\n{{ end }}', '/shared'),
            generic('{{ if eq .Key "FIRST_KEY" }}DEST={{ .Value }}\n{{ else if eq .Key "SECOND_KEY" }}DEST={{ .Value }}\n{{ end }}', '/shared'),
            generic('{{ range . }}{{ .Key }}={{ .Value }}\n{{ end }}'),
            AIOMETADATA + AIOMETADATA,
            AIOMETADATA + AIOSTREAMS.replace('"prod"', '"other-environment"'),
            generic('# ignored {{ .Value }}\n'),
            generic('LITERAL=synthetic-fixed-value\n'),
            '# commented assignment: ' + AIOMETADATA,
            '# prefix ' + AIOMETADATA + 'LITERAL=synthetic-fixed-value\n',
        ]
        for template in templates:
            with self.subTest(template_number=templates.index(template)):
                with self.assertRaises(dr.Refused):
                    m.extract(template)

    def test_cli_expected_output_and_unknown_blocked(self):
        with tempfile.TemporaryDirectory() as directory:
            config = Path(directory) / 'synthetic-agent.json'
            templates = [{'destination-path': str(ROOT / '.secrets' / f'{service}.env'), 'template-content': content}
                         for service, content in [('aiometadata', AIOMETADATA), ('aiostreams', AIOSTREAMS)]]
            config.write_text(json.dumps({'templates': templates}))
            args = [sys.executable, '-B', str(ROOT / 'scripts/verify-agent-dr-mappings.py'),
                    '--target', str(ROOT), '--agent-config', str(config)]
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertEqual(result.returncode, 0)
            self.assertEqual(result.stdout.splitlines(), [
                'aiometadata AGENT_MAPPING PASS',
                'aiometadata /aiometadata -> .secrets/aiometadata.env',
                'aiostreams AGENT_MAPPING PASS',
                'aiostreams /aiostreams -> .secrets/aiostreams.env',
                'aiostreams /shared/JACKETT_API_KEY -> BUILTIN_JACKETT_API_KEY',
                'aiostreams /shared/TMDB_API_KEY -> TMDB_API_KEY',
                'aiostreams /shared/COMET_PUBLIC_API_TOKEN -> COMET_PUBLIC_API_TOKEN',
                'aiostreams /shared/ZILEAN_URL -> BUILTIN_ZILEAN_URL',
                'TOTAL PASS',
            ])
            templates[1]['template-content'] += '{{ unknown .Value }}'
            config.write_text(json.dumps({'templates': templates}))
            result = subprocess.run(args, capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertIn('aiostreams AGENT_MAPPING BLOCKED', result.stdout)
            self.assertTrue(result.stdout.endswith('TOTAL BLOCKED\n'))


if __name__ == '__main__':
    unittest.main()
