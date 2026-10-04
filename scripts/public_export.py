"""Local -> public export. Private trees are never walked; values never enter logs.

This is a second export policy, reusing the existing verified GitHub transport.
Unknown runtime files stay private. This is not a general-purpose secret oracle.
"""
import ast
import copy
import json
import os
from pathlib import Path
import re
import stat

INVENTORY = "config/public-sync-inventory.json"
LAYOUT = "config/public-stack-layout.json"
RESERVED = {INVENTORY, LAYOUT, ".gitignore", ".github", "systemd",
            "config/dr-render-plan.json", "config/dr-manifest.yaml",
            "config/private-config-overlays.json", "config/image-lock.json", "config/compose.dr.yaml",
            "scripts/sync-public-stack.py", "scripts/public_export.py",
            "scripts/verify-public-sync-systemd.sh", "scripts/requirements-public-sync.txt",
            "scripts/install-public-sync-policy.py", "scripts/install-local-guides.py",
            "docs/PUBLIC-STACK-SYNC.md", "docs/SEAMLESS-PUBLIC-SYNC.md"}
PRIVATE_DIRS = {".git", ".secrets", ".generated", ".private-dr", ".ssh", ".gnupg", ".aws",
                "__pycache__", "node_modules", ".venv", "venv", "secrets", "private", "credentials",
                "backups", "backup", "containers-backup", "Aiostreams-Stremio-Backups"}
PRIVATE_SUFFIX = re.compile(r"\.(?:db(?:-.*)?|sqlite.*|sqlitedb|mdb|rdb|aof|dump|bak|backup|before|old|log|pem|key|crt|cer|p12|pfx|jks|kdb|age|zip|gz|tgz|tar|7z|rar|wal|swp|swo|tmp|pid|sock)$", re.I)
PRIVATE_NAME = re.compile(r"(?:^|[-_.])(?:secret|secrets|credential|credentials|token|tokens|password|passwd|private|session|sessions|cookie|cookies|backup)(?:[-_.]|$)", re.I)
TEXT_SUFFIXES = {".md", ".txt", ".sh", ".py", ".js", ".mjs", ".ts", ".json", ".yaml", ".yml",
                 ".toml", ".ini", ".conf", ".cfg", ".sql", ".example", ".template"}
SENSITIVE_KEY = re.compile(r"(?:^|[_-])(?:password|passwd|passphrase|secret|token|apikey|api_key|authkey|auth_key|cookie|credential|credentials|private_key|client_secret|authorization|connection_string)(?:$|[_-])", re.I)
SAFE_KEY_END = re.compile(r"(?:enabled|disabled|enable|disable|required|optional|secure|httponly|samesite|refresh|max_age|login|ttl|timeout|length|algorithm|method|mode|type|expiry|expiration|duration|interval|name|header_name|file|path)$", re.I)
VARIABLE = re.compile(r"\$\{([A-Z][A-Z0-9_]*)(?:(?::[-?+]|[-?+])[^}]*)?\}")
EXAMPLE_REFERENCE = re.compile(r"INFISICAL:/[A-Za-z0-9_/-]+")
SQL_CREDENTIAL = re.compile(r"\b(?:password|identified\s+by)\s+['\"][^'\"]+['\"]", re.I)
PY_DYNAMIC = object()
TOKEN_PATTERNS = (re.compile(r"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{50,})"),
                  re.compile(r"AKIA[0-9A-Z]{16}"), re.compile(r"xox[baprs]-[A-Za-z0-9-]{20,}"),
                  re.compile(r"eyJ[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}\.[A-Za-z0-9_-]{15,}"),
                  re.compile(r"[a-zA-Z][a-zA-Z0-9+.-]*://[^\s/@:]+:[^\s/@]+@"),
                  re.compile(r"[?&](?:api_?key|token|password|secret|auth)=[^\s&#\"']+", re.I))


def sensitive(key):
    key = re.sub(r"([a-z])([A-Z])", r"\1_\2", str(key))
    return bool(SENSITIVE_KEY.search(key)) and not SAFE_KEY_END.search(key)


def public_reference(value, sync):
    return isinstance(value, str) and bool(sync.MARKER.fullmatch(value)
                                          or EXAMPLE_REFERENCE.fullmatch(value) or VARIABLE.fullmatch(value))


def python_literal(node):
    # Static syntax inspection only: never import or execute application code.
    if isinstance(node, ast.Constant): return node.value
    if isinstance(node, ast.BinOp) and isinstance(node.op, ast.Add):
        left, right = python_literal(node.left), python_literal(node.right)
        if type(left) is type(right) and isinstance(left, (str, bytes)): return left + right
    if isinstance(node, ast.JoinedStr) and all(isinstance(row, ast.Constant) and isinstance(row.value, str) for row in node.values):
        return "".join(row.value for row in node.values)
    return PY_DYNAMIC


def python_target_sensitive(node):
    if isinstance(node, ast.Name): return sensitive(node.id)
    if isinstance(node, ast.Attribute): return sensitive(node.attr)
    if isinstance(node, ast.Subscript) and isinstance(node.slice, ast.Constant): return sensitive(node.slice.value)
    if isinstance(node, (ast.Tuple, ast.List)): return any(python_target_sensitive(row) for row in node.elts)
    return False


