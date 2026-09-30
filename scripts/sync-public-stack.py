#!/usr/bin/env python3
"""Read a live stack, export reviewed public fields, publish without its Git history.

No Docker start/exec, Infisical mutation, resolved Compose output or raw diff.
"""
import argparse
import base64
import copy
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import tomllib

import yaml

REPOSITORY = "dav1dera/streams-aio-sanitized"
PREFIX = "repos/" + REPOSITORY
MAX_FILE = 4 * 1024 * 1024
MARKER = re.compile(r"@@INFISICAL:(/[A-Za-z0-9_/-]+)@@")
PRIVATE = (b"AGE-SE" + b"CRET-KEY-", b"-----BEGIN " + b"PRIVATE KEY-----",
           b"-----BEGIN OPENSSH " + b"PRIVATE KEY-----", b"-----BEGIN RSA " + b"PRIVATE KEY-----")


class Refused(Exception):
    pass


def require(ok, code, location=""):
    if not ok:
        # Location is always a reviewed repository path/pointer, never a value.
        raise Refused(code + (" " + location if location else ""))


def digest(data):
    return hashlib.sha256(data).hexdigest()


def sha(value):
    require(isinstance(value, str) and re.fullmatch(r"[0-9a-f]{40}", value), "INVALID_COMMIT")
    return value


def relative(value):
    require(isinstance(value, str) and not value.startswith("/") and "\\" not in value
            and all(p not in ("", ".", "..") for p in value.split("/")), "UNSAFE_PATH")
    return value


def safe_path(path):
    require(not any(p.is_symlink() for p in (path, *path.parents)), "SYMLINK_REFUSED")


def read_file(root, name):
    path = root / relative(name)
    safe_path(path)
    require(path.is_file() and path.stat().st_size <= MAX_FILE, "SOURCE_FILE_MISSING_OR_OVERSIZED", name)
    return path.read_bytes()


def json_pairs(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, "DUPLICATE_KEY")
        result[key] = value
    return result


def loads(data):
    return json.loads(data, object_pairs_hook=json_pairs,
                      parse_constant=lambda value: require(False, "NONFINITE_NUMBER"))


class UniqueLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            require(isinstance(key, str) and key not in result, "DUPLICATE_OR_NONSTRING_YAML_KEY")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def yaml_load(data):
    # YAML aliases can obscure provenance and create recursive/expanding trees.
    require(not any(isinstance(t, (yaml.tokens.AliasToken, yaml.tokens.AnchorToken))
                    for t in yaml.scan(data)), "YAML_ANCHOR_REVIEW_REQUIRED")
    return yaml.load(data, Loader=UniqueLoader)


def pointer(path):
    return "/" + "/".join(str(p).replace("~", "~0").replace("/", "~1") for p in path)


def approved_scalar(old, new, location, path=()):
    if isinstance(old, str) and MARKER.search(old):
        if MARKER.fullmatch(old):
            require(isinstance(new, (str, int, float)) and not isinstance(new, bool), "PRIVATE_FIELD_TYPE_CHANGED", location)
        else:
            require(isinstance(new, str), "PRIVATE_FIELD_TYPE_CHANGED", location)
            # Public code/URI surrounding an embedded binding must remain exact.
            parts = MARKER.split(old)
            pattern = "".join(re.escape(part) if i % 2 == 0 else ".*?" for i, part in enumerate(parts))
            require(re.fullmatch(pattern, new, re.DOTALL), "BOUND_TEXT_CHANGED_REVIEW_REQUIRED", location)
        return old
    if isinstance(old, str) and old.startswith("${INFISICAL_"):
        require(new == old, "COMPOSE_PRIVATE_REFERENCE_CHANGED", location)
        return old
    require(type(new) is type(old), "FIELD_TYPE_CHANGED", location)
    if type(old) is bool:
        return new
    if type(old) in (int, float):
        require(math.isfinite(new) and abs(new) <= 10**12, "INVALID_PUBLIC_NUMBER", location)
        return new
    if isinstance(old, str) and path and path[-1] == "image":
        # Only an existing public registry/repository may receive a new tag/digest.
        repository = image_repository(old)
        require(image_repository(new) == repository, "IMAGE_REPOSITORY_REVIEW_REQUIRED", location)
        return new
    if isinstance(old, str) and "ports" in path:
        require(re.fullmatch(r"[0-9]{1,5}:[0-9]{1,5}(?:/(?:tcp|udp))?", new), "PORT_REVIEW_REQUIRED", location)
        require(all(0 < int(n) <= 65535 for n in new.split("/")[0].split(":")), "INVALID_PORT", location)
        return new
    require(new == old, "PUBLIC_STRING_REVIEW_REQUIRED", location)
    return old


