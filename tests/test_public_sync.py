"""Synthetic live inputs; no production credentials, Docker mutation or API writes."""
import base64
import copy
import importlib.util
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

    def test_new_private_config_field_blocks(self):
        path = self.source / "data/headplane/data/config.yaml"
        value = sync.loads(path.read_bytes())
        value["server"]["new_password"] = SECRET
        path.write_text(json.dumps(value), encoding="utf-8")
        with self.assertRaises(sync.Refused):
            self.render()

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


if __name__ == "__main__":
    unittest.main()
