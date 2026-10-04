"""Documentation installation must never touch deployment inputs or leak old text."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("local_guides", ROOT / "scripts/install-local-guides.py")
guides = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guides)


class LocalGuidesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        base = Path(self.temp.name)
        self.tooling, self.source, self.state = base / "tooling", base / "stack", base / "state"
        self.tooling.mkdir()
        self.source.mkdir()
        for name in guides.FILES:
            target = self.tooling / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(("final guide " + name + "\n").encode())
        self.protected = {"docker-compose.yml": b"LOCAL_COMPOSE", ".env": b"LOCAL_ROOT_ENV",
                          ".secrets/service.env": b"PRIVATE_SENTINEL", "data/app/data/no-extension": b"DATABASE",
                          "scripts/dr.py": b"LOCAL_SCRIPT", ".gitignore": b"LOCAL_POLICY"}
        for name, data in self.protected.items():
            target = self.source / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
        (self.source / "README.md").write_bytes(b"old local note PRIVATE_SENTINEL")

    def invoke(self, apply=False):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            backup = guides.install(self.tooling, self.source, self.state, apply)
        self.assertNotIn("PRIVATE_SENTINEL", output.getvalue())
        return backup

    def test_default_is_read_only(self):
        self.assertIsNone(self.invoke())
        self.assertFalse(self.state.exists())
        self.assertEqual((self.source / "README.md").read_bytes(), b"old local note PRIVATE_SENTINEL")

    def test_apply_preserves_old_bytes_and_only_updates_guides(self):
        backup = self.invoke(True)
        self.assertEqual((backup / "README.md").read_bytes(), b"old local note PRIVATE_SENTINEL")
        self.assertIsNotNone(json.loads((backup / "manifest.json").read_text())["README.md"]["previous_sha256"])
        for name in guides.FILES:
            self.assertEqual((self.source / name).read_bytes(), (self.tooling / name).read_bytes())
        for name, data in self.protected.items():
            self.assertEqual((self.source / name).read_bytes(), data)
        if os.name == "posix":
            self.assertEqual(stat.S_IMODE(backup.stat().st_mode), 0o700)
            self.assertEqual(stat.S_IMODE((backup / "README.md").stat().st_mode), 0o600)
        count = len(list(self.state.iterdir()))
        self.assertIsNone(self.invoke(True))
        self.assertEqual(len(list(self.state.iterdir())), count)

    def test_missing_public_guide_refuses_before_any_write(self):
        (self.tooling / guides.FILES[-1]).unlink()
        with self.assertRaisesRegex(guides.Refused, "GUIDE_FILE_INVALID"):
            self.invoke(True)
        self.assertFalse(self.state.exists())
        self.assertEqual((self.source / "README.md").read_bytes(), b"old local note PRIVATE_SENTINEL")

    def test_backup_inside_live_tree_refused(self):
        self.state = self.source / "docs/backups"
        with self.assertRaisesRegex(guides.Refused, "BACKUPS_MUST_BE_OUTSIDE_CHECKOUTS"):
            self.invoke(True)
        self.assertFalse(self.state.exists())

    def test_non_deployment_target_refused(self):
        (self.source / "docker-compose.yml").unlink()
        with self.assertRaisesRegex(guides.Refused, "DEPLOYMENT_DIRECTORY_REQUIRED"):
            self.invoke(True)
        self.assertFalse(self.state.exists())

    def test_existing_lock_refuses(self):
        (self.state / "install.lock").mkdir(parents=True)
        with self.assertRaisesRegex(guides.Refused, "GUIDE_INSTALL_LOCKED"):
            self.invoke(True)
        self.assertEqual((self.source / "README.md").read_bytes(), b"old local note PRIVATE_SENTINEL")

    @unittest.skipUnless(os.name == "posix", "unprivileged symlinks unavailable on Windows")
    def test_symlinked_guide_directory_refuses(self):
        outside = self.source.parent / "outside"
        outside.mkdir()
        (self.source / "docs").symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(guides.Refused, "SYMLINK_REFUSED"):
            self.invoke(True)
        self.assertEqual(list(outside.iterdir()), [])
        self.assertFalse(self.state.exists())

    def test_installer_covers_each_readme_guide_and_images(self):
        for name in guides.FILES:
            self.assertTrue((ROOT / name).is_file(), name)

    def test_installer_remains_repository_owned(self):
        spec = importlib.util.spec_from_file_location("guide_export_policy", ROOT / "scripts/public_export.py")
        policy = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(policy)
        self.assertFalse(policy.allowed("scripts/install-local-guides.py"))
        self.assertTrue(policy.allowed("docs/DISASTER-RECOVERY.md"))


if __name__ == "__main__":
    unittest.main()