def allowed(name):
    """Immutable floor beneath Git ignore rules; negation cannot permit a DB."""
    parts = name.split("/")
    if name in RESERVED or parts[0] in {".github", "systemd", "tests"}:
        return False
    if any(p.lower() in {d.lower() for d in PRIVATE_DIRS} for p in parts) or PRIVATE_SUFFIX.search(parts[-1]):
        return False
    if PRIVATE_NAME.search(parts[-1]):
        return False
    if parts[-1].startswith(".env"):
        return name == ".env.example" or (len(parts) == 3 and parts[0] == "data"
                                         and parts[-1] in {".env", ".env.example"})
    if any(p.startswith(".") for p in parts):
        return False
    if parts[0] == "data":
        return name == "data/postgres/postgresql.conf" or (len(parts) >= 4 and parts[2] == "public"
                                                           and Path(name).suffix in TEXT_SUFFIXES)
    if parts[0] == "config":
        return len(parts) >= 3 and parts[1] in {"public", "templates"} and Path(name).suffix in TEXT_SUFFIXES
    return Path(name).suffix in TEXT_SUFFIXES and (len(parts) == 1 or parts[0] in {"docs", "scripts"})


def candidates(source, sync):
    names = []
    skipped = 0
    for folder, dirs, files in os.walk(source, followlinks=False):
        rel = Path(folder).relative_to(source).parts
        dirs[:] = sorted(d for d in dirs if d.lower() not in {p.lower() for p in PRIVATE_DIRS} and not d.startswith(".")
                         and not (Path(folder) / d).is_symlink()
                         and (not rel or rel[0] != "data" or len(rel) != 2 or d == "public")
                         and (not rel or rel[0] != "config" or len(rel) != 1 or d in {"public", "templates"})
                         and (rel or d in {"config", "data", "docs", "scripts"}))
        for file in sorted(files):
            name = (Path(folder) / file).relative_to(source).as_posix()
            if allowed(name):
                sync.safe_path(source / name)
                names.append(name)
            else:
                skipped += 1
    # The source ignore file may only add exclusions; the floor above is fixed.
    if (source / ".git").is_dir() and names:
        result = sync.run(["git", "-C", str(source), "check-ignore", "--no-index", "--stdin", "-z"],
                          acceptable=(0, 1), input_data=("\0".join(names) + "\0").encode())
        ignored = set(result.decode().split("\0"))
        names = [n for n in names if n not in ignored]
    sync.require(len(names) <= 1000, "PUBLIC_FILE_LIMIT")
    return names, skipped


def decode(data, sync, name):
    sync.require(not data.startswith((b"SQLite format 3\0", b"PGDMP", b"PK\x03\x04", b"\x1f\x8b")),
                 "DATABASE_OR_ARCHIVE_REFUSED", name)
    sync.require(b"\0" not in data and not any(c < 9 or 13 < c < 32 for c in data), "BINARY_FILE_REFUSED", name)
    try:
        return data.decode("utf-8")
    except UnicodeError:
        raise sync.Refused("NON_UTF8_PUBLIC_FILE " + name) from None


