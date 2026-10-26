"""Synthetic Agent templates; no credentials, VM files or template execution."""
import importlib.util
import json
from pathlib import Path
import sys
import unittest


HERE = Path(__file__).resolve().parent
SCRIPTS = HERE.parent / "scripts"
sys.path.insert(0, str(SCRIPTS))


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    sys.modules[name] = result
    spec.loader.exec_module(result)
    return result


t = module("applied_template_transform", SCRIPTS / "agent_template_transform.py")
s = module("approved_mapping_semantics", SCRIPTS / "verify-agent-dr-mappings.py")


def one(folder, key, body="{{ .Value }}"):
    return '{{ with getSecretByName "synthetic-project" "prod" "' + folder + '" "' + key + '" }}' + body + '{{ end }}'


def printed_argument(action):
    args = t._args(action.expression)
    if args[:2] != [("symbol", "printf"), ("string", "%s")] or len(args) != 3:
        raise AssertionError("expected one literal print argument")
    return args[2][1]


class LiteralParser(s._Parser):
    """Test oracle: evaluate only the reviewed subset against synthetic rows."""
    @staticmethod
    def emission(args):
        if (len(args) == 3 and args[0] == ("symbol", "printf")
                and args[1][0] == args[2][0] == "string"):
            return ("literal", args[1][1], args[2][1])
        return s._Parser.emission(args)


def render_fixture(content, folders):
    parser = LiteralParser(content)
    tree = parser.block()
    if parser.position != len(parser.tokens):
        raise AssertionError("incomplete fixture grammar")

    def render(nodes, row=None, collection=None):
        result = []
        for node in nodes:
            kind = node[0]
            if kind == "text":
                result.append(node[1])
            elif kind == "emit":
                value = node[1]
                if isinstance(value, tuple):
                    _, fmt, literal = value
                    if fmt == "%s":
                        result.append(literal)
                    elif fmt == "%q":
                        result.append(json.dumps(literal, ensure_ascii=False))
                    else:
                        raise AssertionError("unsupported literal printf")
                else:
                    result.append(value.replace(s._KEY, row["Key"]).replace(s._VALUE, row["Value"]))
            elif kind == "if":
                selected = next((body for key, body in node[1] if key == row["Key"]), node[2])
                result.append(render(selected, row, collection))
            elif kind == "with":
                values = folders.get(node[1], [])
                if values:
                    result.append(render(node[2], row, values))
            elif kind == "range":
                values = collection if node[1] is None else folders.get(node[1], [])
                for item in values:
                    result.append(render(node[2], item, values))
            else:
                raise AssertionError("unsupported fixture scope")
        return "".join(result)
    return render(tree)


def assignments(raw):
    lines = [line.strip() for line in raw.splitlines() if line.strip()]
    result = {}
    for line in lines:
        key, separator, value = line.partition("=")
        if not separator or key in result:
            raise AssertionError("missing or duplicate assignment")
        result[key] = value
    return result


