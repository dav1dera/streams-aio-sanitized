"""Synthetic live inputs; no production credentials, Docker mutation or API writes."""
import base64
import copy
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("public_sync", Path(__file__).resolve().parents[1] / "scripts/sync-public-stack.py")
sync = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(sync)
ROOT = Path(__file__).resolve().parents[1]
BASE = "a" * 40
COMMIT = "b" * 40
SECRET = "SYNTHETIC_PRIVATE_MUST_NOT_BE_PUBLISHED"


def baseline():
    paths = ["docker-compose.yml", "config/dr-render-plan.json", "config/private-config-overlays.json",
             "config/compose.dr.yaml", "config/image-lock.json", "data/postgres/postgresql.conf"]
    paths += [str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "data").glob("*/.env")]
    paths += [str(p.relative_to(ROOT)).replace("\\", "/") for p in (ROOT / "config/dr-templates").rglob("*.template")]
    return {p: (ROOT / p).read_bytes() for p in paths}


def live_fixture(root, public):
    def write(name, data):
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    write("docker-compose.yml", public["docker-compose.yml"])
    for name, data in public.items():
        if re.fullmatch(r"data/[a-z0-9-]+/\.env", name):
            lines = data.decode().splitlines()
            lines = [line + SECRET if re.fullmatch(r"[A-Z][A-Z0-9_]*=", line) else line for line in lines]
            write(name, ("\n".join(lines) + "\n# " + SECRET + "\n").encode())
    plan = sync.loads(public["config/dr-render-plan.json"])
    for item in plan["files"]:
        if "template" in item:
            data = public[item["template"]].decode()
            write(item["destination"], sync.MARKER.sub(SECRET, data).encode())
    write("data/postgres/postgresql.conf", public["data/postgres/postgresql.conf"])
    # An unrelated secret/runtime/legacy tree must never be traversed or copied.
    for name in (".env", ".secrets/comet.env", ".generated/private.json", "data/npm/data/database.sqlite",
                 "Aiostreams-Stremio-Backups/private.json", "watchdog.sh", "config/private-note.txt"):
        write(name, SECRET.encode())


class FakeGitHub:
    def __init__(self):
        self.current = BASE
        self.writes = []
        self.files = {}
        self.fail_ref_response = False
        self.corrupt = False

    def head(self):
        return self.current

    def api(self, endpoint, body=None):
        if body is not None:
            self.writes.append((endpoint, body))
            if endpoint == "git/trees":
                self.files = {e["path"]: e["content"].encode() for e in body["tree"]}
                return {"sha": "c" * 40}
            if endpoint == "git/commits":
                return {"sha": COMMIT}
            if endpoint == "git/refs/heads/main":
                if self.current != BASE:
                    raise sync.Refused("SIMULATED_CONCURRENT_WRITER")
                self.current = body["sha"]
                if self.fail_ref_response:
                    raise sync.Refused("SIMULATED_LOST_RESPONSE")
                return {}
            raise AssertionError(endpoint)
        if endpoint == "git/commits/" + BASE:
            return {"tree": {"sha": "d" * 40}}
        if endpoint.startswith("git/trees/"):
            self.blobs = {("%040x" % i): data for i, data in enumerate(self.files.values(), 1)}
            return {"truncated": False, "tree": [{"path": name, "type": "blob", "sha": "%040x" % i}
                    for i, name in enumerate(self.files, 1)]}
        if endpoint.startswith("git/blobs/"):
            data = self.blobs[endpoint.rsplit("/", 1)[1]]
            if self.corrupt:
                data += b"!"
            return {"content": base64.b64encode(data).decode()}
        raise AssertionError(endpoint)


class PublicSyncTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        self.source = self.root / "live"
        self.source.mkdir()
        self.public = baseline()
        live_fixture(self.source, self.public)
        self.images = lambda source, compose, locks: copy.deepcopy(locks)

    def tearDown(self):
        self.temporary.cleanup()

    def render(self, images=None):
        return sync.render(self.source, self.public, images or self.images)

    def test_full_reviewed_corpus_masks_private_values_and_ignores_runtime(self):
        changes = self.render()
        for data in changes.values():
            self.assertNotIn(SECRET.encode(), data)
        self.assertNotIn(".env", changes)
        self.assertNotIn("watchdog.sh", changes)
        self.assertNotIn("data/npm/data/database.sqlite", changes)
        self.assertTrue((self.source / ".env").exists())

    def test_public_numbers_booleans_and_env_tuning_follow_live_changes(self):
        file = self.source / "data/seanime/data/main/config/config.toml"
        raw = file.read_text(encoding="utf-8")
        old = sync.tomllib.loads(raw)
        def mutate(obj):
            for key, value in obj.items():
                if type(value) is bool:
                    obj[key] = not value
                    return True
                if isinstance(value, dict) and mutate(value):
                    return True
            return False
        self.assertTrue(mutate(old))
        file.write_bytes(sync.toml_dump(old))
        file = self.source / "data/comet/.env"
        file.write_text(file.read_text(encoding="utf-8").replace("FASTAPI_WORKERS=1", "FASTAPI_WORKERS=2"), encoding="utf-8")
        changes = self.render()
        self.assertIn("config/dr-templates/seanime/data/main/config/config.toml.template", changes)
        self.assertIn(b"FASTAPI_WORKERS=2", changes["data/comet/.env"])
        self.assertNotIn(SECRET.encode(), changes["data/comet/.env"])

    def test_unknown_compose_field_blocks_without_its_value_in_error(self):
        path = self.source / "docker-compose.yml"
        value = sync.yaml_load(path.read_bytes())
        value["services"]["comet"][SECRET] = SECRET
        path.write_text(sync.yaml.safe_dump(value), encoding="utf-8")
        with self.assertRaises(sync.Refused) as error:
            self.render()
        self.assertNotIn(SECRET, str(error.exception))
        self.assertIn("STRUCTURE_REVIEW_REQUIRED", str(error.exception))

    def test_literal_replacing_compose_private_reference_blocks(self):
        path = self.source / "docker-compose.yml"
        raw = path.read_text()
        raw = re.sub(r"\$\{INFISICAL_[^}]+\}", SECRET, raw, count=1)
        path.write_text(raw)
        with self.assertRaises(sync.Refused) as error:
            self.render()
        self.assertNotIn(SECRET, str(error.exception))

    def test_unknown_env_key_and_private_value_in_public_field_block(self):
        path = self.source / "data/comet/.env"
        original = path.read_bytes()
        for data in (original + b"NEW_ACCESS_TOKEN=" + SECRET.encode() + b"\n",
                     original.replace(b"FASTAPI_WORKERS=1", b"FASTAPI_WORKERS=" + SECRET.encode())):
            path.write_bytes(data)
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertNotIn(SECRET, str(error.exception))

    def test_application_api_key_is_masked_and_instance_id_omitted(self):
        path = self.source / "data/jackett/data/Jackett/ServerConfig.json"
        value = sync.loads(path.read_bytes())
        value["APIKey"] = SECRET
        value["InstanceId"] = SECRET
        value["Port"] = 9222
        path.write_text(json.dumps(value), encoding="utf-8")
        changes = self.render()
        result = changes["config/dr-templates/jackett/data/Jackett/ServerConfig.json.template"]
        self.assertNotIn(SECRET.encode(), result)
        self.assertIn(b"@@INFISICAL:/shared/JACKETT_API_KEY@@", result)
        self.assertIn(b"9222", result)

    def test_changed_extension_code_blocks_and_embedded_binding_is_masked(self):
        path = self.source / "data/seanime/data/shared/config/extensions/aq-anikoto.json"
        value = sync.loads(path.read_bytes())
        value["payload"] += "// " + SECRET
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(sync.Refused) as error:
            self.render()
        self.assertNotIn(SECRET, str(error.exception))

    def test_shared_animetosho_legacy_mirror_keeps_the_reviewed_author_feed(self):
        name = "config/dr-templates/seanime/data/shared/config/extensions/animetosho-new.json.template"
        path = self.source / "data/seanime/data/shared/config/extensions/animetosho-new.json"
        canonical = "https://feed.animetosho.net/feed/json"
        legacy = "https://feed.animetosho.xyz/feed/json"
        old = sync.loads(self.public[name])
        before = self.render()
        value = sync.loads(path.read_bytes())
        value["userConfig"]["fields"][0]["default"] = legacy
        value["payload"] = value["payload"].replace(canonical, legacy)
        path.write_text(json.dumps(value), encoding="utf-8")
        self.assertEqual(self.render(), before)
        value["userConfig"]["requiresConfig"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        result = sync.loads(self.render()[name])
        self.assertIs(result["userConfig"]["requiresConfig"], True)
        self.assertEqual(result["userConfig"]["fields"][0]["default"], old["userConfig"]["fields"][0]["default"])
        self.assertEqual(result["payload"], old["payload"])
        self.assertNotIn(SECRET, json.dumps(result))

    def test_strict_mirror_roundtrip_preserves_either_reviewed_public_baseline(self):
        name = "config/dr-templates/seanime/data/shared/config/extensions/animetosho-new.json.template"
        canonical = "https://feed.animetosho.net/feed/json"
        legacy = "https://feed.animetosho.xyz/feed/json"
        original = sync.loads(self.public[name])
        for old_profile in (canonical, legacy):
            old = copy.deepcopy(original)
            old["userConfig"]["fields"][0]["default"] = old_profile
            old["payload"] = old["payload"].replace(legacy, canonical).replace(canonical, old_profile)
            for new_profile in (canonical, legacy):
                new = copy.deepcopy(old)
                new["userConfig"]["fields"][0]["default"] = new_profile
                new["payload"] = new["payload"].replace(legacy, canonical).replace(canonical, new_profile)
                new["userConfig"]["requiresConfig"] = True
                before = copy.deepcopy(new)
                result = sync.normalize_animetosho_mirror(old, new, name)
                self.assertEqual(result["payload"], old["payload"])
                self.assertEqual(result["userConfig"]["fields"][0]["default"], old_profile)
                self.assertIs(result["userConfig"]["requiresConfig"], True)
                self.assertEqual(new, before)
                new["payload"] += "// " + SECRET
                with self.assertRaises(sync.Refused) as error:
                    sync.normalize_animetosho_mirror(old, new, name)
                self.assertIn("PROVIDER_CODE_REVIEW_REQUIRED", str(error.exception))
                self.assertNotIn(SECRET, str(error.exception))

    def test_provider_mirror_rule_refuses_private_urls_new_code_and_other_metadata(self):
        path = self.source / "data/seanime/data/shared/config/extensions/animetosho-new.json"
        original = sync.loads(path.read_bytes())
        candidates = []
        for url in ("https://feed.animetosho.xyz/feed/json?apikey=" + SECRET,
                    "https://feed.animetosho.net/feed/json#" + SECRET,
                    "https://" + SECRET + "@feed.animetosho.xyz/feed/json", None):
            value = copy.deepcopy(original)
            value["userConfig"]["fields"][0]["default"] = url
            candidates.append(value)
        for key, new in (("payload", original["payload"] + "// " + SECRET),
                         ("payload", {SECRET: SECRET}), (SECRET, SECRET)):
            value = copy.deepcopy(original)
            value[key] = new
            candidates.append(value)
        value = copy.deepcopy(original)
        value["userConfig"]["fields"][0]["name"] = SECRET
        candidates.append(value)
        for value in candidates:
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertNotIn(SECRET, str(error.exception))
        # The mirror exception is confined to the documented shared template.
        path.write_text(json.dumps(original), encoding="utf-8")
        main = self.source / "data/seanime/data/main/config/extensions/animetosho-new.json"
        value = sync.loads(main.read_bytes())
        value["payload"] = value["payload"].replace("https://feed.animetosho.net/feed/json",
                                                  "https://feed.animetosho.xyz/feed/json")
        main.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(sync.Refused):
            self.render()

    def test_template_failures_are_collected_without_parser_values_or_partial_publication(self):
        path = self.source / "data/easyproxy/data/config.json"
        value = sync.loads(path.read_bytes())
        value[SECRET] = SECRET
        path.write_text(json.dumps(value), encoding="utf-8")
        path = self.source / "data/seanime/data/shared/config/extensions/animetosho-new.json"
        value = sync.loads(path.read_bytes())
        value["payload"] += "// " + SECRET
        path.write_text(json.dumps(value), encoding="utf-8")
        path = self.source / "data/jackett/data/Jackett/Indexers/knaben.json"
        path.write_text('{"' + SECRET, encoding="utf-8")
        github = mock.Mock()
        github.head.return_value = BASE
        github.baseline.return_value = self.public
        args = sync.argparse.Namespace(source=self.source, state=self.root / "state", publish=True)
        with mock.patch.object(sync, "GitHub", return_value=github), mock.patch.object(sync, "publish") as publish:
            with self.assertRaises(sync.Refused) as error:
                sync.execute(args)
            publish.assert_not_called()
        text = str(error.exception)
        self.assertIn("TEMPLATE_CHECKS_FAILED count=3", text)
        self.assertIn("easyproxy/data/config.json.template", text)
        self.assertIn("PROVIDER_CODE_REVIEW_REQUIRED", text)
        self.assertIn("TEMPLATE_INPUT_CHECK_FAILED config/dr-templates/jackett/data/Jackett/Indexers/knaben.json.template", text)
        self.assertNotIn(SECRET, text)
        self.assertFalse((args.state / "pending.json").exists())

    def test_all_jackett_indexer_cookies_and_null_errors_stay_out_of_public_exports(self):
        before = self.render()
        names = [n for n in self.public if n.startswith("config/dr-templates/jackett/data/Jackett/Indexers/")]
        self.assertEqual(len(names), 5)
        for name in names:
            path = self.source / ("data/" + name.removeprefix("config/dr-templates/").removesuffix(".template"))
            value = sync.loads(path.read_bytes())
            for row in value:
                if row["id"] == "cookieheader":
                    row["value"] = SECRET
                if row["id"] == "lasterror":
                    row["value"] = None
            path.write_text(json.dumps(value), encoding="utf-8")
        # Runtime changes alone do not create public revisions.
        self.assertEqual(self.render(), before)
        name = "config/dr-templates/jackett/data/Jackett/Indexers/animetosho-xyz.json.template"
        path = self.source / "data/jackett/data/Jackett/Indexers/animetosho-xyz.json"
        value = sync.loads(path.read_bytes())
        next(row for row in value if row["type"] == "inputbool")["value"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        changes = self.render()
        result = sync.loads(changes[name])
        self.assertTrue(any(row["value"] is True for row in result if row["type"] == "inputbool"))
        self.assertEqual(next(row["value"] for row in result if row["id"] == "cookieheader"), "")
        self.assertEqual(next(row["value"] for row in result if row["id"] == "lasterror"), "")
        self.assertEqual(next(row["value"] for row in result if row["id"] == "apikey"),
                         "@@INFISICAL:/jackett/ANIMETOSHO_XYZ_API_KEY@@")
        self.assertNotIn(SECRET.encode(), changes[name])

    def test_jackett_runtime_normalization_refuses_unknown_shapes_and_wrong_types(self):
        path = self.source / "data/jackett/data/Jackett/Indexers/animetosho-xyz.json"
        original = sync.loads(path.read_bytes())
        candidates = []
        for key, new in (("id", SECRET), ("type", SECRET), (SECRET, SECRET),
                         ("value", {SECRET: SECRET}), ("value", [SECRET]), ("value", True)):
            value = copy.deepcopy(original)
            value[2][key] = new
            candidates.append(value)
        value = copy.deepcopy(original)
        value[1], value[2] = value[2], value[1]
        candidates += [value, original[:-1], original + [{SECRET: SECRET}]]
        value = copy.deepcopy(original)
        del value[2]["value"]
        candidates.append(value)
        for value in candidates:
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertNotIn(SECRET, str(error.exception))

    def test_jackett_nullable_runtime_policy_does_not_relax_other_fields(self):
        path = self.source / "data/jackett/data/Jackett/Indexers/animetosho-xyz.json"
        original = sync.loads(path.read_bytes())
        for index, new in ((0, SECRET), (0, None), (3, None), (5, 1)):
            value = copy.deepcopy(original)
            value[index]["value"] = new
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertNotIn(SECRET, str(error.exception))

    def test_new_private_config_field_blocks(self):
        path = self.source / "data/headplane/data/config.yaml"
        value = sync.loads(path.read_bytes())
        value["server"]["new_password"] = SECRET
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(sync.Refused):
            self.render()

    def test_easyproxy_quality_switches_follow_live_and_preserve_private_boundary(self):
        name = "config/dr-templates/easyproxy/data/config.json.template"
        path = self.source / "data/easyproxy/data/config.json"
        value = sync.loads(path.read_bytes())
        value["max_res_mpd"] = True
        value["max_res_hls"] = True
        path.write_text(json.dumps(value), encoding="utf-8")
        changes = self.render()
        result = sync.loads(changes[name])
        self.assertIs(result["max_res_mpd"], True)
        self.assertIs(result["max_res_hls"], True)
        self.assertEqual(result["max_res_extractors"], [])
        self.assertNotIn(SECRET.encode(), changes[name])
        # Reviewing these three fields does not admit future VPN credentials,
        # even when their source value is currently empty.
        for extra in ("nordvpn_token", SECRET):
            value[extra] = ""
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertIn("STRUCTURE_REVIEW_REQUIRED", str(error.exception))
            self.assertNotIn(SECRET, str(error.exception))
            del value[extra]

    def test_easyproxy_quality_strings_lists_and_wrong_types_require_review(self):
        path = self.source / "data/easyproxy/data/config.json"
        original = sync.loads(path.read_bytes())
        for key, new in (("max_res_extractors", [SECRET]),
                         ("max_res_mpd", SECRET), ("max_res_hls", 1)):
            value = copy.deepcopy(original)
            value[key] = new
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertNotIn(SECRET, str(error.exception))

    def test_honey_branding_stays_generic_while_public_ui_switches_follow_live(self):
        name = "config/dr-templates/honey/data/config/config.json.template"
        old = sync.loads(self.public[name])
        path = self.source / "data/honey/data/config/config.json"
        value = sync.loads(path.read_bytes())
        for key in ("name", "desc", "icon"):
            value["ui"][key] = SECRET
        for row in value["services"]:
            row["name"] = SECRET
            row["desc"] = SECRET
        value["ui"]["open_new_tab"] = not old["ui"]["open_new_tab"]
        path.write_text(json.dumps(value), encoding="utf-8")
        changes = self.render()
        result = sync.loads(changes[name])
        for key in ("name", "desc", "icon"):
            self.assertEqual(result["ui"][key], old["ui"][key])
        self.assertNotEqual(result["ui"]["open_new_tab"], old["ui"]["open_new_tab"])
        self.assertNotIn(SECRET.encode(), changes[name])
        for name in changes:
            self.assertNotIn(SECRET.encode(), changes[name])

    def test_honey_generic_fields_require_strings_and_other_fields_still_block(self):
        path = self.source / "data/honey/data/config/config.json"
        original = sync.loads(path.read_bytes())
        mutations = [("desc", {SECRET: SECRET}), ("icon", [SECRET]),
                     (SECRET, SECRET), ("wallpaper", SECRET)]
        for key, new in mutations:
            value = copy.deepcopy(original)
            value["ui"][key] = new
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertNotIn(SECRET, str(error.exception))
        for key in ("name", "desc"):
            value = copy.deepcopy(original)
            value["services"][0][key] = {SECRET: SECRET}
            path.write_text(json.dumps(value), encoding="utf-8")
            with self.assertRaises(sync.Refused) as error:
                self.render()
            self.assertIn("GENERIC_FIELD_TYPE_CHANGED", str(error.exception))
            self.assertNotIn(SECRET, str(error.exception))

    def test_image_lock_and_recovery_override_move_together(self):
        def images(source, compose, locks):
            result = copy.deepcopy(locks)
            result["comet"]["image"] = "g0ldyy/comet@sha256:" + "f" * 64
            return result
        changes = self.render(images)
        lock = sync.loads(changes["config/image-lock.json"])
        override = sync.loads(changes["config/compose.dr.yaml"])
        self.assertEqual(lock["comet"]["image"], override["services"]["comet"]["image"])
        self.assertEqual(lock["comet"]["platform"], override["services"]["comet"]["platform"])

    def test_source_or_image_changes_mid_export_block(self):
        calls = [0]
        def images(source, compose, locks):
            calls[0] += 1
            if calls[0] == 1:
                (source / "data/comet/.env").write_text("MUTATED=" + SECRET)
            return locks
        with self.assertRaises(sync.Refused) as error:
            self.render(images)
        self.assertIn("SOURCE_CHANGED_DURING_EXPORT", str(error.exception))

    def test_running_image_metadata_is_minimal_and_verified(self):
        compose = sync.yaml_load(self.public["docker-compose.yml"])
        locks = sync.loads(self.public["config/image-lock.json"])
        image_id = "sha256:" + "e" * 64
        configured = compose["services"]["comet"]["image"]
        metadata = {"RepoDigests": ["g0ldyy/comet@sha256:" + "f" * 64], "Os": "linux", "Architecture": "amd64"}
        values = [b"123abcdefabc\n", (json.dumps("comet") + " " + json.dumps(image_id) + " " + json.dumps(configured)).encode(),
                  json.dumps(metadata).encode()]
        with mock.patch.object(sync, "run", side_effect=values) as run:
            result = sync.docker_locks(self.source, compose, locks)
        self.assertEqual(result["comet"]["image"], metadata["RepoDigests"][0])
        self.assertEqual(result["comet"]["configured_image"], configured)
        self.assertEqual(result["comet"]["platform"], "linux/amd64")
        args = str(run.call_args_list)
        self.assertNotIn("Config.Env", args)
        self.assertNotIn("{{json .}}", args)

    def test_postgres_multiple_manifests_keep_verified_existing_pin(self):
        compose = sync.yaml_load(self.public["docker-compose.yml"])
        locks = sync.loads(self.public["config/image-lock.json"])
        image_id = "sha256:c293117fcecda7344b5480222e813b9f673d7abd69b1dd95eff239b768b04f59"
        # Public metadata from the reported PostgreSQL image; no runtime config.
        digests = [
            "postgres@sha256:650d7a867dc5336eff67802a1417fcc801821d73d824c608e80afbde792fe6e1",
            "postgres@sha256:6c538e7206ea40ff740ef27883529390a690b6ead6ba96b44c67a9f7c638e8fd",
            "postgres@sha256:77f585114c32fbca283dc835b0596f4e52b51b4c6662d7810b2f4084f60a1873",
        ]
        configured = compose["services"]["postgres"]["image"]
        for order in (digests, list(reversed(digests))):
            values = [b"123abcdefabc\n",
                      " ".join(map(json.dumps, ("postgres", image_id, configured))).encode(),
                      json.dumps({"RepoDigests": order, "Os": "linux", "Architecture": "amd64"}).encode()]
            with mock.patch.object(sync, "run", side_effect=values) as transport:
                result = sync.docker_locks(self.source, compose, locks)
            self.assertEqual(result["postgres"]["image"], locks["postgres"]["image"])
            self.assertEqual(result["postgres"]["platform"], "linux/amd64")
            self.assertEqual(transport.call_args_list[-1].args[0][-1], image_id)
            commands = [call.args[0] for call in transport.call_args_list]
            self.assertEqual([c[:2] for c in commands], [["docker", "ps"], ["docker", "inspect"], ["docker", "image"]])
            self.assertEqual(commands[-1][2], "inspect")
            self.assertNotIn("Config.Env", str(commands))
            self.assertNotIn("{{json .}}", str(commands))

    def test_new_running_image_selects_stable_digest_and_updates_recovery_pair(self):
        compose = sync.yaml_load(self.public["docker-compose.yml"])
        image_id = "sha256:" + "e" * 64
        configured = compose["services"]["postgres"]["image"]
        digests = ["mirror.invalid/library/postgres@sha256:" + "0" * 64,
                   "docker.io/other/postgres@sha256:" + "0" * 64,
                   "postgres@sha256:" + "f" * 64,
                   "docker.io/library/postgres@sha256:" + "b" * 64]
        values = []
        # A changed listing order between the two reads must not cause churn.
        for order in (digests, list(reversed(digests))):
            values += [b"123abcdefabc\n",
                       " ".join(map(json.dumps, ("postgres", image_id, configured))).encode(),
                       json.dumps({"RepoDigests": order, "Os": "linux", "Architecture": "amd64"}).encode()]
        with mock.patch.object(sync, "run", side_effect=values):
            changes = self.render(sync.docker_locks)
        lock = sync.loads(changes["config/image-lock.json"])["postgres"]
        override = sync.loads(changes["config/compose.dr.yaml"])["services"]["postgres"]
        self.assertEqual(lock["image"], "postgres@sha256:" + "b" * 64)
        self.assertEqual(override["image"], lock["image"])
        self.assertEqual(override["platform"], lock["platform"])
        self.assertEqual(lock["configured_image"], configured)
        self.assertNotEqual(lock["image"], image_id)
        self.assertNotIn(SECRET.encode(), b"".join(changes.values()))

    def test_digest_matching_accepts_only_exact_docker_hub_repository_aliases(self):
        aliases = ("postgres", "library/postgres", "docker.io/postgres",
                   "docker.io/library/postgres", "index.docker.io/library/postgres")
        previous = "postgres@sha256:" + "f" * 64
        for configured_repo in aliases:
            for observed_repo in aliases:
                candidates = ["untrusted.invalid/library/postgres@sha256:" + "0" * 64,
                              "docker.io/other/postgres@sha256:" + "0" * 64,
                              "postgres@sha256:" + "a" * 64,
                              observed_repo + "@sha256:" + "f" * 64]
                result = sync.select_image_digest(configured_repo + ":18-alpine", candidates, previous, "reviewed")
                self.assertEqual(result, configured_repo + "@sha256:" + "f" * 64)
        for repo, equivalent, unrelated in (
                ("g0ldyy/comet", "docker.io/g0ldyy/comet", "docker.io/library/comet"),
                ("ghcr.io/tale/headplane", "ghcr.io/tale/headplane", "docker.io/tale/headplane")):
            result = sync.select_image_digest(repo + ":latest", [unrelated + "@sha256:" + "0" * 64,
                                               equivalent + "@sha256:" + "b" * 64], repo + "@sha256:" + "f" * 64, "reviewed")
            self.assertEqual(result, repo + "@sha256:" + "b" * 64)

    def test_missing_or_invalid_digests_block_without_old_pin_or_image_id_fallback(self):
        previous = "postgres@sha256:" + "f" * 64
        cases = [(None, "PUBLIC_IMAGE_DIGEST_UNAVAILABLE"),
                 ([], "PUBLIC_IMAGE_DIGEST_UNAVAILABLE"),
                 (["mirror.invalid/library/postgres@sha256:" + "b" * 64], "PUBLIC_IMAGE_DIGEST_UNAVAILABLE"),
                 (["docker.io/other/postgres@sha256:" + "b" * 64], "PUBLIC_IMAGE_DIGEST_UNAVAILABLE"),
                 ({SECRET: SECRET}, "INVALID_IMAGE_DIGEST_METADATA"),
                 (SECRET, "INVALID_IMAGE_DIGEST_METADATA"),
                 ([{SECRET: SECRET}], "INVALID_IMAGE_DIGEST_METADATA"),
                 ([previous, "postgres@sha256:" + SECRET], "INVALID_REPOSITORY_DIGEST")]
        for observed, code in cases:
            with self.assertRaises(sync.Refused) as error:
                sync.select_image_digest("postgres:18-alpine", observed, previous, "reviewed")
            self.assertIn(code, str(error.exception))
            self.assertNotIn(SECRET, str(error.exception))

    def test_explicit_compose_pin_must_be_observed_and_takes_priority(self):
        previous = "postgres@sha256:" + "f" * 64
        configured = "postgres@sha256:" + "b" * 64
        candidates = [previous, "docker.io/library/postgres@sha256:" + "b" * 64]
        self.assertEqual(sync.select_image_digest(configured, candidates, previous, "reviewed"), configured)
        with self.assertRaises(sync.Refused) as error:
            sync.select_image_digest(configured, [previous], previous, "reviewed")
        self.assertIn("CONFIGURED_IMAGE_DIGEST_NOT_VERIFIED", str(error.exception))

    def test_unapplied_compose_image_blocks_publication(self):
        compose = sync.yaml_load(self.public["docker-compose.yml"])
        locks = sync.loads(self.public["config/image-lock.json"])
        values = [b"123abcdefabc\n", json.dumps("comet").encode() + b' "sha256:' + b'e' * 64 + b'" "unapplied:latest"']
        with mock.patch.object(sync, "run", side_effect=values):
            with self.assertRaises(sync.Refused):
                sync.docker_locks(self.source, compose, locks)

    def test_image_switch_mid_export_blocks(self):
        calls = [0]
        def images(source, compose, locks):
            calls[0] += 1
            result = copy.deepcopy(locks)
            result["comet"]["image"] = "g0ldyy/comet@sha256:" + str(calls[0]) * 64
            return result
        with self.assertRaises(sync.Refused) as error:
            self.render(images)
        self.assertIn("IMAGES_CHANGED_DURING_EXPORT", str(error.exception))

    def test_duplicate_keys_recursive_yaml_and_unsafe_tags_block(self):
        for data in (b"a: 1\na: 2\n", b"a: &a [*a]\n", b"a: !!python/object/apply:os.system [echo forbidden]\n"):
            with self.assertRaises(Exception):
                sync.yaml_load(data)
        with self.assertRaises(sync.Refused):
            sync.loads('{"a":1,"a":2}')

    @unittest.skipIf(os.name == "nt", "Linux CI verifies symlinks")
    def test_source_symlink_and_state_symlink_block(self):
        file = self.source / "data/postgres/postgresql.conf"
        file.unlink()
        file.symlink_to(self.source / ".env")
        with self.assertRaises(sync.Refused):
            self.render()
        state = self.root / "alias"
        state.symlink_to(self.source, target_is_directory=True)
        with self.assertRaises(sync.Refused):
            sync.safe_path(state)

    def test_public_commit_uses_only_generated_files_and_never_force(self):
        github = FakeGitHub()
        state = self.root / "state"
        state.mkdir()
        sync.publish(github, state, BASE, {"data/comet/.env": b"FASTAPI_WORKERS=2\n"})
        self.assertEqual(github.writes[-1], ("git/refs/heads/main", {"sha": COMMIT, "force": False}))
        self.assertEqual(set(github.files), {"data/comet/.env"})
        self.assertTrue(sync.loads((state / "current.json").read_bytes())["verified"])
        self.assertFalse((state / "pending.json").exists())

    def test_lost_ref_response_recovers_without_replaying_writes(self):
        github = FakeGitHub()
        github.fail_ref_response = True
        state = self.root / "state"
        state.mkdir()
        with self.assertRaises(sync.Refused):
            sync.publish(github, state, BASE, {"data/comet/.env": b"FASTAPI_WORKERS=2\n"})
        self.assertFalse((state / "current.json").exists())
        count = len(github.writes)
        sync.publish(github, state, BASE, {})
        self.assertEqual(count, len(github.writes))
        self.assertFalse((state / "pending.json").exists())

    def test_corrupt_readback_and_concurrent_main_block_receipt(self):
        github = FakeGitHub()
        github.corrupt = True
        state = self.root / "state"
        state.mkdir()
        with self.assertRaises(sync.Refused):
            sync.publish(github, state, BASE, {"data/comet/.env": b"FASTAPI_WORKERS=2\n"})
        self.assertTrue((state / "pending.json").exists())
        self.assertFalse((state / "current.json").exists())
        github = FakeGitHub()
        github.current = COMMIT
        with self.assertRaises(sync.Refused):
            sync.publish(github, self.root, BASE, {"data/comet/.env": b"x"})
        self.assertFalse(github.writes)

    def test_unchanged_run_only_writes_private_receipt(self):
        github = FakeGitHub()
        sync.publish(github, self.root, BASE, {})
        self.assertFalse(github.writes)

    def test_transport_does_not_surface_stderr_with_secret(self):
        with mock.patch.object(sync.subprocess, "run", return_value=subprocess.CompletedProcess([], 1, b"", SECRET.encode())):
            with self.assertRaises(sync.Refused) as error:
                sync.GitHub().preflight()
        self.assertNotIn(SECRET, str(error.exception))


class SafeDiagnosticsTests(unittest.TestCase):
    def invoke(self, action):
        output = io.StringIO()
        with mock.patch.object(sync, "main", side_effect=action), contextlib.redirect_stderr(output):
            status = sync.cli()
        return status, output.getvalue()

    def test_parser_error_reports_class_and_trusted_line_without_input_or_source(self):
        status, output = self.invoke(lambda: sync.loads('{"' + SECRET + '": invalid}'))
        self.assertEqual(status, 1)
        self.assertIn("type=JSONDecodeError", output)
        self.assertRegex(output, r"code=sync-public-stack\.py:\d+")
        self.assertNotIn(SECRET, output)
        self.assertNotIn(str(ROOT), output)
        self.assertNotIn("json.loads", output)
        self.assertNotIn("Traceback", output)

    def test_os_error_does_not_log_private_filename_message_or_chained_error(self):
        def denied():
            try:
                raise ValueError(SECRET)
            except ValueError as error:
                raise PermissionError(13, SECRET, "/private/" + SECRET + ".env") from error
        status, output = self.invoke(denied)
        self.assertEqual(status, 1)
        self.assertIn("type=PermissionError", output)
        self.assertNotIn(SECRET, output)
        self.assertNotIn("/private", output)
        self.assertNotIn("denied", output)
        self.assertNotIn("test_public_sync.py", output)

    def test_known_refusal_keeps_safe_code(self):
        status, output = self.invoke(lambda: sync.require(False, "SOURCE_CHANGED_DURING_EXPORT"))
        self.assertEqual(status, 1)
        self.assertEqual(output, "PUBLIC_SYNC FAIL: SOURCE_CHANGED_DURING_EXPORT\n")

    def test_success_returns_zero(self):
        self.assertEqual(self.invoke(lambda: None), (0, ""))


if __name__ == "__main__":
    unittest.main()