class Guard:
    def __init__(self, source, baseline, sync, read):
        self.sync, self.read = sync, read
        self.baseline = baseline
        self.plan = sync.loads(baseline["config/dr-render-plan.json"])
        self.values = {}
        # Common settings already declared public are not evidence of a leak.
        self.public_values = set()
        self.reviewed_healthchecks = {}
        compose = sync.yaml_load(baseline["docker-compose.yml"])
        for service, row in compose["services"].items():
            test = row.get("healthcheck", {}).get("test")
            path = ("services", service, "healthcheck", "test")
            if isinstance(test, str):
                self.reviewed_healthchecks[path] = test
            elif isinstance(test, list):
                for index, value in enumerate(test):
                    if isinstance(value, str): self.reviewed_healthchecks[(*path, index)] = value
        def literals(row):
            if isinstance(row, dict):
                for key, value in row.items(): literals(value)
            elif isinstance(row, list):
                for value in row: literals(value)
            elif isinstance(row, str) and not sync.MARKER.search(row): self.public_values.add(row)
        literals(compose)
        for name, data in baseline.items():
            if name.endswith("/.env"):
                self.public_values.update(sync.env_values(data).values())
        for item in self.plan["files"]:
            if item.get("template") and item["format"] == "json": literals(sync.loads(baseline[item["template"]]))
        for name, bindings in self.plan["env_files"].items():
            if (source / name).is_file():
                rows = sync.env_values(read(name))
                for key, binding in bindings.items():
                    if key in rows:
                        self.remember(rows[key], binding["path"] + "/" + binding["key"])
        for name, export in self.plan["service_folder_exports"].items():
            if (source / name).is_file():
                for key, value in sync.env_values(read(name)).items():
                    binding = export["overrides"].get(key, {"path": export["path"], "key": key})
                    self.remember(value, binding["path"] + "/" + binding["key"])
        if (source / ".env").is_file():
            for key, value in sync.env_values(read(".env")).items():
                binding = self.plan["root_env"].get(key)
                if binding or sensitive(key):
                    self.remember(value, binding["path"] + "/" + binding["key"] if binding else None)
        # Additional Agent env files supply a leak-detection corpus, not guessed mappings.
        private = source / ".secrets"
        if private.is_dir():
            sync.safe_path(private)
            for path in sorted(private.glob("*.env")):
                sync.require(len(self.values) < 3000, "PRIVATE_INPUT_LIMIT")
                for key, value in sync.env_values(read(path.relative_to(source).as_posix())).items():
                    existing = [ref for ref in self.plan["required_values"] if ref.rsplit("/", 1)[1] == key]
                    # Reuse a unique published binding; never invent a folder for a new key.
                    self.remember(value, existing[0] if len(existing) == 1 else None)

    def remember(self, value, ref):
        # Dotenv producer outputs may quote values. Never evaluate shell syntax.
        if value.startswith('"') and value.endswith('"'):
            try:
                value = json.loads(value)
            except ValueError:
                value = value[1:-1]
        elif value.startswith("'") and value.endswith("'"):
            value = value[1:-1]
        if value and value not in self.public_values and value.lower() not in {"true", "false", "yes", "no", "on", "off"}:
            self.values.setdefault(value, set()).add(ref)

    def mask(self, text, name, preferred=()):
        for value, references in sorted(self.values.items(), key=lambda row: -len(row[0])):
            if value != text and (len(value) < 8 or value not in text):
                continue
            choices = (set(preferred) & references) or (references - {None})
            self.sync.require(len(choices) == 1, "PRIVATE_VALUE_NEEDS_EXPLICIT_BINDING", name)
            text = text.replace(value, "@@INFISICAL:" + next(iter(choices)) + "@@")
        return text

    def aligned(self, old, new):
        # Config rows (Jackett IDs, extension field names) may be inserted/reordered.
        # Position alone must not attach an old private binding to a different row.
        for key in ("id", "name"):
            if old and all(isinstance(row, dict) and isinstance(row.get(key), str) for row in old + new) and not any(self.sync.MARKER.search(row[key]) for row in old):
                before = {row[key]: row for row in old}
                after = {row[key]: row for row in new}
                if len(before) == len(old) and len(after) == len(new):
                    return [(before.get(row[key]), row) for row in new]
        if self.sync.MARKER.search(json.dumps(old)):
            self.sync.require(len(old) == len(new), "BOUND_ARRAY_LAYOUT_REVIEW_REQUIRED")
        return [(old[i] if i < len(old) else None, row) for i, row in enumerate(new)]

    def bindings(self, old, new):
        """Collect only explicitly bound values before scanning any public candidate."""
        if isinstance(old, dict) and isinstance(new, dict):
            for key in old.keys() & new.keys(): self.bindings(old[key], new[key])
        elif isinstance(old, list) and isinstance(new, list):
            for a, b in self.aligned(old, new): self.bindings(a, b)
        elif isinstance(old, str) and isinstance(new, (str, int, float)) and not isinstance(new, bool):
            marker = self.sync.MARKER.fullmatch(old)
            if marker: self.remember(str(new), marker[1])
            elif isinstance(new, str):
                for marker in self.sync.MARKER.finditer(old):
                    left, right = old[:marker.start()][-80:], old[marker.end():][:80]
                    if len(left) < 8 or len(right) < 8: continue
                    found = re.findall(re.escape(left) + "(.{1,4096}?)" + re.escape(right), new, re.S)
                    if len(found) == 1: self.remember(found[0], marker[1])

    def private_projection(self, text, name):
        if name != "docker-compose.yml":
            return text
        # Exempt only the exact reviewed scalar in its original healthcheck field.
        # YAML source spans preserve all other text, including comments. Removing
        # a matching substring globally would also hide leaks in newly added fields.
        self.sync.yaml_load(text)
        node = self.sync.yaml.compose(text, Loader=self.sync.UniqueLoader)
        spans = []
        def visit(row, path=()):
            if isinstance(row, self.sync.yaml.nodes.MappingNode):
                for key, value in row.value: visit(value, (*path, key.value))
            elif isinstance(row, self.sync.yaml.nodes.SequenceNode):
                for index, value in enumerate(row.value): visit(value, (*path, index))
            elif isinstance(row, self.sync.yaml.nodes.ScalarNode) and row.tag == "tag:yaml.org,2002:str":
                if path in self.reviewed_healthchecks and row.value == self.reviewed_healthchecks[path]:
                    spans.append((row.start_mark.index, row.end_mark.index))
        visit(node)
        for start, end in sorted(spans, reverse=True):
            text = text[:start] + '""' + text[end:]
        return text

    def scan(self, data, name, assignments=True):
        text = decode(data, self.sync, name)
        kind = name.removesuffix(".example").removesuffix(".template")
        python = None
        if assignments and kind.endswith(".py"):
            try:
                python = ast.parse(text, filename="<public-python>")
            except (SyntaxError, ValueError):
                raise self.sync.Refused("PYTHON_SOURCE_INVALID " + name) from None
        self.sync.require(not any(p in data for p in self.sync.PRIVATE), "PRIVATE_IDENTITY_REFUSED", name)
        self.sync.require(not re.search(r"-----BEGIN (?:[A-Z]+ )*PRIVATE KEY-----|PuTTY-User-Key-File:\s*\d", text),
                          "PRIVATE_IDENTITY_REFUSED", name)
        public = self.sync.MARKER.sub("", text)
        # A reference is public; a credential supplied as its default is not.
        for variable in VARIABLE.finditer(public):
            default = variable[0][len(variable[1]) + 2:-1]
            self.sync.require(not any(rx.search(default) for rx in TOKEN_PATTERNS)
                              and not any(len(v) >= 8 and v in default for v in self.values),
                              "PRIVATE_VARIABLE_DEFAULT_REFUSED", name)
            if sensitive(variable[1]):
                self.sync.require(variable[0] in {"${" + variable[1] + "}", "${" + variable[1] + ":-}", "${" + variable[1] + "-}"},
                                  "PRIVATE_VARIABLE_DEFAULT_REFUSED", name)
        public = VARIABLE.sub("", public)
        for index, rx in enumerate(TOKEN_PATTERNS):
            # A few reviewed extension fixtures contain public example URL tokens.
            # Only those exact occurrences in this same public file are grandfathered.
            prior = self.sync.MARKER.sub("", self.baseline.get(name, b"").decode())
            matches = rx.findall(public)
            self.sync.require(all(index == 5 and matches.count(value) <= rx.findall(prior).count(value)
                                  for value in matches), "CREDENTIAL_PATTERN_REFUSED", name)
        self.sync.require("-- PostgreSQL database dump" not in text and "-- MySQL dump" not in text,
                          "DATABASE_DUMP_REFUSED", name)
        sql_text = public
        if python is not None:
            # Inspect static SQL string contents, rather than Python quote syntax
            # surrounding expressions such as ' PASSWORD ' + sql_literal(value).
            sql_text = "\n".join(value for node in ast.walk(python)
                                 if isinstance(value := python_literal(node), str))
        self.sync.require(not SQL_CREDENTIAL.search(sql_text), "SQL_CREDENTIAL_REFUSED", name)
        private = VARIABLE.sub("", self.sync.MARKER.sub("", self.private_projection(text, name)))
        for value in self.values:
            self.sync.require(len(value) < 8 or value not in private, "PRIVATE_VALUE_IN_PUBLIC_FILE", name)
        # Detect literal credential assignments even in otherwise ordinary text/code.
        structured = assignments and kind.endswith((".json", ".yaml", ".yml", ".toml"))
        if structured:
            obj = (self.sync.loads(data) if kind.endswith(".json") else
                   self.sync.tomllib.loads(text) if kind.endswith(".toml") else self.sync.yaml_load(data))
            def inspect(row):
                if isinstance(row, dict):
                    pairs = list(row.items())
                    if isinstance(row.get("id"), str) and "value" in row: pairs.append((row["id"], row["value"]))
                    for key, value in pairs:
                        if sensitive(key) and type(value) not in (dict, list, bool, type(None)):
                            self.sync.require(value == "" or public_reference(value, self.sync),
                                              "LITERAL_CREDENTIAL_REFUSED", name)
                        inspect(value)
                elif isinstance(row, list):
                    for value in row: inspect(value)
            inspect(obj)
        if python is not None:
            def credential(node):
                value = python_literal(node)
                if value is PY_DYNAMIC or value is None or isinstance(value, bool): return
                if isinstance(value, bytes): value = value.decode("utf-8", errors="replace")
                self.sync.require(value == "" or public_reference(value, self.sync), "LITERAL_CREDENTIAL_REFUSED", name)
            for node in ast.walk(python):
                if isinstance(node, ast.Assign) and any(python_target_sensitive(t) for t in node.targets): credential(node.value)
                elif isinstance(node, (ast.AnnAssign, ast.AugAssign, ast.NamedExpr)) and python_target_sensitive(node.target): credential(node.value)
                elif isinstance(node, ast.Dict):
                    for key, value in zip(node.keys, node.values):
                        if isinstance(key, ast.Constant) and isinstance(key.value, str) and sensitive(key.value): credential(value)
                elif isinstance(node, ast.keyword) and node.arg and sensitive(node.arg): credential(node.value)
        for line in public.splitlines() if assignments and python is None and not structured else ():
            match = re.match(r"\s*(?:(?:export|const|let|var)\s+)?[\"']?([A-Za-z][A-Za-z0-9_-]*)[\"']?\s*[:=]\s*(.*?)\s*[,;]?$", line)
            if match and sensitive(match[1]):
                value = match[2].strip("\"' ")
                self.sync.require(not value or value in {"null", "None", "true", "false", "True", "False", "PUBLIC_BINDING", "PUBLIC_VARIABLE", "{}", "[]"}
                                  or re.fullmatch(r"(?:os\.)?(?:getenv|environ|process\.env).*", value)
                                  or EXAMPLE_REFERENCE.fullmatch(value),
                                  "LITERAL_CREDENTIAL_REFUSED", name)
        return data

    def tree(self, old, new, name, path=()):
        location = name + ":" + self.sync.pointer(path)
        if isinstance(old, str) and self.sync.MARKER.search(old):
            if self.sync.MARKER.fullmatch(old):
                self.sync.require(isinstance(new, (str, int, float)) and not isinstance(new, bool),
                                  "PRIVATE_FIELD_TYPE_CHANGED", location)
                self.remember(str(new), self.sync.MARKER.fullmatch(old)[1])
                return old
            preferred = self.sync.MARKER.findall(old)
            # Capture unchanged framing when this is the only occurrence of a private value.
            pattern = "".join(re.escape(part) if i % 2 == 0 else "(.*?)"
                              for i, part in enumerate(self.sync.MARKER.split(old)))
            match = re.fullmatch(pattern, new, re.S) if isinstance(new, str) else None
            if match:
                for value, ref in zip(match.groups(), preferred):
                    self.remember(value, ref)
            elif isinstance(new, str):
                # Updated code can keep each binding's immediate literal context.
                # Ambiguous or rewritten contexts still require an explicit binding.
                for marker in self.sync.MARKER.finditer(old):
                    left, right = old[:marker.start()][-80:], old[marker.end():][:80]
                    if len(left) < 8 or len(right) < 8: continue
                    found = re.findall(re.escape(left) + "(.{1,4096}?)" + re.escape(right), new, re.S)
                    if len(found) == 1: self.remember(found[0], marker[1])
            self.sync.require(isinstance(new, str), "PRIVATE_FIELD_TYPE_CHANGED", location)
            masked = self.mask(new, location, preferred)
            self.sync.require(all("@@INFISICAL:" + ref + "@@" in masked for ref in preferred),
                              "BOUND_CODE_NEEDS_EXPLICIT_BINDING", location)
            return masked
        if isinstance(new, dict):
            prior = old if isinstance(old, dict) else {}
            return {key: self.tree(prior.get(key), value, name, (*path, key)) for key, value in new.items()}
        if isinstance(new, list):
            prior = old if isinstance(old, list) else []
            return [self.tree(before, value, name, (*path, i))
                    for i, (before, value) in enumerate(self.aligned(prior, new))]
        self.sync.require(type(new) in (str, bool, int, float, type(None)), "PUBLIC_TYPE_REFUSED", location)
        if isinstance(new, str):
            if name == "docker-compose.yml" and path in self.reviewed_healthchecks and new == self.reviewed_healthchecks[path]:
                return new  # Existing public healthcheck; new/changed fields still undergo masking.
            new = self.mask(new, location)
            if path and sensitive(path[-1]) and new and not self.sync.MARKER.search(new) and not VARIABLE.search(new):
                self.sync.require(False, "PRIVATE_FIELD_NEEDS_EXPLICIT_BINDING", location)
        return new

    def env(self, data, name, private_keys=()):
        result = []
        for key, value in self.sync.env_values(data).items():
            if key in private_keys or sensitive(key):
                self.sync.require(not value.strip("\"' ") or value.strip("\"' ") in self.values or value.strip("\"' ") in self.public_values,
                                  "PUBLIC_ENV_PRIVATE_VALUE_MUST_COME_FROM_AGENT", name)
                continue  # Agent/private env must supply these; no guessed Infisical path.
            self.sync.require(not self.sync.MARKER.search(value), "PUBLIC_ENV_BINDING_USE_PRIVATE_ENV", name)
            result.append(key + "=" + value)
        return self.scan(("\n".join(result) + "\n").encode(), name)


