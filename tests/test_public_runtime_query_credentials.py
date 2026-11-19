"""Runtime URL references stay public; literal credentials remain refused."""
from pathlib import Path
import sys
import unittest

from test_public_sync import sync, ROOT

sys.path.insert(0, str(ROOT / 'scripts'))
import public_export as export


class RuntimeQueryCredentialTests(unittest.TestCase):
    name = 'data/seanime/public/extensions/subtitle-engine.ts'

    def guard(self, private_values=()):
        # No published-file grandfathering: every example is new content.
        guard = export.Guard.__new__(export.Guard)
        guard.sync = sync
        guard.baseline = {}
        guard.plan = {'required_values': ['/synthetic/API_KEY']}
        guard.values = {value: {'/synthetic/API_KEY'} for value in private_values}
        guard.compose_env_decoded_values = set()
        guard.discarded_honey_alias_values = set()
        return guard

    def accepted(self, source):
        raw = source.encode('utf-8')
        self.assertEqual(self.guard().scan(raw, self.name), raw)

    def refused(self, source, code='CREDENTIAL_PATTERN_REFUSED', private_values=()):
        with self.assertRaisesRegex(sync.Refused, '^' + code + ' '):
            self.guard(private_values).scan(source.encode('utf-8'), self.name)

    def test_new_seanime_fetch_expression_passes_without_prior_publication(self):
        self.accepted('const response = await fetch(`${this.apiUrl}/download?api_key=${apiKey}`);\n')

    def test_symbolic_identifiers_and_member_references_are_supported(self):
        for value in ('apiKey', 'API_KEY', 'this.apiKey', 'settings.apiKey',
                      'CONFIG.API_KEY', '_runtime.currentToken', '$runtime.token'):
            with self.subTest(value=value):
                self.accepted('const url = `https://example.invalid/download?api_key=${' + value + '}`;')

    def test_sensitive_query_names_and_delimiters_are_consistent(self):
        for name in ('api_key', 'apikey', 'ApiKey', 'TOKEN', 'password', 'secret', 'AUTH'):
            for ending in ('', '&page=2', '#part', '`);', '"', "'", ' '):
                with self.subTest(name=name, ending=ending):
                    self.accepted('https://example.invalid/download?' + name + '=${state.currentKey}' + ending)

    def test_multiple_runtime_references_are_not_confused_with_literals(self):
        self.accepted('fetch(`https://example.invalid/?api_key=${state.apiKey}&token=${session.token}&page=1`);')
        self.refused('fetch(`https://example.invalid/?api_key=${state.apiKey}&token=synthetic-literal-value`);')

    def test_literal_prefixes_suffixes_and_encoded_suffixes_remain_refused(self):
        for value in ('prefix${state.key}', '${state.key}suffix', '${state.key}%2Ftail',
                      '${state.key}/${other}', '${state.key};literal', '${state.key}.${other}'):
            with self.subTest(value=value):
                self.refused('fetch(`https://example.invalid/?api_key=' + value + '`);')

    def test_defaults_calls_computed_and_malformed_expressions_remain_refused(self):
        for value in ('${state.key || "fallback"}', '${state.key ?? "fallback"}',
                      '${state.key:-fallback}', '${getToken()}', '${state["key"]}',
                      '${state.keys[0]}', '${(state.key)}', '${state..key}',
                      '${state.key!}', '${state.key', '${}'):
            with self.subTest(value=value):
                self.refused('fetch(`https://example.invalid/?api_key=' + value + '`);')

    def test_literal_urls_still_fail_even_when_another_query_value_is_runtime(self):
        self.refused('fetch(`https://example.invalid/?token=synthetic-literal-value&api_key=${state.apiKey}`);')
        self.refused('fetch(`https://synthetic-user:synthetic-password@example.invalid/?api_key=${state.apiKey}`);')

    def test_other_token_patterns_still_see_the_original_runtime_reference(self):
        token = 'ghp_' + 'syntheticlowercase' * 3
        self.refused('fetch(`https://example.invalid/?api_key=${' + token + '}`);')

    def test_private_corpus_is_not_hidden_by_the_query_only_projection(self):
        for value in ('actualPrivateVariableName', '${apiKey}'):
            source = 'fetch(`https://example.invalid/?api_key=${actualPrivateVariableName}`);'
            if value.startswith('${'):
                source = 'fetch(`https://example.invalid/?api_key=${apiKey}`);'
            with self.subTest(value=value):
                self.refused(source, 'PRIVATE_VALUE_IN_PUBLIC_FILE', [value])

    def test_variable_defaults_and_sql_guards_are_not_weakened(self):
        self.refused('fetch(`https://example.invalid/?api_key=${API_KEY:-fallback}`);',
                     'PRIVATE_VARIABLE_DEFAULT_REFUSED')
        self.refused('const url = `https://example.invalid/?api_key=${apiKey}`;\nPASSWORD \'synthetic-literal\'',
                     'SQL_CREDENTIAL_REFUSED')


if __name__ == '__main__':
    unittest.main()