def sanitize_tree(old, new, name, path=(), omit=(), preserved=()):
    location = name + ":" + pointer(path)
    if path in preserved:
        require(isinstance(old, str) and isinstance(new, str), "GENERIC_FIELD_TYPE_CHANGED", location)
        return copy.deepcopy(old)
    if isinstance(old, dict):
        require(isinstance(new, dict), "STRUCTURE_CHANGED", location)
        new = {k: v for k, v in new.items() if (*path, k) not in omit}
        require(set(new) == set(old), "STRUCTURE_REVIEW_REQUIRED", location)
        return {k: sanitize_tree(v, new[k], name, (*path, k), omit, preserved) for k, v in old.items()}
    if isinstance(old, list):
        require(isinstance(new, list) and len(new) == len(old), "LIST_STRUCTURE_REVIEW_REQUIRED", location)
        return [sanitize_tree(v, new[i], name, (*path, i), omit, preserved) for i, v in enumerate(old)]
    return approved_scalar(old, new, location, path)


def normalize_indexer_runtime(old, new, name):
    # Jackett saves hidden CookieHeader/LastError as nullable strings in its
    # positional config list. Neither session cookies nor error text is public.
    require(isinstance(old, list) and isinstance(new, list) and len(new) == len(old),
            "LIST_STRUCTURE_REVIEW_REQUIRED", name + ":/")
    result = copy.deepcopy(new)
    for index, row in enumerate(old):
        if row.get("type") != "hiddendata" or row.get("id") not in ("cookieheader", "lasterror"):
            continue
        location = name + ":" + pointer((index, "value"))
        require(row.get("value") == "", "INVALID_INDEXER_RUNTIME_DEFAULT", location)
        actual = result[index]
        require(isinstance(actual, dict) and set(actual) == set(row)
                and actual.get("id") == row["id"] and actual.get("type") == row["type"],
                "INDEXER_RUNTIME_STRUCTURE_REVIEW_REQUIRED", location)
        require(actual["value"] is None or isinstance(actual["value"], str),
                "INDEXER_RUNTIME_TYPE_CHANGED", location)
        actual["value"] = ""
    return result


def env_values(data):
    result = {}
    for line in data.decode("utf-8").splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, sep, value = line.partition("=")
        require(sep and re.fullmatch(r"[A-Z][A-Z0-9_]*", key) and key not in result, "DOTENV_FORMAT_REVIEW_REQUIRED")
        result[key] = value.strip()
    return result


def public_env(old_data, new_data, private_keys, name):
    old, new = env_values(old_data), env_values(new_data)
    require(set(old) <= set(new) and set(new) <= set(old) | set(private_keys), "DOTENV_KEYS_REVIEW_REQUIRED", name)
    values = {}
    for key, value in old.items():
        location = name + ":" + key
        if key in private_keys or value in ("", '""', "''"):
            values[key] = value
        elif re.fullmatch(r"-?[0-9]+", value):
            require(re.fullmatch(r"-?[0-9]+", new[key]) and abs(int(new[key])) <= 10**12,
                    "DOTENV_NUMBER_REVIEW_REQUIRED", location)
            values[key] = new[key]
        elif value.lower() in ("true", "false", "on", "off", "yes", "no"):
            group = next(g for g in ({"true", "false"}, {"on", "off"}, {"yes", "no"}) if value.lower() in g)
            require(new[key].lower() in group, "DOTENV_BOOLEAN_REVIEW_REQUIRED", location)
            values[key] = new[key]
        elif key == "LOG_LEVEL":
            require(new[key].lower() in {"trace", "debug", "info", "warn", "warning", "error", "critical", "fatal"},
                    "LOG_LEVEL_REVIEW_REQUIRED", location)
            values[key] = new[key]
        else:
            require(new[key] == value, "DOTENV_PUBLIC_STRING_REVIEW_REQUIRED", location)
            values[key] = value
    # Keep only previously public comments. Never copy live comments or extra keys.
    lines = []
    for line in old_data.decode("utf-8").splitlines():
        key, sep, _ = line.partition("=")
        lines.append(key + "=" + values[key] if sep and key in values else line)
    return ("\n".join(lines) + "\n").encode()