def dynamic_images(source, compose, old, sync):
    ids = sync.run(["docker", "ps", "-aq", "--filter", "label=com.docker.compose.project.working_dir=" + str(source)]).decode().split()
    running = {}
    fmt = '{{json (index .Config.Labels "com.docker.compose.service")}} {{json .Image}} {{json .Config.Image}}'
    decoder = json.JSONDecoder()
    for container in ids:
        sync.require(re.fullmatch(r"[0-9a-f]{12,64}", container), "INVALID_CONTAINER_ID")
        text = sync.run(["docker", "inspect", "--format", fmt, container]).decode()
        values, pos = [], 0
        for _ in range(3):
            while pos < len(text) and text[pos].isspace(): pos += 1
            value, pos = decoder.raw_decode(text, pos); values.append(value)
        service, image, configured = values
        if service not in compose["services"]:
            continue  # A removed service can leave an orphan without becoming part of recovery.
        sync.require(configured == compose["services"][service]["image"], "COMPOSE_IMAGE_NOT_APPLIED", service)
        sync.require(service not in running or running[service] == image, "MIXED_SERVICE_IMAGES")
        running[service] = image
    result = {}
    metadata = '{"RepoDigests":{{json .RepoDigests}},"Os":{{json .Os}},"Architecture":{{json .Architecture}}}'
    for service, row in compose["services"].items():
        configured = row.get("image")
        sync.require(configured and not row.get("build"), "PUBLISHED_IMAGE_REQUIRED", service)
        sync.image_repository(configured)
        image = running.get(service, configured)
        if service in running:
            sync.require(re.fullmatch(r"sha256:[0-9a-f]{64}", image), "INVALID_IMAGE_ID")
        data = sync.loads(sync.run(["docker", "image", "inspect", "--format", metadata, image]))
        selected = sync.select_image_digest(configured, data.get("RepoDigests"), old.get(service, {}).get("image", ""), service)
        platform = data.get("Os", "") + "/" + data.get("Architecture", "")
        sync.require(platform in {"linux/amd64", "linux/arm64"}, "IMAGE_PLATFORM_REVIEW_REQUIRED", service)
        result[service] = {"image": selected, "platform": platform, "configured_image": configured}
    return result


