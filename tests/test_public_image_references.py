"""Synthetic runtime image references; no Docker, registry or VM access."""
import importlib.util
import json
from pathlib import Path
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]


def module(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / filename)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


sync = module("image_reference_sync", "sync-public-stack.py")
export = module("image_reference_export", "public_export.py")


class PublicImageReferenceTests(unittest.TestCase):
    IMAGE_ID = "sha256:" + "a" * 64
    PREVIOUS = "sha256:" + "b" * 64
    UPDATED = "sha256:" + "c" * 64

    def read_images(self, policy, declared, observed, digests, previous=None):
        source = Path("/synthetic/stack")
        compose = {"services": {"example": {"image": declared}}}
        repository = sync.image_repository(declared)
        old = {"example": {"image": previous or repository + "@" + self.PREVIOUS,
                           "platform": "linux/amd64", "configured_image": declared}}
        values = [b"123456789abc\n",
                  " ".join(map(json.dumps, ("example", self.IMAGE_ID, observed))).encode(),
                  json.dumps({"RepoDigests": digests, "Os": "linux", "Architecture": "amd64"}).encode()]
        with mock.patch.object(sync, "run", side_effect=values) as transport:
            if policy == "strict":
                result = sync.docker_locks(source, compose, old)
            else:
                result = export.dynamic_images(source, compose, old, sync)
        commands = [call.args[0] for call in transport.call_args_list]
        self.assertEqual([command[:2] for command in commands],
                         [["docker", "ps"], ["docker", "inspect"], ["docker", "image"]])
        self.assertEqual(commands[-1][2], "inspect")
        self.assertEqual(commands[-1][-1], self.IMAGE_ID)
        self.assertNotIn("Config.Env", str(commands))
        self.assertNotIn("{{json .}}", str(commands))
        self.assertFalse(any("pull" in command or "exec" in command for command in commands))
        return result["example"]

    def test_both_policies_accept_only_equivalent_repository_aliases(self):
        pairs = [
            ("redis", "docker.io/library/redis:latest"),
            ("library/redis:latest", "index.docker.io/redis"),
            ("docker.io/redis:latest", "redis:latest"),
            ("example/app:Beta", "docker.io/example/app:Beta"),
            ("index.docker.io/example/app:latest", "example/app:latest"),
            ("ghcr.io/example/app:latest", "ghcr.io/example/app:latest"),
            ("example/app@" + self.UPDATED, "docker.io/example/app@" + self.UPDATED),
        ]
        for policy in ("strict", "seamless"):
            for declared, observed in pairs:
                with self.subTest(policy=policy, declared=declared, observed=observed):
                    repository = sync.image_repository(declared)
                    result = self.read_images(policy, declared, observed,
                                              [repository + "@" + self.UPDATED])
                    self.assertEqual(result["image"], repository + "@" + self.UPDATED)
                    self.assertEqual(result["configured_image"], declared)
                    self.assertEqual(result["platform"], "linux/amd64")

    def test_both_policies_refuse_changed_selector_or_repository(self):
        pairs = [
            ("example/app:latest", "docker.io/example/app:previous"),
            ("example/app:Beta", "docker.io/example/app:beta"),
            ("example/app:latest", "docker.io/other/app:latest"),
            ("example/app:latest", "ghcr.io/example/app:latest"),
            ("example/app:latest", "registry-1.docker.io/example/app:latest"),
            ("example/app:latest", "docker.io/example/app@" + self.UPDATED),
            ("example/app@" + self.UPDATED, "docker.io/example/app@" + self.PREVIOUS),
        ]
        for policy in ("strict", "seamless"):
            for declared, observed in pairs:
                with self.subTest(policy=policy, declared=declared, observed=observed):
                    with self.assertRaisesRegex(sync.Refused, "COMPOSE_IMAGE_NOT_APPLIED"):
                        self.read_images(policy, declared, observed,
                                         [sync.image_repository(declared) + "@" + self.UPDATED])

    def test_previous_pin_is_kept_only_when_observed_on_exact_image_id(self):
        declared = "example/app:latest"
        for policy in ("strict", "seamless"):
            with self.subTest(policy=policy, previous_observed=True):
                result = self.read_images(policy, declared, "docker.io/" + declared,
                                          ["docker.io/example/app@" + self.PREVIOUS,
                                           "example/app@" + self.UPDATED])
                self.assertEqual(result["image"], "example/app@" + self.PREVIOUS)
            with self.subTest(policy=policy, previous_observed=False):
                result = self.read_images(policy, declared, "docker.io/" + declared,
                                          ["example/app@" + self.UPDATED])
                self.assertEqual(result["image"], "example/app@" + self.UPDATED)
            with self.subTest(policy=policy, previous_wrong_repository=True):
                result = self.read_images(policy, declared, "docker.io/" + declared,
                                          ["example/app@" + self.PREVIOUS,
                                           "example/app@" + self.UPDATED],
                                          previous="ghcr.io/example/app@" + self.UPDATED)
                self.assertEqual(result["image"], "example/app@" + self.PREVIOUS)

    def test_missing_matching_metadata_never_falls_back_to_previous_pin(self):
        for policy in ("strict", "seamless"):
            for observed in ([], ["ghcr.io/example/app@" + self.PREVIOUS]):
                with self.subTest(policy=policy, observed=observed):
                    with self.assertRaisesRegex(sync.Refused, "PUBLIC_IMAGE_DIGEST_UNAVAILABLE"):
                        self.read_images(policy, "example/app:latest", "docker.io/example/app:latest", observed)

    def test_seamless_compatibility_wrapper_delegates_to_shared_helper(self):
        value = "example/app:Beta"
        with mock.patch.object(sync, "image_reference_identity", return_value=("sentinel",)) as identity:
            self.assertEqual(export.image_reference_identity(value, sync), ("sentinel",))
        identity.assert_called_once_with(value)


if __name__ == "__main__":
    unittest.main()