def toml_dump(value):
    lines = []
    def scalar(item):
        if isinstance(item, list):
            require(not any(isinstance(v, dict) for v in item), "TOML_ARRAY_TABLE_REVIEW_REQUIRED")
            return "[" + ", ".join(scalar(v) for v in item) + "]"
        require(type(item) in (str, int, float, bool), "TOML_VALUE_REVIEW_REQUIRED")
        return json.dumps(item, ensure_ascii=False, allow_nan=False)
    def table(obj, path):
        if path:
            lines.append("[" + ".".join(json.dumps(p) for p in path) + "]")
        for key, val in obj.items():
            if not isinstance(val, dict):
                lines.append(json.dumps(key) + " = " + scalar(val))
        lines.append("")
        for key, val in obj.items():
            if isinstance(val, dict):
                table(val, (*path, key))
    table(value, ())
    result = ("\n".join(lines) + "\n").encode()
    require(tomllib.loads(result.decode()) == value, "TOML_SERIALIZATION_MISMATCH")
    return result


def image_repository(value):
    require(isinstance(value, str) and re.fullmatch(r"[a-z0-9./_-]+(?::[A-Za-z0-9_.-]+|@sha256:[0-9a-f]{64})?", value),
            "INVALID_PUBLIC_IMAGE")
    return value.split("@")[0].rsplit(":", 1)[0]


def run(args, location=""):
    env = os.environ.copy()
    env.update(GH_HOST="github.com", GH_PROMPT_DISABLED="1", GIT_TERMINAL_PROMPT="0")
    try:
        result = subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, env=env, check=False)
    except OSError:
        raise Refused("DEPENDENCY_UNAVAILABLE") from None
    require(result.returncode == 0, "COMMAND_FAILED", location)
    return result.stdout


class GitHub:
    def api(self, endpoint, body=None):
        args = ["gh", "api", "--hostname", "github.com", PREFIX + "/" + endpoint if endpoint else PREFIX]
        if body is None:
            args += ["--method", "GET", "--header", "Cache-Control: no-cache"]
            return loads(run(args))
        with tempfile.TemporaryDirectory(prefix="public-stack-api-") as temporary:
            request = Path(temporary) / "request.json"
            request.write_text(json.dumps(body), encoding="utf-8")
            args += ["--method", "PATCH" if endpoint.startswith("git/refs/") else "POST", "--input", str(request)]
            return loads(run(args))

    def preflight(self):
        info = self.api("")
        require(info.get("full_name") == REPOSITORY and info.get("private") is False
                and info.get("permissions", {}).get("push") is True, "PUBLIC_REPOSITORY_WRITE_ACCESS_REQUIRED")

    def head(self):
        return sha(self.api("git/ref/heads/main")["object"]["sha"])

    def baseline(self, commit):
        tree = self.api("git/trees/" + commit + "?recursive=1")
        require(not tree.get("truncated"), "TRUNCATED_PUBLIC_TREE")
        paths = {e["path"]: e for e in tree["tree"] if e["type"] == "blob"}
        result = {}
        for name, entry in paths.items():
            if (name in {"docker-compose.yml", "config/dr-render-plan.json", "config/private-config-overlays.json",
                        "config/compose.dr.yaml", "config/image-lock.json", "data/postgres/postgresql.conf"}
                    or re.fullmatch(r"data/[a-z0-9-]+/\.env", name)
                    or name.startswith("config/dr-templates/") and name.endswith(".template")):
                require(entry["mode"] in ("100644", "100755"), "UNSAFE_PUBLIC_MODE")
                blob = self.api("git/blobs/" + sha(entry["sha"]))
                require(blob.get("encoding") == "base64" and blob.get("size", MAX_FILE + 1) <= MAX_FILE, "INVALID_PUBLIC_BLOB")
                data = base64.b64decode(blob["content"], validate=False)
                require(hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest() == entry["sha"],
                        "PUBLIC_BLOB_HASH_MISMATCH")
                result[name] = data
        return result