def mounts(row, sync):
    result = []
    for volume in row.get("volumes", []):
        if isinstance(volume, str):
            bits = volume.split(":")
            sync.require(len(bits) in {1, 2, 3}, "MOUNT_SYNTAX_REVIEW_REQUIRED")
            if len(bits) < 2: continue
            src, target = bits[:2]
            kind = "bind" if src.startswith((".", "/")) else "volume"
        elif isinstance(volume, dict):
            kind, src, target = volume.get("type", "volume"), volume.get("source", ""), volume.get("target", "")
        else:
            raise sync.Refused("MOUNT_SYNTAX_REVIEW_REQUIRED")
        mode = bits[2] if isinstance(volume, str) and len(bits) == 3 else ("ro" if isinstance(volume, dict) and volume.get("read_only") else "rw")
        result.append((kind, src.removeprefix("./"), target, mode))
    return result


def toml_dump(value, sync):
    """Inline tables also cover arrays of tables without an extra dependency."""
    def scalar(row):
        if isinstance(row, dict):
            return "{ " + ", ".join(json.dumps(k) + " = " + scalar(v) for k, v in row.items()) + " }"
        if isinstance(row, list): return "[" + ", ".join(map(scalar, row)) + "]"
        sync.require(type(row) in (str, int, float, bool), "TOML_TYPE_REFUSED")
        return json.dumps(row, ensure_ascii=False, allow_nan=False)
    data = ("\n".join(json.dumps(k) + " = " + scalar(v) for k, v in value.items()) + "\n").encode()
    sync.require(sync.tomllib.loads(data.decode()) == value, "TOML_ROUNDTRIP_FAILED")
    return data


def restore_waves(compose, sync):
    dependencies = {}
    for service, row in compose["services"].items():
        deps = set(row.get("depends_on", []))
        for field in ("network_mode", "ipc", "pid"):
            value = row.get(field, "")
            if isinstance(value, str) and value.startswith("service:"): deps.add(value[8:])
        sync.require(deps <= set(compose["services"]), "MISSING_SERVICE_DEPENDENCY", service)
        dependencies[service] = deps
    done, waves = set(), []
    while len(done) < len(dependencies):
        ready = sorted(s for s, deps in dependencies.items() if s not in done and deps <= done)
        sync.require(ready, "SERVICE_DEPENDENCY_CYCLE")
        waves.append(ready); done.update(ready)
    return waves


def declared_refs(value, sync):
    refs = set()
    if isinstance(value, dict):
        if isinstance(value.get("path"), str) and isinstance(value.get("key"), str):
            refs.add(value["path"] + "/" + value["key"])
        for key, row in value.items():
            if key != "required_values": refs.update(declared_refs(row, sync))
    elif isinstance(value, list):
        for row in value: refs.update(declared_refs(row, sync))
    elif isinstance(value, str): refs.update(sync.MARKER.findall(value))
    return refs


