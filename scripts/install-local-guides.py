#!/usr/bin/env python3
"""Copy only the reviewed public guides into the live tree, preserving old bytes.

Default: comparison only. --apply never updates Compose, secrets, runtime,
other scripts, Git refs, or services. Run as the deployment owner, without sudo.
"""
import argparse
from contextlib import contextmanager
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile

FILES = (
    "README.md", "AUDIT-SCOPE.md", "docs/BOOTSTRAP.md",
    "docs/DISASTER-RECOVERY.md", "docs/NPM-RECOVERY.md", "docs/AIO-EXPORT-IMPORT.md",
    "docs/ESEMPI-PRATICI.md", "docs/SEAMLESS-PUBLIC-SYNC.md", "docs/PUBLIC-STACK-SYNC.md",
    "docs/images/streams-aio-banner.svg", "docs/images/local-to-github.svg",
)
MAX_BYTES = 2 * 1024 * 1024


class Refused(Exception):
    pass


def require(condition, code):
    if not condition:
        raise Refused(code)


def safe(path):
    path = Path(os.path.abspath(path))
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_REFUSED")
    return path


def content(path, optional=False):
    safe(path)
    if optional and not path.exists():
        return None
    require(path.is_file() and path.stat().st_size <= MAX_BYTES, "GUIDE_FILE_INVALID")
    return path.read_bytes()


def sha(data):
    return hashlib.sha256(data).hexdigest() if data is not None else None


@contextmanager
def lock(state):
    marker = state / "install.lock"
    try:
        marker.mkdir(mode=0o700)
    except FileExistsError:
        raise Refused("GUIDE_INSTALL_LOCKED") from None
    try:
        yield
    finally:
        marker.rmdir()


def atomic_write(path, data, mode):
    safe(path)
    fd, temporary = tempfile.mkstemp(prefix=".guide-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def install(tooling, source, state, apply=False):
    tooling, source, state = (safe(p) for p in (tooling, source, state))
    require(tooling.is_dir() and source.is_dir() and tooling != source, "SEPARATE_EXISTING_SOURCE_REQUIRED")
    require((source / "docker-compose.yml").is_file(), "DEPLOYMENT_DIRECTORY_REQUIRED")
    require(not (state == source or source in state.parents or state == tooling or tooling in state.parents),
            "BACKUPS_MUST_BE_OUTSIDE_CHECKOUTS")
    before, desired, modes = {}, {}, {}
    for name in FILES:
        target = source / name
        safe(target)
        require(target.parent == source or all(not p.exists() or p.is_dir()
                for p in target.parents if source in p.parents), "GUIDE_PARENT_INVALID")
        desired[name] = content(tooling / name)
        before[name] = content(target, optional=True)
        modes[name] = stat.S_IMODE(target.stat().st_mode) & 0o666 if target.exists() else 0o644
    changed = [name for name in FILES if before[name] != desired[name]]
    if not apply or not changed:
        print("LOCAL_GUIDES_" + ("APPLY" if apply else "CHECK") + " PASS changed_files=" + str(len(changed)))
        for name in changed:
            print("GUIDE_FILE", name)
        return None
    state.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(state, 0o700)
    with lock(state):
        require(all(content(source / name, optional=True) == before[name] for name in FILES), "LOCAL_GUIDE_CHANGED")
        require(all(content(tooling / name) == desired[name] for name in FILES), "TOOLING_GUIDE_CHANGED")
        stamp = dt.datetime.now(dt.timezone.utc).strftime("%Y%m%dT%H%M%SZ-")
        backup = Path(tempfile.mkdtemp(prefix=stamp, dir=state))
        os.chmod(backup, 0o700)
        records = {}
        for name in changed:
            records[name] = {"previous_sha256": sha(before[name]), "installed_sha256": sha(desired[name])}
            if before[name] is not None:
                target = backup / name
                target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                atomic_write(target, before[name], 0o600)
        atomic_write(backup / "manifest.json", (json.dumps(records, indent=2) + "\n").encode(), 0o600)
        for name in changed:
            target = source / name
            require(content(target, optional=True) == before[name], "LOCAL_GUIDE_CHANGED")
            safe(target)
            target.parent.mkdir(parents=True, exist_ok=True, mode=0o755)
            atomic_write(target, desired[name], modes[name])
        require(all(content(source / name) == desired[name] for name in FILES), "GUIDE_READBACK_FAILED")
    print("LOCAL_GUIDES_APPLY PASS changed_files=" + str(len(changed)))
    print("LOCAL_GUIDES_BACKUP", backup)
    return backup


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    install(Path(__file__).resolve().parents[1], Path(args.source),
            Path.home() / ".local/state/streams-aio-guide-updates", args.apply)


if __name__ == "__main__":
    try:
        main()
    except Exception as error:
        print("LOCAL_GUIDES FAIL:", str(error) if isinstance(error, Refused) else type(error).__name__)
        sys.exit(1)