class AgentTemplateTransformation(unittest.TestCase):
    def test_unmatched_credential_blocks_are_byte_identical(self):
        credential = one("/shared", "DB_PASSWORD")
        original = "database=" + credential + "\nport=" + one("/ordinary", "PORT")
        transformed = t.transform_template(original, {"/ordinary/PORT": "3000"})
        self.assertIn(credential, transformed)
        self.assertEqual(transformed.count("getSecretByName"), 1)
        self.assertEqual(t.transform_template(original, {"/other/PORT": "3000"}), original)
        self.assertEqual(t.transform_template(original, {}), original)

    def test_legacy_mixed_case_credential_is_untouched_by_unrelated_local_value(self):
        credential = one("/shared", "ApiToken") + one("/shared", "_SECOND")
        result = t.transform_template(credential + one("/ordinary", "PORT"), {"/ordinary/PORT": "3000"})
        self.assertTrue(result.startswith(credential), 'LEGACY_AGENT_CREDENTIAL_CHANGED')

    def test_single_value_keeps_body_literals(self):
        original = one("/ordinary", "HOST", "prefix:'{{ .Value }}'\n")
        result = t.transform_template(original, {"/ordinary/HOST": "example.invalid"})
        actions = t._scan(result)
        self.assertEqual(len(actions), 1)
        self.assertEqual(printed_argument(actions[0]), "prefix:'example.invalid'\n")

    def test_injection_quotes_dollars_newlines_and_unicode_are_only_print_arguments(self):
        value = '"\\$HOME\n{{ with getSecretByName "evil" "prod" "/shared" "TOKEN" }}{{ .Value }}{{ end }} 😀'
        result = t.transform_template(one("/ordinary", "LABEL"), {"/ordinary/LABEL": value})
        self.assertEqual(len(t._scan(result)), 1)
        self.assertEqual(printed_argument(t._scan(result)[0]), value)
        self.assertNotIn("\\ud83d", result)

    def test_single_block_preserves_external_and_internal_trim_semantics(self):
        original = 'left \n{{- with getSecretByName "p" "prod" "/ordinary" "HOST" -}} \n{{ .Value }} \n{{- end -}} \nright'
        result = t.transform_template(original, {"/ordinary/HOST": "plain"})
        action = t._scan(result)[0]
        self.assertTrue(action.left)
        self.assertTrue(action.right)
        self.assertEqual(printed_argument(action), "plain")
        self.assertTrue(result.startswith("left \n"))
        self.assertTrue(result.endswith(" \nright"))

    def test_unsupported_matching_expression_is_refused_without_values(self):
        for body in ("{{ unknown .Value }}", "{{ .Key }}", "{{ if .Value }}x{{ end }}"):
            with self.subTest(body_shape=body.split()[1]), self.assertRaises(t.TransformRefused) as caught:
                t.transform_template(one("/ordinary", "PORT", body), {"/ordinary/PORT": "private-canary"})
            self.assertEqual(str(caught.exception), "LOCAL_TEMPLATE_TRANSFORM_REFUSED")

    def test_invalid_unicode_and_oversized_values_refuse_with_fixed_code(self):
        for value in ("\ud800", "x" * 65537, "null\0byte"):
            with self.subTest(value_length=len(value)), self.assertRaisesRegex(
                    t.TransformRefused, "^LOCAL_TEMPLATE_TRANSFORM_REFUSED$"):
                t.transform_template(one("/ordinary", "PORT"), {"/ordinary/PORT": value})

    def test_aio_generic_range_preserves_complete_assignments_and_remaining_secrets(self):
        original = r'''{{- with secret "p" "prod" "/aiometadata" }}
{{- range . }}
{{ printf "%s=%s\n" .Key .Value }}
{{- end }}
{{- end }}
'''
        rows = [{"Key": "PORT", "Value": "3000"}, {"Key": "SECRET_KEY", "Value": "synthetic-secret"}]
        transformed = t.transform_template(original, {"/aiometadata/PORT": "3000"}, s)
        expected = assignments(render_fixture(original, {"/aiometadata": rows}))
        actual = assignments(render_fixture(transformed, {"/aiometadata": [rows[1]]}))
        # A stale duplicate still in Infisical must be suppressed by the filter.
        duplicate = assignments(render_fixture(transformed, {"/aiometadata": rows}))
        self.assertEqual(actual, expected)
        self.assertEqual(duplicate, expected)
        self.assertIn('{{ printf "%s=%s\\n" .Key .Value }}', transformed)

    def test_aio_conditional_body_keeps_selected_quoting_and_shared_overrides(self):
        original = r'''{{ with secret "p" "prod" "/aiostreams" }}{{ range . }}{{ if eq .Key "LOCAL_LABEL" }}{{ printf "%s='%s'\n" .Key .Value }}{{ else }}{{ printf "%s=%s\n" .Key .Value }}{{ end }}{{ end }}{{ end }}
{{ with secret "p" "prod" "/shared" }}{{ range . }}{{ if eq .Key "JACKETT_API_KEY" }}BUILTIN_JACKETT_API_KEY={{ .Value }}
{{ else if eq .Key "ZILEAN_URL" }}BUILTIN_ZILEAN_URL={{ .Value }}
{{ end }}{{ end }}{{ end }}'''
        rows = {"/aiostreams": [{"Key": "LOCAL_LABEL", "Value": "sample"}, {"Key": "SECRET_KEY", "Value": "synthetic-secret"}],
                "/shared": [{"Key": "JACKETT_API_KEY", "Value": "synthetic-token"}, {"Key": "ZILEAN_URL", "Value": "http://zilean.invalid"}]}
        local = {"/aiostreams/LOCAL_LABEL": "sample", "/shared/ZILEAN_URL": "http://zilean.invalid"}
        transformed = t.transform_template(original, local, s)
        remaining = {"/aiostreams": rows["/aiostreams"][1:], "/shared": rows["/shared"][:1]}
        self.assertEqual(assignments(render_fixture(transformed, remaining)), assignments(render_fixture(original, rows)))
        self.assertIn('{{ if eq .Key "JACKETT_API_KEY" }}BUILTIN_JACKETT_API_KEY={{ .Value }}', transformed)

    def test_direct_range_and_all_local_folder_still_emit_values(self):
        original = '{{ range secret "p" "prod" "/ordinary" }}{{ .Key }}={{ .Value }}\n{{ end }}'
        transformed = t.transform_template(original, {"/ordinary/PORT": "3000", "/ordinary/HOST": "node.invalid"}, s)
        self.assertEqual(assignments(render_fixture(transformed, {})), {"HOST": "node.invalid", "PORT": "3000"})

    def test_filtered_range_ignores_local_keys_it_never_emitted(self):
        original = '{{ range secret "p" "prod" "/shared" }}{{ if eq .Key "SECRET_KEY" }}SECRET={{ .Value }}\n{{ end }}{{ end }}'
        self.assertEqual(t.transform_template(original, {"/shared/ORDINARY_UNUSED": "plain"}, s), original)

    def test_listsecrets_alias_uses_injected_semantics_and_keeps_original_alias(self):
        original = '{{ with listSecrets "p" "prod" "/ordinary" }}{{ range . }}{{ .Key }}={{ .Value }}\n{{ end }}{{ end }}'
        result = t.transform_template(original, {"/ordinary/PORT": "3000"}, s)
        self.assertIn('with listSecrets "p" "prod" "/ordinary"', result)
        self.assertIn('if eq .Key "PORT"', result)

    def test_raw_collection_keeps_option_and_unselected_credentials_during_transform(self):
        options = json.dumps('{"expandSecretReferences":false}')
        original = ('{{ range listSecrets "p" "prod" "/ordinary" ' + options + ' }}'
                    '{{ if eq .Key "PORT" }}PORT=\'{{ .Value }}\'\n{{ end }}'
                    '{{ if eq .Key "ApiToken" }}ApiToken=\'{{ .Value }}\'\n{{ end }}{{ end }}')
        result = t.transform_template(original, {"/ordinary/PORT": "3000"}, s)
        self.assertIn('range listSecrets "p" "prod" "/ordinary" ' + options, result)
        self.assertIn('{{ if eq .Key "ApiToken" }}ApiToken=\'{{ .Value }}\'', result)
        values = assignments(render_fixture(result, {'/ordinary': [
            {'Key': 'PORT', 'Value': 'unused-runtime-port'},
            {'Key': 'ApiToken', 'Value': 'synthetic-$cash-${UNEXPANDED}-\u00e9'},
            {'Key': 'UNSELECTED', 'Value': 'unselected-canary'}]}))
        self.assertTrue(values == {'PORT': "'3000'", 'ApiToken': "'synthetic-$cash-${UNEXPANDED}-\u00e9'"},
                        'RAW_AGENT_TRANSFORM_CHANGED_SELECTED_ASSIGNMENTS')
        self.assertEqual(t.transform_template(original, {'/other/PORT': '3001'}, s), original)

    def test_raw_collection_rejects_unknown_options_before_transforming(self):
        invalid = ('{}', '{"expandSecretReferences":true}', '{"expandSecretReferences":0}',
                   '{"expandSecretReferences":"false"}', '{"recursive":false}',
                   '{"expandSecretReferences":false,"recursive":false}',
                   '{"expandSecretReferences":false,"expandSecretReferences":false}',
                   '[["expandSecretReferences",false]]', 'false', 'invalid-json')
        for number, raw in enumerate(invalid):
            with self.subTest(option_number=number):
                text = ('{{ range listSecrets "p" "prod" "/ordinary" ' + json.dumps(raw)
                        + ' }}{{ .Key }}={{ .Value }}\n{{ end }}')
                with self.assertRaisesRegex(t.TransformRefused, '^LOCAL_TEMPLATE_TRANSFORM_REFUSED$'):
                    t.transform_template(text, {'/ordinary/PORT': '3000'}, s)

    def test_matching_collection_requires_explicit_reviewed_parser(self):
        original = '{{ range secret "p" "prod" "/ordinary" }}{{ .Key }}={{ .Value }}\n{{ end }}'
        with self.assertRaisesRegex(t.TransformRefused, "^LOCAL_TEMPLATE_TRANSFORM_REFUSED$"):
            t.transform_template(original, {"/ordinary/PORT": "3000"})

    def test_printf_q_local_emission_keeps_runtime_quote_formatter(self):
        original = '{{ range secret "p" "prod" "/ordinary" }}{{ .Key }}={{ printf "%q" .Value }}\n{{ end }}'
        result = t.transform_template(original, {"/ordinary/LABEL": 'quote"slash\\'}, s)
        args = [t._args(action.expression) for action in t._scan(result)]
        self.assertIn([("symbol", "printf"), ("string", "%q"), ("string", 'quote"slash\\')], args)


if __name__ == "__main__":
    unittest.main()