def live_json_template(item, data, sync):
    # The render-plan format describes the public template. JSON is also the
    # canonical representation for reviewed YAML application configurations.
    # Match the existing strict exporter without accepting YAML for .json files.
    return sync.yaml_load(data) if item["destination"].endswith((".yaml", ".yml")) else sync.loads(data)


def json_template_trees(item, old, raw, sync):
    before, after = sync.loads(old), live_json_template(item, raw, sync)
    if item["destination"] != "data/honey/data/config/config.json":
        return before, after
    name = item["template"]
    # Honey's reviewed SERVICES_<index> bindings use positions. Its anonymized
    # display names are not IDs: normalize them before the generic name matcher
    # and before collecting private values. Never overwrite a bound icon first.
    sync.require(isinstance(before, dict) and isinstance(after, dict)
                 and isinstance(before.get("services"), list) and isinstance(after.get("services"), list)
                 and len(before["services"]) == len(after["services"]), "HONEY_BOUND_LAYOUT_REVIEW_REQUIRED", name)
    after = copy.deepcopy(after)
    for index, (old_row, new_row) in enumerate(zip(before["services"], after["services"])):
        sync.require(isinstance(old_row, dict) and isinstance(new_row, dict)
                     and isinstance(new_row.get("name"), str), "HONEY_BOUND_LAYOUT_REVIEW_REQUIRED", name)
        generic = old_row.get("name") == "Service " + str(index + 1)
        sync.require(generic or new_row["name"] == old_row.get("name"), "HONEY_BOUND_LAYOUT_REVIEW_REQUIRED", name)
        for key in ("name", "desc", "icon", "href"):
            value = old_row.get(key)
            preserved = key != "href" or value in ("#", "")
            if key in new_row and isinstance(value, str) and preserved and not sync.MARKER.search(value):
                new_row[key] = value
    if isinstance(before.get("ui"), dict) and isinstance(after.get("ui"), dict):
        for key in ("name", "desc", "icon"):
            value = before["ui"].get(key)
            if key in after["ui"] and isinstance(value, str) and not sync.MARKER.search(value):
                after["ui"][key] = value
    return before, after