def docker_locks(source, compose, old_locks):
    ids = run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project.working_dir=" + str(source)]).decode().split()
    images = {}
    fmt = '{{json (index .Config.Labels "com.docker.compose.service")}} {{json .Image}} {{json .Config.Image}}'
    decoder = json.JSONDecoder()
    for container in ids:
        require(re.fullmatch(r"[0-9a-f]{12,64}", container), "INVALID_CONTAINER_ID")
        text = run(["docker", "inspect", "--format", fmt, container]).decode()
        values, pos = [], 0
        for _ in range(3):
            while pos < len(text) and text[pos].isspace():
                pos += 1
            value, pos = decoder.raw_decode(text, pos)
            values.append(value)
        service, image_id, configured = values
        require(service in compose["services"], "UNREVIEWED_RUNTIME_SERVICE")
        require(configured == compose["services"][service]["image"], "COMPOSE_IMAGE_NOT_APPLIED", "docker-compose.yml:services/" + service)
        require(service not in images or images[service] == image_id, "MIXED_SERVICE_IMAGES")
        images[service] = image_id
    require(images, "NO_STACK_CONTAINERS_FOUND")
    result = copy.deepcopy(old_locks)
    for service, image_id in images.items():
        require(re.fullmatch(r"sha256:[0-9a-f]{64}", image_id), "INVALID_IMAGE_ID")
        metadata = '{"RepoDigests":{{json .RepoDigests}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}'
        data = loads(run(["docker", "image", "inspect", "--format", metadata, image_id]))
        # Request only public image metadata; never Config.Env, labels or auth.
        repo = image_repository(compose["services"][service]["image"])
        candidates = [d for d in data.get("RepoDigests", []) if d.startswith(repo + "@sha256:")]
        require(candidates and len(set(candidates)) == 1, "PUBLIC_IMAGE_DIGEST_UNAVAILABLE", "docker-compose.yml:services/" + service)
        require(re.fullmatch(re.escape(repo) + r"@sha256:[0-9a-f]{64}", candidates[0]), "INVALID_REPOSITORY_DIGEST")
        platform = data.get("Os", "") + "/" + data.get("Architecture", "")
        require(platform in {"linux/amd64", "linux/arm64"}, "IMAGE_PLATFORM_REVIEW_REQUIRED")
        result[service] = {"image": candidates[0], "platform": platform,
                           "configured_image": compose["services"][service]["image"]}
    return result


