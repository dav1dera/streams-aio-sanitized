"""Synthetic configuration-only recovery inputs. No production secrets or writes."""
import copy
import hashlib
import importlib.util
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

from test_public_sync import baseline, sync, ROOT, FakeGitHub, BASE, COMMIT

sys.path.insert(0, str(ROOT / "scripts"))
import public_export as export


def fixture(root, public):
    def write(name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    def secret(ref):
        return "SYNTHETIC_PRIVATE_" + hashlib.sha256(ref.encode()).hexdigest()
    plan = sync.loads(public["config/dr-render-plan.json"])
    write("docker-compose.yml", public["docker-compose.yml"])
    write(".gitignore", public[".gitignore"])
    for name, data in public.items():
        if name.startswith("data/"):
            write(name, data)
    for name, bindings in plan["env_files"].items():
        write(name, ("\n".join(key + "=" + secret(ref["path"] + "/" + ref["key"]) for key, ref in bindings.items()) + "\n").encode())
    for name, row in plan["service_folder_exports"].items():
        values = row["overrides"] or {"RUNTIME_TOKEN": {"path": row["path"], "key": "RUNTIME_TOKEN"}}
        write(name, ("\n".join(key + "=" + secret(ref["path"] + "/" + ref["key"]) for key, ref in values.items()) + "\n").encode())
    write(".env", ("\n".join(key + "=" + secret(ref["path"] + "/" + ref["key"]) for key, ref in plan["root_env"].items()) + "\nCOMPOSE_PROJECT_NAME=streams-aio\n").encode())
    for item in plan["files"]:
        if "template" in item:
            text = sync.MARKER.sub(lambda m: secret(m[1]), public[item["template"]].decode())
            if item["format"] == "json" and item["destination"].endswith((".yaml", ".yml")):
                text = "# Synthetic live YAML; public template uses JSON.\n" + sync.yaml.safe_dump(sync.loads(text), sort_keys=False)
            write(item["destination"], text.encode())
    compose = sync.yaml_load(public["docker-compose.yml"])
    destinations = {r["destination"] for r in plan["files"]}
    for row in compose["services"].values():
        for kind, name, target, mode in export.mounts(row, sync):
            if kind != "bind" or name.startswith("/"): continue
            if name in destinations or name in public:
                continue
            if Path(name).suffix and not name.startswith("data/"):
                write(name, b"#!/bin/sh\nexit 0\n")
            else:
                (root / name).mkdir(parents=True, exist_ok=True)
    return write


class PublicExportTests(unittest.TestCase):
    def repository_url_guard(self):
        root = "https://github.com/" + sync.REPOSITORY
        text = ("[badge](" + root + "/actions/workflows/public-sync-tests.yml/badge.svg)\n"
                "git clone " + root + ".git\n").encode()
        self.public["README.md"] = text
        guard = export.Guard(self.source, self.public, sync, lambda name: (self.source / name).read_bytes())
        owner = sync.REPOSITORY.split("/")[0]
        guard.remember(owner, "/stremthru/STREMTHRU_AUTH_ADMIN")
        guard.remember(owner, "/stremthru/STREMTHRU_INTEGRATION_GITHUB_USER")
        return guard, text, owner, root

    def test_published_repository_urls_do_not_taint_private_username(self):
        guard, text, owner, root = self.repository_url_guard()
        self.assertIn(owner, guard.values)
        guard.scan(text, "README.md")

    def test_repository_url_exception_never_exempts_username_or_other_file(self):
        guard, text, owner, root = self.repository_url_guard()
        for data, name in ((text + owner.encode(), "README.md"), (text, "docs/new.md"),
                           (text + (root + ".git\n").encode(), "README.md")):
            with self.subTest(name=name, length=len(data)):
                with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE"):
                    guard.scan(data, name)

    def test_repository_url_exception_requires_complete_url(self):
        guard, text, owner, root = self.repository_url_guard()
        for suffix in ("?token=extra", "/unreviewed", "@untrusted.example", "SECRET", "#extra"):
            with self.subTest(suffix=suffix):
                changed = text.replace((root + ".git").encode(), (root + ".git" + suffix).encode())
                with self.assertRaisesRegex(sync.Refused, "(?:PRIVATE_VALUE_IN_PUBLIC_FILE|CREDENTIAL_PATTERN_REFUSED)"):
                    guard.scan(changed, "README.md")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.source = self.root / "live"
        self.source.mkdir()
        self.public = baseline()
        for name in ("config/dr-manifest.yaml", ".gitignore"):
            self.public[name] = (ROOT / name).read_bytes()
        self.write = fixture(self.source, self.public)
        def images(source, compose, old):
            return {s: dict(old.get(s, {"image": "example/new@sha256:" + "a" * 64, "platform": "linux/amd64"}), configured_image=r["image"])
                    for s, r in compose["services"].items()}
        self.images = images

    def tearDown(self):
        self.tmp.cleanup()

    def test_yaml_application_sources_keep_json_templates_and_private_bindings(self):
        cases = [("data/headplane/data/config.yaml", ("server", "port"), 3001),
                 ("data/headscale/data/config/config.yaml", ("listen_addr",), "0.0.0.0:8081")]
        for name, keys, value in cases:
            with self.subTest(name=name):
                raw = (self.source / name).read_bytes()
                with self.assertRaises(json.JSONDecodeError): sync.loads(raw)
                obj = sync.yaml_load(raw)
                target = obj
                for key in keys[:-1]: target = target[key]
                target[keys[-1]] = value
                self.write(name, ("# YAML comments are local only.\n" + sync.yaml.safe_dump(obj, sort_keys=False)).encode())
        changes = self.render()
        for name, keys, value in cases:
            template = "config/dr-templates/" + name.removeprefix("data/") + ".template"
            safe = sync.loads(changes[template])
            target = safe
            for key in keys: target = target[key]
            self.assertEqual(target, value)
            self.assertEqual(set(sync.MARKER.findall(changes[template].decode())),
                             set(sync.MARKER.findall(self.public[template].decode())))
            self.assertNotIn(b"SYNTHETIC_PRIVATE_", changes[template])
            self.assertNotIn(b"comments are local", changes[template])

    def test_yaml_application_duplicate_keys_aliases_and_unsafe_tags_still_block(self):
        name = "data/headplane/data/config.yaml"
        for data in (b"server: {}\nserver: {}\n", b"server: &a [*a]\n",
                     b"server: !!python/object/apply:os.system [forbidden]\n"):
            with self.subTest(data=data):
                self.write(name, data)
                with self.assertRaises(Exception): self.render()

    def render(self, images=None):
        return export.render(self.source, self.public, sync, images or self.images)

    def compose(self, change):
        c = sync.yaml_load((self.source / "docker-compose.yml").read_bytes())
        change(c["services"])
        self.write("docker-compose.yml", sync.yaml.safe_dump(c, sort_keys=False).encode())

    def apply(self, changes):
        for name, data in changes.items():
            if data is None: self.public.pop(name, None)
            else: self.public[name] = data

    def test_existing_stack_private_bindings_and_no_raw_value_fingerprints(self):
        changes = self.render()
        self.assertTrue(changes)
        self.assertFalse(any(re.search(rb"SYNTHETIC_PRIVATE_[a-f0-9]{64}", data) for data in changes.values() if data))
        self.assertNotIn(".env", changes)
        inventory = sync.loads(changes[export.INVENTORY])
        for name in inventory["source_hashes"]:
            if name.endswith("/.env"):
                self.assertEqual(inventory["source_hashes"][name], sync.digest(changes.get(name, self.public[name])))

    def test_new_service_settings_private_agent_target_and_empty_folder(self):
        self.write("data/mediafusion/.env", b"WORKERS=4\nLOG_LEVEL=debug\nNEW_OPTION=balanced\n")
        self.write(".secrets/mediafusion.env", b"API_KEY=SYNTHETIC_NEW_PRIVATE_KEY\n")
        self.write("data/mediafusion/db/unknown", b"SYNTHETIC_NEW_PRIVATE_KEY")
        self.compose(lambda rows: rows.update(mediafusion={"image": "example/new:1", "env_file": ["data/mediafusion/.env", ".secrets/mediafusion.env"],
                                                        "volumes": ["./data/mediafusion/db:/app/db:rw"]}))
        changes = self.render()
        layout = sync.loads(changes[export.LAYOUT])
        self.assertIn("mediafusion", layout["services"])
        self.assertIn(".secrets/mediafusion.env", layout["private_env_targets_from_restored_agent"])
        self.assertTrue(any(r["path"] == "data/mediafusion/db" for r in layout["empty_directories"]))
        self.assertNotIn("data/mediafusion/db/unknown", changes)
        self.assertIn(b"NEW_OPTION=balanced", changes["data/mediafusion/.env"])
        self.assertFalse(any(b"SYNTHETIC_NEW_PRIVATE_KEY" in v for v in changes.values() if v))

    def test_service_removal_deletes_public_env_templates_and_image_lock(self):
        self.compose(lambda rows: rows.pop("jackett"))
        changes = self.render()
        self.assertIsNone(changes["data/jackett/.env"])
        self.assertIsNone(changes["config/dr-templates/jackett/data/Jackett/ServerConfig.json.template"])
        self.assertNotIn("jackett", sync.loads(changes["config/image-lock.json"]))
        self.assertNotIn("jackett", sync.loads(changes["config/dr-manifest.yaml"])["components"])
        self.assertTrue((self.source / "data/jackett/data/Jackett/ServerConfig.json").exists())

    def test_removed_service_root_secret_reference_is_not_required_for_restore(self):
        old = sync.loads(self.public["config/dr-render-plan.json"])
        self.compose(lambda rows: rows.pop("portainer"))
        changes = self.render()
        plan = sync.loads(changes["config/dr-render-plan.json"])
        compose = changes.get("docker-compose.yml", self.public["docker-compose.yml"]).decode()
        obsolete = [key for key in old["root_env"] if "PORTAINER" in key and "${" + key + "}" not in compose]
        self.assertTrue(obsolete)
        for key in obsolete:
            self.assertNotIn(key, plan["root_env"])
            self.assertNotIn(old["root_env"][key]["path"] + "/" + old["root_env"][key]["key"], plan["required_values"])

    def test_extension_version_user_config_and_code_updates_preserve_binding(self):
        name = "data/seanime/data/main/config/extensions/aq-anikoto.json"
        value = sync.loads((self.source / name).read_bytes())
        value["version"] = "2.0.0"
        value["userConfig"]["newOption"] = {"enabled": True, "description": "Additional public setting"}
        value["payload"] += "\n// updated public extension logic\n"
        self.write(name, json.dumps(value).encode())
        changes = self.render()
        result = sync.loads(changes["config/dr-templates/" + name.removeprefix("data/") + ".template"])
        self.assertEqual(result["version"], "2.0.0")
        self.assertIn("updated public extension logic", result["payload"])
        self.assertNotIn("SYNTHETIC_PRIVATE_", result["payload"])

    def test_rewritten_extension_code_uses_existing_binding_from_native_agent(self):
        ref = "/shared/SEANIME_SOLVER_URL"
        value = "SYNTHETIC_PRIVATE_" + hashlib.sha256(ref.encode()).hexdigest()
        self.write(".secrets/shared.env", ("SEANIME_SOLVER_URL=" + value + "\n").encode())
        name = "data/seanime/data/main/config/extensions/aq-anikoto.json"
        row = sync.loads((self.source / name).read_bytes())
        row["payload"] = "const NEW_LOGIC = {endpoint: '" + value + "'};\n"
        self.write(name, json.dumps(row).encode())
        result = sync.loads(self.render()["config/dr-templates/" + name.removeprefix("data/") + ".template"])
        self.assertIn("NEW_LOGIC", result["payload"])
        self.assertIn("@@INFISICAL:/shared/SEANIME_SOLVER_URL@@", result["payload"])
        self.assertNotIn(value, result["payload"])

    def test_reordered_indexer_fields_preserve_private_binding_by_id(self):
        name = "data/jackett/data/Jackett/Indexers/animetosho-xyz.json"
        value = sync.loads((self.source / name).read_bytes())
        value.reverse()
        self.write(name, json.dumps(value).encode())
        result = sync.loads(self.render()["config/dr-templates/" + name.removeprefix("data/") + ".template"])
        self.assertEqual(next(r["value"] for r in result if r["id"] == "apikey"), "@@INFISICAL:/jackett/ANIMETOSHO_XYZ_API_KEY@@")

    def honey(self):
        name = "data/honey/data/config/config.json"
        return name, sync.loads((self.source / name).read_bytes())

    def test_honey_anonymized_names_keep_all_positional_private_bindings(self):
        name, value = self.honey()
        template = "config/dr-templates/honey/data/config/config.json.template"
        old = sync.loads(self.public[template])
        for index, row in enumerate(value["services"]):
            if row["name"] == "Service " + str(index + 1):
                row["name"] = "SYNTHETIC_LOCAL_LABEL_" + str(index)
            row["desc"] = "SYNTHETIC_LOCAL_DESCRIPTION_" + str(index)
        value["ui"].update(name="SYNTHETIC_LOCAL_DASHBOARD", desc="SYNTHETIC_LOCAL_DESCRIPTION")
        value["ui"]["blur"] = not old["ui"]["blur"]
        self.write(name, json.dumps(value).encode())
        changes = self.render()
        data = changes[template]
        result = sync.loads(data)
        self.assertEqual(set(sync.MARKER.findall(data.decode())), set(sync.MARKER.findall(self.public[template].decode())))
        self.assertEqual([row["name"] for row in result["services"]], [row["name"] for row in old["services"]])
        self.assertEqual([row["desc"] for row in result["services"]], [row["desc"] for row in old["services"]])
        self.assertEqual(result["ui"]["name"], old["ui"]["name"])
        self.assertEqual(result["ui"]["blur"], value["ui"]["blur"])
        self.assertNotIn(b"SYNTHETIC_LOCAL_", data)
        self.assertNotIn(b"SYNTHETIC_PRIVATE_", data)
        self.apply(changes)
        self.assertEqual(self.render(), {})

    def test_honey_bound_icon_is_collected_before_canonical_identity_defaults(self):
        name, value = self.honey()
        value["services"][11]["name"] = "SYNTHETIC_LOCAL_LABEL"
        private_icon = "https://synthetic-private.invalid/logo.png"
        value["services"][11]["icon"] = private_icon
        self.write(name, json.dumps(value).encode())
        self.write("docs/new.md", ("Unlabeled " + private_icon).encode())
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE") as error:
            self.render()
        self.assertNotIn(private_icon, str(error.exception))

    def test_honey_unbound_identity_defaults_stay_private_and_settings_follow(self):
        name, value = self.honey()
        template = "config/dr-templates/honey/data/config/config.json.template"
        old = sync.loads(self.public[template])
        value["services"][2]["name"] = "SYNTHETIC_LOCAL_LABEL"
        value["services"][2]["href"] = "https://synthetic-private.invalid/dashboard"
        value["services"][2]["icon"] = "https://synthetic-private.invalid/icon.png"
        value["services"][2]["ping"] = True
        self.write(name, json.dumps(value).encode())
        result = sync.loads(self.render()[template])
        self.assertEqual(result["services"][2]["href"], old["services"][2]["href"])
        self.assertEqual(result["services"][2]["icon"], old["services"][2]["icon"])
        self.assertTrue(result["services"][2]["ping"])
        self.assertNotIn("synthetic-private.invalid", json.dumps(result))

    def test_honey_malformed_rows_do_not_bypass_layout_checks(self):
        name, original = self.honey()
        for replacement in (None, [], "invalid"):
            with self.subTest(replacement=replacement):
                value = copy.deepcopy(original)
                value["services"][0] = replacement
                self.write(name, json.dumps(value).encode())
                with self.assertRaisesRegex(sync.Refused, "HONEY_BOUND_LAYOUT_REVIEW_REQUIRED"): self.render()

    def test_honey_positional_layout_change_requires_review(self):
        name, original = self.honey()
        for action in ("add", "remove", "reorder"):
            with self.subTest(action=action):
                value = copy.deepcopy(original)
                if action == "add": value["services"].append({"name": "New", "desc": "", "href": "#", "icon": ""})
                elif action == "remove": value["services"].pop(0)
                else: value["services"][3], value["services"][9] = value["services"][9], value["services"][3]
                self.write(name, json.dumps(value).encode())
                with self.assertRaisesRegex(sync.Refused, "HONEY_BOUND_LAYOUT_REVIEW_REQUIRED"): self.render()

    def test_honey_anonymized_name_does_not_allow_removed_binding_or_new_credential(self):
        name, original = self.honey()
        value = copy.deepcopy(original)
        value["services"][0]["name"] = "SYNTHETIC_LOCAL_LABEL"
        value["services"][0].pop("href")
        self.write(name, json.dumps(value).encode())
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_BINDING_REMOVAL_REVIEW_REQUIRED"): self.render()
        value = copy.deepcopy(original)
        value["services"][0]["name"] = "SYNTHETIC_LOCAL_LABEL"
        value["services"][0]["api_key"] = "unrecognized_private_literal"
        self.write(name, json.dumps(value).encode())
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_FIELD_NEEDS_EXPLICIT_BINDING"): self.render()

    def test_public_config_scripts_docs_and_deleted_files_follow_source(self):
        self.write("config/public/new-container.json", b'{"workers":4,"theme":"dark"}\n')
        self.write("scripts/start-new.sh", b"#!/bin/sh\nexit 0\n")
        self.write("docs/local-recovery.md", b"Public recovery steps\n")
        changes = self.render()
        self.assertIn("config/public/new-container.json", changes)
        self.apply(changes)
        (self.source / "docs/local-recovery.md").unlink()
        self.assertIsNone(self.render()["docs/local-recovery.md"])

    def test_existing_public_examples_scripts_and_docs_pass_content_checks(self):
        names = []
        for path in sorted(ROOT.rglob("*")):
            if not path.is_file(): continue
            name = path.relative_to(ROOT).as_posix()
            if export.allowed(name) and not name.startswith("data/") and name != "docker-compose.yml":
                data = path.read_bytes()
                self.public[name] = data
                self.write(name, data)
                names.append(name)
        guard = export.Guard(self.source, self.public, sync, lambda name: sync.read_file(self.source, name))
        for name in names:
            with self.subTest(name=name):
                guard.scan(self.public[name], name)
        self.render()  # Check discovery and final publication checks with the full public tree.

    def test_infisical_example_reference_is_not_a_literal_credential(self):
        name = "config/templates/headplane.yaml.example"
        data = (ROOT / name).read_bytes()
        self.write(name, data)
        self.assertIn(name, self.render())

    def test_structured_example_references_with_comments_are_supported(self):
        files = {"config/templates/new.yaml.example": b"cookie_secret: INFISICAL:/headplane/SERVER_COOKIE_SECRET # reference only\n",
                 "config/templates/new.json.example": b'{"cookie_secret":"@@INFISICAL:/headplane/SERVER_COOKIE_SECRET@@"}\n',
                 "config/templates/new.toml.example": b'cookie_secret = "${COOKIE_SECRET}" # reference only\n'}
        for name, data in files.items(): self.write(name, data)
        changes = self.render()
        for name in files: self.assertIn(name, changes)

    def test_inline_toml_example_cannot_hide_credentials(self):
        self.write("config/templates/new.toml.example", b'server = { cookie_secret = "unrecognized_private_literal" }\n')
        with self.assertRaisesRegex(sync.Refused, "LITERAL_CREDENTIAL_REFUSED"): self.render()

    def test_example_file_with_real_or_disguised_credentials_still_blocks(self):
        cases = (b"cookie_secret: unrecognized_private_literal\n",
                 b'cookie_secret: "INFISICAL:/headplane/SERVER_COOKIE_SECRET extra_private_literal"\n',
                 b'cookie_secret: "@@INFISICAL:/headplane/SERVER_COOKIE_SECRET@@extra_private_literal"\n',
                 b'cookie_secret: "${COOKIE_SECRET}extra_private_literal"\n',
                 b"cookie_secret: INFISICAL:/headplane/ghp_" + b"a" * 36 + b"\n")
        for data in cases:
            with self.subTest(data=data[:15]):
                self.write("config/templates/new.yaml.example", data)
                with self.assertRaises(sync.Refused): self.render()

    def test_one_line_yaml_and_json_examples_cannot_hide_credentials(self):
        for name, data in (("config/templates/new.yaml.example", b"server: {cookie_secret: unrecognized_private_literal}\n"),
                           ("config/templates/new.json.example", b'{"server":{"cookie_secret":"unrecognized_private_literal"}}\n')):
            with self.subTest(name=name):
                self.write(name, data)
                with self.assertRaisesRegex(sync.Refused, "LITERAL_CREDENTIAL_REFUSED"): self.render()
                (self.source / name).unlink()

    def test_known_private_value_cannot_be_hidden_in_example_reference(self):
        value = next(iter(sync.env_values((self.source / ".secrets/comet.env").read_bytes()).values()))
        self.write("config/templates/new.yaml.example", ("api_key: INFISICAL:/shared/" + value + "\n").encode())
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE") as error:
            self.render()
        self.assertNotIn(value, str(error.exception))

    def test_python_dynamic_secret_reads_and_sql_builders_are_not_executed_or_refused(self):
        code = (b"password = identity['password']\nsecret = values['/shared/SECRET_KEY']\n"
                b"lines.append('ALTER ROLE ' + role + ' PASSWORD ' + sql_literal(secret))\n"
                b"raise RuntimeError('APPLICATION_SOURCE_MUST_NEVER_EXECUTE')\n")
        self.write("scripts/new-public.py", code)
        self.assertIn("scripts/new-public.py", self.render())

    def test_python_literal_credentials_in_assignments_dictionaries_and_calls_block(self):
        cases = (b"password = 'unrecognized_private_literal'\n",
                 b"password: str = 'unrecognized_private_literal'\n",
                 b"password = 'unrecognized_' + 'private_literal'\n",
                 b"password = b'unrecognized_private_literal'\n",
                 b"password = f'unrecognized_private_literal'\n",
                 b"settings['api_key'] = 'unrecognized_private_literal'\n",
                 b"settings.password = 'unrecognized_private_literal'\n",
                 b"config = {'server': {'api_key': 'unrecognized_private_literal'}}\n",
                 b"connect(password='unrecognized_private_literal')\n")
        for code in cases:
            with self.subTest(code=code[:15]):
                self.write("scripts/new-public.py", code)
                with self.assertRaisesRegex(sync.Refused, "LITERAL_CREDENTIAL_REFUSED") as error: self.render()
                self.assertNotIn("unrecognized_private_literal", str(error.exception))

    def test_python_sql_with_literal_password_still_blocks(self):
        for code in (b'''sql = "CREATE ROLE example PASSWORD 'private-literal';"\n''',
                     b'''sql = "CREATE ROLE example PASSWORD '" + "private-literal" + "';"\n'''):
            self.write("scripts/new-public.py", code)
            with self.assertRaisesRegex(sync.Refused, "SQL_CREDENTIAL_REFUSED"): self.render()

    def test_manual_github_edit_is_preserved_when_source_is_unchanged(self):
        self.write("docs/local-recovery.md", b"Local initial version\n")
        self.apply(self.render())
        self.public["docs/local-recovery.md"] = b"New remote public version\n"
        self.assertNotIn("docs/local-recovery.md", self.render())
        (self.source / "docs/local-recovery.md").unlink()
        with self.assertRaisesRegex(sync.Refused, "DELETE_EXTERNALLY_EDITED_FILE_REFUSED"):
            self.render()

    def test_changed_public_setting_follows_even_when_new_string(self):
        self.write("data/comet/.env", b"FASTAPI_WORKERS=8\nADDITIONAL_SETTING=public-option\nAPI_KEY=\n")
        changes = self.render()
        self.assertIn(b"ADDITIONAL_SETTING=public-option", changes["data/comet/.env"])
        self.assertNotIn(b"API_KEY=", changes["data/comet/.env"])

    def test_agent_public_constants_do_not_block_or_hide_other_service_settings(self):
        path = self.source / ".secrets/aiostreams.env"
        path.write_bytes(path.read_bytes() + b"DATABASE_TYPE=postgres\nTZ=Europe/Rome\nLOG_LEVEL=info\n")
        self.write("data/comet/.env", b"FASTAPI_WORKERS=8\nLOG_LEVEL=debug\n")
        changes = self.render()
        self.assertIn(b"LOG_LEVEL=debug", changes["data/comet/.env"])

    def healthcheck_collision(self):
        name = ".secrets/oauth2-proxy.env"
        rows = sync.env_values((self.source / name).read_bytes())
        rows["OAUTH2_PROXY_UPSTREAMS"] = "http://127.0.0.1"
        self.write(name, ("\n".join(k + "=" + v for k, v in rows.items()) + "\n").encode())

    def test_unchanged_reviewed_healthchecks_with_private_substring_do_not_block(self):
        self.healthcheck_collision()
        self.write("data/comet/.env", b"FASTAPI_WORKERS=8\n")
        changes = self.render()
        original = sync.yaml_load(self.public["docker-compose.yml"])
        compose = sync.yaml_load(changes.get("docker-compose.yml", self.public["docker-compose.yml"]))
        for service in ("tailscale", "seanime", "seanime-shared"):
            self.assertEqual(compose["services"][service]["healthcheck"], original["services"][service]["healthcheck"])
        self.assertIn(b"FASTAPI_WORKERS=8", changes["data/comet/.env"])

    def test_changed_healthcheck_with_known_private_substring_still_blocks(self):
        self.healthcheck_collision()
        self.compose(lambda rows: rows["tailscale"]["healthcheck"]["test"].__setitem__(4, "http://127.0.0.1:9002/healthz?changed=1"))
        with self.assertRaisesRegex(sync.Refused, "COMPOSE_LITERAL_PRIVATE_VALUE_USE_INTERPOLATION"):
            self.render()

    def test_new_service_cannot_inherit_reviewed_healthcheck_exception(self):
        self.healthcheck_collision()
        self.compose(lambda rows: rows.update(added={"image": "example/new:1", "healthcheck": copy.deepcopy(rows["seanime"]["healthcheck"])}))
        with self.assertRaisesRegex(sync.Refused, "COMPOSE_LITERAL_PRIVATE_VALUE_USE_INTERPOLATION"):
            self.render()

    def test_healthcheck_approval_does_not_allow_private_substring_in_other_file(self):
        self.healthcheck_collision()
        self.write("docs/unknown.md", b"http://127.0.0.1:9002/healthz\n")
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE"):
            self.render()

    def test_private_scan_healthcheck_exception_is_scoped_to_scalar_at_same_path(self):
        guard = export.Guard(self.source, self.public, sync, lambda name: sync.read_file(self.source, name))
        guard.remember("http://127.0.0.1", "/oauth2-proxy/OAUTH2_PROXY_UPSTREAMS")
        self.assertIn("http://127.0.0.1", guard.values)
        baseline_compose = self.public["docker-compose.yml"]
        guard.scan(baseline_compose, "docker-compose.yml")
        changed = sync.yaml_load(baseline_compose)
        changed["services"]["comet"]["labels"].append("http://127.0.0.1:9002/healthz")
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE"):
            guard.scan(sync.yaml.safe_dump(changed).encode(), "docker-compose.yml")
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE"):
            guard.scan(baseline_compose + b"\n# private: http://127.0.0.1\n", "docker-compose.yml")

    def test_runtime_trees_and_suspicious_names_are_never_published(self):
        for name in ("data/new/db/config.json", "data/new/state", "data/new/public/auth.sqlite", "config/public/credentials.json", "secrets/unfamiliar.txt", "docs/session.json"):
            self.write(name, b"SYNTHETIC_UNPUBLISHED_PRIVATE")
        changes = self.render()
        self.assertFalse(any(b"SYNTHETIC_UNPUBLISHED_PRIVATE" in v for v in changes.values() if v))

    def test_binary_disguised_as_public_configuration_blocks(self):
        self.write("config/public/new.json", b"SQLite format 3\0secret")
        with self.assertRaisesRegex(sync.Refused, "DATABASE_OR_ARCHIVE_REFUSED"):
            self.render()

    def test_credentials_in_new_public_text_block_without_logging_value(self):
        bad = (b"api_key=some_literal_unknown_credential\n", b"https://user:password@example.org\n", b"ghp_" + b"a" * 36,
               b"-----BEGIN PRIVATE KEY-----\n", b"-----BEGIN EC PRIVATE KEY-----\n", b"CREATE USER example WITH PASSWORD 'private-literal';\n")
        for data in bad:
            with self.subTest(data=data[:10]):
                self.write("config/public/unexpected.conf", data)
                with self.assertRaises(sync.Refused) as error: self.render()
                self.assertNotIn("some_literal_unknown_credential", str(error.exception))

    def test_one_line_json_credentials_and_indexer_rows_block(self):
        for data in (b'{"api_key":"unrecognized_private_value"}', b'[{"id":"apikey","value":"unrecognized_private_value"}]'):
            self.write("config/public/new.json", data)
            with self.assertRaisesRegex(sync.Refused, "LITERAL_CREDENTIAL_REFUSED"): self.render()

    def test_new_service_dependencies_are_respected(self):
        self.compose(lambda rows: rows.update(added={"image":"example/new:1", "depends_on":["postgres"]}))
        waves = sync.loads(self.render()["config/dr-manifest.yaml"])["restore_waves"]
        self.assertLess(next(i for i,w in enumerate(waves) if "postgres" in w), next(i for i,w in enumerate(waves) if "added" in w))

    def test_removed_service_still_referenced_by_dependency_blocks(self):
        self.compose(lambda rows: rows.pop("redis"))
        with self.assertRaisesRegex(sync.Refused, "MISSING_SERVICE_DEPENDENCY"): self.render()

    def test_second_identical_run_does_not_generate_a_commit(self):
        self.apply(self.render())
        self.assertEqual(self.render(), {})

    def test_known_private_value_in_new_noncredential_field_blocks(self):
        value = sync.env_values((self.source / ".secrets/comet.env").read_bytes())
        secret = next(iter(value.values()))
        self.write("docs/custom.md", ("Unlabeled " + secret).encode())
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VALUE_IN_PUBLIC_FILE"):
            self.render()

    def test_secret_in_compose_interpolation_default_blocks(self):
        self.compose(lambda rows: rows["comet"].update(environment={"API_KEY": "${API_KEY:-some_unrecognized_private_default}"}))
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VARIABLE_DEFAULT_REFUSED"):
            self.render()

    def test_database_url_credential_default_blocks(self):
        self.compose(lambda rows: rows["comet"].update(environment={"SETTING": "${DATABASE_URL:-postgresql://user:private-password@example.org/db}"}))
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_VARIABLE_DEFAULT_REFUSED"): self.render()

    def test_removing_private_binding_requires_explicit_contract_review(self):
        name = "data/jackett/data/Jackett/ServerConfig.json"
        value = sync.loads((self.source / name).read_bytes()); value.pop("APIKey")
        self.write(name, json.dumps(value).encode())
        with self.assertRaisesRegex(sync.Refused, "PRIVATE_BINDING_REMOVAL_REVIEW_REQUIRED"): self.render()

    def test_private_literal_in_public_env_requires_agent(self):
        self.write("data/comet/.env", b"PASSWORD=literal_not_managed_in_infisical\n")
        with self.assertRaisesRegex(sync.Refused, "PUBLIC_ENV_PRIVATE_VALUE_MUST_COME_FROM_AGENT"):
            self.render()

    def test_optional_absent_env_file_is_supported(self):
        self.compose(lambda rows: rows["comet"]["env_file"].append({"path": "data/comet/.env.example", "required": False}))
        self.render()

    def test_readonly_mount_is_recorded(self):
        changes = self.render()
        refs = sync.loads(changes["config/dr-manifest.yaml"])["references"]
        self.assertTrue(any(r.get("MODE") == "ro" for r in refs))

    def test_postgres_recovery_mount_change_requires_review(self):
        self.compose(lambda rows: rows["postgres"]["volumes"].append("./data/postgres/extra:/extra"))
        (self.source / "data/postgres/extra").mkdir()
        with self.assertRaisesRegex(sync.Refused, "RECOVERY_VOLUME_OVERRIDE_REVIEW_REQUIRED"):
            self.render()

    def test_changing_image_inventory_blocks_export(self):
        count = [0]
        def changing(source, compose, old):
            result = self.images(source, compose, old)
            count[0] += 1
            result["comet"]["image"] += str(count[0])
            return result
        with self.assertRaisesRegex(sync.Refused, "IMAGES_CHANGED_DURING_EXPORT"):
            self.render(changing)

    def test_new_service_cached_image_is_locked_without_reading_environment(self):
        compose = {"services": {"added": {"image": "example/new:1"}}}
        calls = []
        def run(args):
            calls.append(args)
            if args[1] == "ps": return b""
            return json.dumps({"RepoDigests": ["example/new@sha256:" + "f" * 64], "Os": "linux", "Architecture": "amd64"}).encode()
        with mock.patch.object(sync, "run", side_effect=run):
            result = export.dynamic_images(self.source, compose, {}, sync)
        self.assertEqual(result["added"]["image"], "example/new@sha256:" + "f" * 64)
        self.assertNotIn(".Config.Env", str(calls))
        self.assertFalse(any("pull" in args or "exec" in args for args in calls))

    def test_new_service_without_matching_digest_blocks(self):
        compose = {"services": {"added": {"image": "example/new:1"}}}
        with mock.patch.object(sync, "run", side_effect=[b"", b'{"RepoDigests":[],"Os":"linux","Architecture":"amd64"}']):
            with self.assertRaisesRegex(sync.Refused, "PUBLIC_IMAGE_DIGEST_UNAVAILABLE"):
                export.dynamic_images(self.source, compose, {}, sync)

    @unittest.skipUnless(shutil.which("git"), "Git not available")
    def test_ignore_all_new_runtime_and_allow_only_explicit_public_paths(self):
        subprocess.run(["git", "init", "-q", str(self.source)], check=True, capture_output=True)
        names = ["data/new/db/unknown", "data/new/.env", "data/new/public/config.json", "data/new/public/local.sqlite", ".secrets/new.env", ".env", "config/public/new.json", "docs/new.md"]
        for name in names: self.write(name, b"public\n")
        output = subprocess.run(["git", "-C", str(self.source), "check-ignore", "--no-index", "--stdin", "-z"], input=("\0".join(names) + "\0").encode(), capture_output=True)
        ignored = set(output.stdout.decode().split("\0"))
        self.assertEqual(ignored - {""}, {"data/new/db/unknown", "data/new/public/local.sqlite", ".secrets/new.env", ".env"})
        with (self.source / ".gitignore").open("a") as file: file.write("\n/docs/new.md\n!data/new/public/local.sqlite\n")
        selected, count = export.candidates(self.source, sync)
        self.assertNotIn("docs/new.md", selected)
        self.assertNotIn("data/new/public/local.sqlite", selected)


class DeletionGitHub(FakeGitHub):
    def api(self, endpoint, body=None):
        if body is not None and endpoint == "git/trees":
            self.writes.append((endpoint, body))
            self.files = {e["path"]: e["content"].encode() for e in body["tree"] if e.get("sha", "present") is not None}
            self.modes = {e["path"]: e["mode"] for e in body["tree"]}
            return {"sha": "c" * 40}
        result = super().api(endpoint, body)
        if body is None and endpoint.startswith("git/trees/"):
            for row in result["tree"]: row["mode"] = self.modes.get(row["path"], "100644")
        return result


class TransportDeletionTests(unittest.TestCase):
    def test_executable_mode_is_preserved_and_verified(self):
        with tempfile.TemporaryDirectory() as folder:
            gh = DeletionGitHub()
            sync.publish(gh, Path(folder), BASE, {"scripts/start-new.sh": b"#!/bin/sh\nexit 0\n"}, {"scripts/start-new.sh": "100755"})
            self.assertEqual(gh.modes["scripts/start-new.sh"], "100755")

    def test_deletion_is_verified_and_lost_response_recovers(self):
        with tempfile.TemporaryDirectory() as folder:
            state = Path(folder)
            gh = DeletionGitHub()
            gh.fail_ref_response = True
            changes = {"data/removed/.env": None, "config/image-lock.json": b"{}\n"}
            with self.assertRaises(sync.Refused): sync.publish(gh, state, BASE, changes)
            pending = sync.loads((state / "pending.json").read_bytes())
            self.assertIsNone(pending["hashes"]["data/removed/.env"])
            self.assertEqual(sync.publish(gh, state, "", {}), COMMIT)
            self.assertFalse((state / "pending.json").exists())


class IgnoreInstallerTests(unittest.TestCase):
    def test_installer_preserves_custom_rules_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / ".gitignore").write_text("/local-personal-files/\n")
            args = [sys.executable, "-B", str(ROOT / "scripts/install-public-sync-policy.py"), "--source", str(root)]
            subprocess.run(args, check=True, capture_output=True)
            first = (root / ".gitignore").read_bytes()
            subprocess.run(args, check=True, capture_output=True)
            self.assertEqual(first, (root / ".gitignore").read_bytes())
            self.assertIn(b"/local-personal-files/", first)
            self.assertEqual(first.count(b"# BEGIN STREAMS-AIO PUBLIC SYNC POLICY"), 1)


if __name__ == "__main__": unittest.main()