def render(source, baseline, sync, image_reader=None):
    inputs, outputs, directories = {}, {}, {}
    def read(name):
        data = sync.read_file(source, name); inputs[name] = data; return data
    guard = Guard(source, baseline, sync, read)
    for item in guard.plan["files"]:
        if not item.get("template") or not (source / item["destination"]).is_file(): continue
        old, new = baseline[item["template"]], read(item["destination"])
        if item["format"] == "json": old, new = json_template_trees(item, old, new, sync)
        elif item["format"] == "toml": old, new = sync.tomllib.loads(old.decode()), sync.tomllib.loads(new.decode())
        else: old, new = old.decode(), new.decode()
        guard.bindings(old, new)
    compose = sync.yaml_load(read("docker-compose.yml"))
    sync.require(isinstance(compose, dict) and isinstance(compose.get("services"), dict) and compose["services"], "SERVICES_REQUIRED")
    sync.require(len(compose["services"]) <= 100, "SERVICE_LIMIT")
    sync.require(all(re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,63}", s) for s in compose["services"]), "SERVICE_NAME_REFUSED")
    compose = guard.tree(sync.yaml_load(baseline["docker-compose.yml"]), compose, "docker-compose.yml")
    # Compose consumes ${...}; Infisical template markers are for DR config files only.
    text = sync.yaml.safe_dump(compose, sort_keys=False)
    sync.require(not sync.MARKER.search(text), "COMPOSE_LITERAL_PRIVATE_VALUE_USE_INTERPOLATION")
    outputs["docker-compose.yml"] = guard.scan(text.encode(), "docker-compose.yml")
    services = set(compose["services"])
    private_by_public = {}
    for row in compose["services"].values():
        entries = row.get("env_file", [])
        if isinstance(entries, (str, dict)): entries = [entries]
        names = [sync.relative((e if isinstance(e, str) else e.get("path", "")).removeprefix("./")) for e in entries]
        keys = set()
        for name in names:
            if name.startswith(".secrets/") and (source / name).is_file(): keys.update(sync.env_values(read(name)))
        for name in names:
            if not name.startswith(".secrets/"): private_by_public.setdefault(name, set()).update(keys)
    plan = copy.deepcopy(guard.plan)
    old_manifest = sync.loads(baseline["config/dr-manifest.yaml"])
    names, skipped = candidates(source, sync)
    prior = sync.loads(baseline.get(INVENTORY, b'{"schema":1,"files":{},"source_hashes":{}}'))
    sync.require(prior.get("schema") == 1 and isinstance(prior.get("files"), dict)
                 and isinstance(prior.get("source_hashes"), dict), "INVALID_EXPORT_INVENTORY")
    owned, source_hashes = {}, {}
    for name in names:
        if name == "docker-compose.yml": continue
        if name.startswith("data/") and name.split("/")[1] not in services: continue
        data = read(name)
        if re.fullmatch(r"data/[^/]+/\.env(?:\.example)?", name):
            safe = guard.env(data, name, private_by_public.get(name, ()))
        else:
            guard.scan(data, name)
            safe = data
        # Public metadata must not fingerprint raw env files containing private values.
        hashed = sync.digest(safe)
        source_hashes[name] = hashed
        # On the first run, do not replace newer repo tooling/docs with the old live checkout.
        previous = prior["source_hashes"].get(name)
        if name in baseline and previous is None and not name.startswith("data/"):
            continue
        if name in baseline and previous == hashed and not name.startswith("data/"):
            continue
        outputs[name] = safe
        owned[name] = sync.digest(safe)
    owned.update({n: h for n, h in prior["files"].items() if n in source_hashes and n not in owned})
    # Deletion is restricted to files this exporter previously owned and has not been externally edited.
    for name, expected in prior["files"].items():
        sync.require(allowed(name), "UNSAFE_OWNED_PATH")
        if name not in source_hashes and name in baseline:
            sync.require(sync.digest(baseline[name]) == expected, "DELETE_EXTERNALLY_EDITED_FILE_REFUSED", name)
            outputs[name] = None
    env_targets, refs, components = set(), [], {}
    for service, row in compose["services"].items():
        public, private, required = [], [], []
        entries = row.get("env_file", [])
        if isinstance(entries, (str, dict)): entries = [entries]
        for entry in entries:
            name = entry if isinstance(entry, str) else entry.get("path", "")
            name = sync.relative(name.removeprefix("./"))
            optional = isinstance(entry, dict) and entry.get("required") is False
            if optional and not (source / name).exists():
                continue
            if name.startswith(".secrets/"):
                rows = sync.env_values(read(name))
                sync.require(rows or isinstance(entry, dict) and entry.get("required") is False, "PRIVATE_ENV_EMPTY", service)
                private.append(name); env_targets.add(name)
                method = "INFISICAL_AGENT_RENDER"
            else:
                sync.require(allowed(name) and (name in outputs or name in baseline), "PUBLIC_ENV_PATH_REQUIRED", service)
                public.append(name); method = "TRACKED_PUBLIC"
            required.append(name)
            refs.append({"SERVICE": service, "REFERENCE": name, "TYPE": "ENV_FILE", "SOURCE": name,
                         "RESTORE_METHOD": method, "RESOLVED": True})
        for kind, name, target, mode in mounts(row, sync):
            if kind == "bind" and not name.startswith("/"):
                sync.relative(name)
                path = source / name; sync.safe_path(path)
                if path.is_dir():
                    info = path.stat()
                    directories[name] = {"path": name, "uid": getattr(info, "st_uid", 1000), "gid": getattr(info, "st_gid", 1000),
                                         "mode": format(stat.S_IMODE(info.st_mode), "04o")}
                    method = "BOOTSTRAP_GENERATED"
                elif allowed(name) and (name in outputs or name in baseline):
                    method = "TRACKED_PUBLIC"; public.append(name)
                elif name in {f["destination"] for f in plan["files"]}:
                    method = "TRACKED_SANITIZED_TEMPLATE"; private.append(name)
                else:
                    sync.require(name.startswith((".secrets/", ".generated/")), "PRIVATE_BIND_FILE_NEEDS_TEMPLATE", service)
                    method = "INFISICAL_AGENT_RENDER"; private.append(name)
                required.append(name)
                refs.append({"SERVICE": service, "REFERENCE": target, "TYPE": "BIND_MOUNT", "SOURCE": name,
                             "RESTORE_METHOD": method, "MODE": mode, "RESOLVED": True})
            elif kind == "bind":
                refs.append({"SERVICE": service, "REFERENCE": target, "TYPE": "HOST_PREREQUISITE", "SOURCE": name,
                             "RESTORE_METHOD": "HOST_PREREQUISITE", "RESOLVED": True})
        components[service] = copy.deepcopy(old_manifest.get("components", {}).get(service, {}))
        private += old_manifest.get("components", {}).get(service, {}).get("GENERATED_FILES", [])
        components[service].update(PUBLIC_REPO_FILES=sorted(set(public)), GENERATED_FILES=sorted(set(private)),
                                   REQUIRED_BEFORE_COMPOSE_UP=sorted(set(required)))
    old_services = set(old_manifest.get("components", {}))
    removed = old_services - services
    removed_generated = {f for s in removed for f in old_manifest["components"][s].get("GENERATED_FILES", [])}
    active_generated = {f for row in components.values() for f in row.get("GENERATED_FILES", [])}
    plan["env_files"] = {n: b for n, b in plan["env_files"].items() if n not in removed_generated or n in active_generated}
    plan["service_folder_exports"] = {n: b for n, b in plan["service_folder_exports"].items() if n not in removed_generated or n in active_generated}
    plan["postgres_bootstrap"]["connections"] = [r for r in plan["postgres_bootstrap"]["connections"] if r["service"] in services]
    plan["files"] = [f for f in plan["files"] if f["destination"] not in removed_generated or f["destination"] in active_generated]
    obsolete_refs = set()
    for name in set(guard.plan["env_files"]) - set(plan["env_files"]): obsolete_refs.update(declared_refs(guard.plan["env_files"][name], sync))
    for name in set(guard.plan["service_folder_exports"]) - set(plan["service_folder_exports"]):
        obsolete_refs.update(declared_refs(guard.plan["service_folder_exports"][name], sync))
    for item in guard.plan["files"]:
        if item["destination"] in removed_generated and item["destination"] not in active_generated:
            obsolete_refs.update(declared_refs(item, sync))
            if item.get("template"): obsolete_refs.update(sync.MARKER.findall(baseline[item["template"]].decode()))
    old_variables = {m[1] for m in VARIABLE.finditer(baseline["docker-compose.yml"].decode())}
    variables = {m[1] for m in VARIABLE.finditer(text)}
    for key in old_variables - variables:
        if key in plan["root_env"]: obsolete_refs.update(declared_refs(plan["root_env"].pop(key), sync))
    # Existing mixed configs are sanitized with their bindings; ordinary strings and code can evolve.
    overlays = {r["file"]: r for r in sync.loads(baseline["config/private-config-overlays.json"])}
    for item in plan["files"]:
        if not item.get("template"): continue
        name, dest = item["template"], item["destination"]
        if not (source / dest).is_file():
            sync.require(False, "ACTIVE_TEMPLATE_SOURCE_MISSING", name)
        old, raw = baseline[name], read(dest)
        if item["format"] == "json":
            a, b = json_template_trees(item, old, raw, sync)
            omitted = set(overlays.get(dest, {}).get("omit_runtime_identity_fields", []))
            if isinstance(b, dict): b = {k: v for k, v in b.items() if k not in omitted}
            if isinstance(a, list) and isinstance(b, list):
                b = copy.deepcopy(b)
                for row in b:
                    if isinstance(row, dict) and row.get("type") == "hiddendata" and row.get("id") in {"cookieheader", "lasterror"}: row["value"] = ""
            result = guard.tree(a, b, name)
            data = (json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False) + "\n").encode()
        elif item["format"] == "toml":
            data = toml_dump(guard.tree(sync.tomllib.loads(old.decode()), sync.tomllib.loads(raw.decode()), name), sync)
        elif item["format"] == "ini-token":
            data = old  # Private-only Cloudflare token input; public template remains canonical.
        else:
            data = guard.tree(old.decode(), raw.decode(), name).encode()
        sync.require(set(sync.MARKER.findall(old.decode())) <= set(sync.MARKER.findall(data.decode())),
                     "PRIVATE_BINDING_REMOVAL_REVIEW_REQUIRED", name)
        outputs[name] = guard.scan(data, name)
    for item in guard.plan["files"]:
        name = item.get("template")
        if name and name not in {i.get("template") for i in plan["files"]}:
            outputs[name] = None
    active_refs = declared_refs(plan, sync)
    for item in plan["files"]:
        if item.get("template"): active_refs.update(sync.MARKER.findall(outputs[item["template"]].decode()))
    plan["required_values"] = sorted((set(plan["required_values"]) - (obsolete_refs - active_refs)) | active_refs)
    # Removed public env files are no longer needed by any active service.
    for name in baseline:
        if re.fullmatch(r"data/[^/]+/\.env", name) and name.split("/")[1] in removed:
            outputs[name] = None
    image_reader = image_reader or (lambda src, c, old: dynamic_images(src, c, old, sync))
    old_locks = sync.loads(baseline["config/image-lock.json"])
    locks = image_reader(source, compose, old_locks)
    sync.require(set(locks) == services, "IMAGE_INVENTORY_MISMATCH")
    override = sync.loads(baseline["config/compose.dr.yaml"])
    # The one existing non-image override is a reviewed fresh PostgreSQL cluster.
    # Never silently keep it when the live PostgreSQL mounts are changed.
    for service, old_override in override["services"].items():
        if service in services and set(old_override) - {"image", "platform"}:
            old_compose = sync.yaml_load(baseline["docker-compose.yml"])
            sync.require(compose["services"][service].get("volumes") == old_compose["services"][service].get("volumes"),
                         "RECOVERY_VOLUME_OVERRIDE_REVIEW_REQUIRED", service)
    override["services"] = {s: dict(override["services"].get(s, {}), image=locks[s]["image"], platform=locks[s]["platform"]) for s in compose["services"]}
    manifest = copy.deepcopy(old_manifest)
    manifest["components"], manifest["references"] = components, refs
    manifest["state_units"] = [dict(r, services=[s for s in r["services"] if s in services]) for r in manifest["state_units"] if set(r["services"]) & services]
    for old_dir in old_manifest["quick_redeploy"]["empty_directories"]:
        if any(old_dir["path"] == name or old_dir["path"].startswith(name + "/") for name in directories):
            directories.setdefault(old_dir["path"], old_dir)
    manifest["quick_redeploy"]["empty_directories"] = list(directories.values())
    manifest["restore_waves"] = restore_waves(compose, sync)
    manifest["public_sync_note"] = "New private targets require the restored Infisical Agent. Paths are inventoried; Infisical folder mappings are never guessed."
    unknown = sorted(env_targets - set(plan["env_files"]) - set(plan["service_folder_exports"]))
    root_variables = {m[1] for m in VARIABLE.finditer(text)} - set(plan["root_env"])
    layout = {"schema": 1, "services": sorted(services), "empty_directories": list(directories.values()),
              "root_env_variables_from_restored_agent": sorted(root_variables),
              "private_env_targets": sorted(env_targets), "private_env_targets_from_restored_agent": unknown,
              "unclassified_files_excluded": skipped, "database_policy": "fresh_application_state_restore_infisical",
              "scope": "public declarations only; private Agent configuration is preserved by the encrypted DR backup"}
    for name, value in {"config/image-lock.json": locks, "config/compose.dr.yaml": override,
                        "config/dr-render-plan.json": plan, "config/dr-manifest.yaml": manifest, LAYOUT: layout}.items():
        outputs[name] = guard.scan((json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode(), name, assignments=False)
    owned = {n: h for n, h in owned.items() if outputs.get(n, b"") is not None}
    outputs[INVENTORY] = (json.dumps({"schema": 1, "files": owned, "source_hashes": source_hashes}, indent=2, sort_keys=True) + "\n").encode()
    # Recheck the complete candidate against the complete binding corpus.
    # Values learned in later templates must also protect earlier files.
    generated = {INVENTORY, LAYOUT, "config/image-lock.json", "config/compose.dr.yaml",
                 "config/dr-render-plan.json", "config/dr-manifest.yaml"}
    for name, data in outputs.items():
        if data is not None: guard.scan(data, name, assignments=name not in generated)
    sync.require(all(sync.read_file(source, n) == value for n, value in inputs.items()), "SOURCE_CHANGED_DURING_EXPORT")
    sync.require(candidates(source, sync)[0] == names, "PUBLIC_FILE_LIST_CHANGED_DURING_EXPORT")
    sync.require(image_reader(source, compose, old_locks) == locks, "IMAGES_CHANGED_DURING_EXPORT")
    return {n: data for n, data in outputs.items() if data != baseline.get(n)}