def render(source, baseline, image_reader=docker_locks):
    inputs = {}
    def read(name):
        data = read_file(source, name)
        inputs[name] = data
        return data
    outputs = {}
    original_compose = yaml_load(baseline["docker-compose.yml"])
    compose = sanitize_tree(original_compose, yaml_load(read("docker-compose.yml")), "docker-compose.yml")
    outputs["docker-compose.yml"] = (yaml.safe_dump(compose, sort_keys=False, allow_unicode=True) + "").encode()
    plan = loads(baseline["config/dr-render-plan.json"])
    private_keys = {ref.rsplit("/", 1)[1] for ref in plan["required_values"]}
    private_keys.update(ref["key"] for values in plan["env_files"].values() for ref in values.values())
    for name, data in baseline.items():
        if re.fullmatch(r"data/[a-z0-9-]+/\.env", name):
            outputs[name] = public_env(data, read(name), private_keys, name)
    overlays = {item["file"]: item for item in loads(baseline["config/private-config-overlays.json"])}
    for item in plan["files"]:
        if "template" not in item:
            continue
        name, destination = item["template"], item["destination"]
        data, live = baseline[name], read(destination)
        omit = tuple((field,) for field in overlays.get(destination, {}).get("omit_runtime_identity_fields", []))
        omit += tuple((field,) for field in plan.get("runtime_fields", {}).get(destination, []) if item["format"] == "json")
        if item["format"] == "json":
            old = loads(data)
            # Several reviewed Honey fields intentionally expose generic defaults.
            preserved = ()
            if destination == "data/honey/data/config/config.json":
                preserved = tuple(("services", i, key) for i, row in enumerate(old["services"])
                                  for key in ("name", "desc") if key in row)
                preserved += tuple(("services", i, key) for i, row in enumerate(old["services"])
                                   for key in ("href", "icon") if row.get(key) in ("#", ""))
                preserved += (("services", 8, "icon"),)
                preserved += tuple(("ui", key) for key in ("name", "desc", "icon") if key in old["ui"])
            new = yaml_load(live) if destination.endswith((".yaml", ".yml")) else loads(live)
            if re.fullmatch(r"data/jackett/data/Jackett/Indexers/[a-z0-9-]+\.json", destination):
                new = normalize_indexer_runtime(old, new, name)
            safe = sanitize_tree(old, new, name, omit=omit, preserved=preserved)
            outputs[name] = data if safe == old else (json.dumps(safe, indent=2, ensure_ascii=False) + "\n").encode()
        elif item["format"] == "toml":
            old, new = tomllib.loads(data.decode()), tomllib.loads(live.decode())
            safe = sanitize_tree(old, new, name)
            outputs[name] = data if safe == old else toml_dump(safe)
        elif item["format"] in ("text", "ini-token"):
            # Text/INI requires an exact public skeleton; bindings are masked.
            safe = approved_scalar(data.decode(), live.decode(), name)
            outputs[name] = safe.encode()
        else:
            raise Refused("TEMPLATE_FORMAT_REVIEW_REQUIRED")
    public_conf = "data/postgres/postgresql.conf"
    require(read(public_conf) == baseline[public_conf], "POSTGRES_CONFIGURATION_REVIEW_REQUIRED", public_conf)
    outputs[public_conf] = baseline[public_conf]
    old_locks = loads(baseline["config/image-lock.json"])
    locks = image_reader(source, compose, old_locks)
    require(set(locks) == set(compose["services"]) == set(old_locks), "IMAGE_INVENTORY_MISMATCH")
    override = loads(baseline["config/compose.dr.yaml"])
    for service in locks:
        require(service in override["services"], "DR_SERVICE_MISSING")
        if compose["services"][service]["image"] != original_compose["services"][service]["image"]:
            require(locks[service].get("configured_image") == compose["services"][service]["image"],
                    "CHANGED_IMAGE_NOT_VERIFIED", "docker-compose.yml:services/" + service)
        override["services"][service]["image"] = locks[service]["image"]
        # Platform is locked along with the digest for reproducible recovery.
        override["services"][service]["platform"] = locks[service]["platform"]
    outputs["config/image-lock.json"] = (json.dumps(locks, indent=2) + "\n").encode()
    outputs["config/compose.dr.yaml"] = (json.dumps(override, indent=2) + "\n").encode()
    for data in outputs.values():
        require(not any(marker in data for marker in PRIVATE), "PRIVATE_IDENTITY_IN_CANDIDATE")
    # Read source files again and image metadata twice to reject a torn snapshot.
    require(all(read_file(source, n) == v for n, v in inputs.items()), "SOURCE_CHANGED_DURING_EXPORT")
    require(image_reader(source, compose, old_locks) == locks, "IMAGES_CHANGED_DURING_EXPORT")
    return {name: data for name, data in outputs.items() if data != baseline[name]}


def confirm(read, predicate):
    for delay in (0, 1, 2, 4, 8):
        if delay:
            time.sleep(delay)
        result = read()
        if predicate(result):
            return result
    raise Refused("PUBLICATION_UNCONFIRMED")


def write_record(path, value):
    safe_path(path)
    temporary = path.with_suffix(".tmp")
    safe_path(temporary)
    with temporary.open("w", encoding="utf-8") as file:
        json.dump(value, file)
        file.write("\n")
        file.flush()
        os.fsync(file.fileno())
    temporary.chmod(0o600)
    os.replace(temporary, path)


def verify_commit(github, commit, hashes):
    tree = github.api("git/trees/" + sha(commit) + "?recursive=1")
    require(not tree.get("truncated"), "TRUNCATED_VERIFICATION_TREE")
    entries = {e["path"]: e for e in tree["tree"] if e["type"] == "blob"}
    for name, expected in hashes.items():
        require(name in entries, "PUBLISHED_FILE_MISSING")
        blob = github.api("git/blobs/" + sha(entries[name]["sha"]))
        data = base64.b64decode(blob["content"])
        require(digest(data) == expected, "PUBLISHED_HASH_MISMATCH")


def publish(github, state, base, changes):
    pending = state / "pending.json"
    if pending.exists():
        safe_path(pending)
        intent = loads(pending.read_bytes())
        require(intent.get("repository") == REPOSITORY, "PENDING_RECORD_MISMATCH")
        commit = sha(intent["commit"])
        # Never replay a branch mutation after an ambiguous write response.
        require(github.head() == commit, "PENDING_PUBLICATION_NEEDS_REVIEW")
        verify_commit(github, commit, intent["hashes"])
        write_record(state / "current.json", {"repository": REPOSITORY, "commit": commit, "verified": True})
        pending.unlink()
        print("PUBLIC_SYNC_RECOVERED PASS commit=" + commit)
        return commit
    if not changes:
        require(github.head() == base, "PUBLIC_BASE_MOVED")
        write_record(state / "current.json", {"repository": REPOSITORY, "commit": base, "verified": True})
        print("PUBLIC_SYNC PASS unchanged commit=" + base)
        return base
    require(github.head() == base, "PUBLIC_BASE_MOVED")
    parent = github.api("git/commits/" + sha(base))
    elements = [{"path": relative(name), "mode": "100644", "type": "blob", "content": data.decode("utf-8")}
                for name, data in sorted(changes.items())]
    tree = sha(github.api("git/trees", {"base_tree": parent["tree"]["sha"], "tree": elements})["sha"])
    commit = sha(github.api("git/commits", {"message": "Sync reviewed stack configuration " + dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
                                          "tree": tree, "parents": [base]})["sha"])
    hashes = {name: digest(data) for name, data in changes.items()}
    write_record(pending, {"repository": REPOSITORY, "base": base, "commit": commit, "hashes": hashes})
    github.api("git/refs/heads/main", {"sha": commit, "force": False})
    confirm(github.head, lambda value: value == commit)
    verify_commit(github, commit, hashes)
    write_record(state / "current.json", {"repository": REPOSITORY, "commit": commit, "verified": True})
    pending.unlink()
    print("PUBLIC_SYNC PASS commit=" + commit + " files=" + str(len(changes)))
    return commit


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, default=Path("/home/pi/streams-aio"))
    parser.add_argument("--state", type=Path, default=Path("/home/pi/.local/state/streams-aio-public-sync"))
    parser.add_argument("--publish", action="store_true", help="otherwise read-only check; no GitHub mutation")
    args = parser.parse_args()
    os.umask(0o077)
    safe_path(args.source)
    safe_path(args.state)
    require(args.source.is_absolute() and args.source.is_dir() and args.state.is_absolute(), "ABSOLUTE_PATHS_REQUIRED")
    require(not args.state.resolve().is_relative_to(args.source.resolve()), "STATE_MUST_BE_OUTSIDE_STACK")
    args.state.mkdir(parents=True, exist_ok=True, mode=0o700)
    if os.name == "posix":
        require(args.state.stat().st_uid == os.getuid() and args.state.stat().st_mode & 0o077 == 0, "UNSAFE_STATE_PERMISSIONS")
        import fcntl
        lock_path = args.state / "run.lock"
        safe_path(lock_path)
        with lock_path.open("a") as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise Refused("SYNC_ALREADY_RUNNING") from None
            execute(args)
    else:
        require(not args.publish, "POSIX_LOCK_REQUIRED_FOR_PUBLICATION")
        execute(args)


def execute(args):
    github = GitHub()
    github.preflight()
    if args.publish and (args.state / "pending.json").exists():
        publish(github, args.state, "", {})
        return
    base = github.head()
    changes = render(args.source, github.baseline(base))
    print("PUBLIC_SYNC_CHECK PASS changed_files=" + str(len(changes)))
    for name in sorted(changes):
        print("PUBLIC_FILE " + name)
    if args.publish:
        publish(github, args.state, base, changes)


if __name__ == "__main__":
    try:
        main()
    except Refused as error:
        print("PUBLIC_SYNC FAIL: " + str(error), file=sys.stderr)
        sys.exit(1)
    except Exception:
        # Parsers and transports may include deployment values in exceptions.
        print("PUBLIC_SYNC FAIL: operation stopped; no source contents logged", file=sys.stderr)
        sys.exit(1)
