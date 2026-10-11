"""Deterministic transformation of the audited Agent template subset.

No template evaluation, file access or network calls. The caller classifies and
approves local_values first, and supplies the reviewed mapping parser for AIO.
"""
from dataclasses import dataclass
import json
import re


class TransformRefused(Exception):
    pass


def _need(ok):
    if not ok:
        raise TransformRefused("LOCAL_TEMPLATE_TRANSFORM_REFUSED")


def _size(value):
    try:
        return len(value.encode("utf-8"))
    except UnicodeError:
        raise TransformRefused("LOCAL_TEMPLATE_TRANSFORM_REFUSED") from None


@dataclass(frozen=True)
class Action:
    start: int
    end: int
    expression: str
    left: bool
    right: bool


def _scan(content):
    actions = []
    position = 0
    while True:
        start = content.find("{{", position)
        if start < 0:
            break
        cursor, quoted, escaped = start + 2, False, False
        while cursor < len(content):
            char = content[cursor]
            if quoted:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    quoted = False
            elif char == '"':
                quoted = True
            elif content.startswith("}}", cursor):
                break
            else:
                _need(not content.startswith("{{", cursor))
            cursor += 1
        _need(cursor < len(content) and not quoted)
        raw = content[start + 2:cursor]
        left, right = raw.startswith("-"), raw.endswith("-")
        if left:
            _need(len(raw) > 1 and raw[1].isspace())
            raw = raw[1:]
        if right:
            _need(len(raw) > 1 and raw[-2].isspace())
            raw = raw[:-1]
        actions.append(Action(start, cursor + 2, raw.strip(), left, right))
        position = cursor + 2
    return actions


def _args(expression):
    result, decoder = [], json.JSONDecoder()
    while expression.strip():
        expression = expression.lstrip()
        if expression.startswith('"'):
            try:
                value, end = decoder.raw_decode(expression)
            except (ValueError, RecursionError):
                raise TransformRefused("LOCAL_TEMPLATE_TRANSFORM_REFUSED") from None
            _need(isinstance(value, str))
            result.append(("string", value))
        else:
            match = re.match(r'[^\s"]+', expression)
            _need(match is not None)
            end = match.end()
            result.append(("symbol", match[0]))
        _need(end == len(expression) or expression[end].isspace())
        expression = expression[end:]
    return result


def _tag(expression, *, left=False, right=False):
    return "{{" + ("-" if left else "") + " " + expression + " " + ("-" if right else "") + "}}"


def _print(value, *, left=False, right=False, fmt="%s"):
    # JSON quoted UTF-8 strings are valid Go interpreted string literals.
    # ASCII escaping would generate invalid Go surrogate literals for emoji.
    return _tag("printf " + json.dumps(fmt) + " " + json.dumps(value, ensure_ascii=False),
                left=left, right=right)


def _closing(actions, start):
    depth = 0
    for index in range(start, len(actions)):
        name = actions[index].expression.split(None, 1)[0]
        if name in ("with", "range", "if"):
            depth += 1
        elif name == "end":
            depth -= 1
            if depth == 0:
                return index
        _need(depth >= 0)
    raise TransformRefused("LOCAL_TEMPLATE_TRANSFORM_REFUSED")


def _signature(action):
    if not re.match(r"^(?:with|range)\s+(?:getSecretByName|secret|listSecrets)\s", action.expression):
        return None
    args = _args(action.expression)
    _need(args[0][0] == args[1][0] == "symbol" and all(k == "string" for k, _ in args[2:]))
    operation, function = args[0][1], args[1][1]
    if function == "getSecretByName":
        _need(operation == "with" and len(args) == 6)
    else:
        _need(len(args) == 5)
    project, environment, folder = (v for _, v in args[2:5])
    _need(re.fullmatch(r"[A-Za-z0-9_-]+", project) and re.fullmatch(r"[A-Za-z0-9_-]+", environment)
          and re.fullmatch(r"/(?:[a-z0-9_-]+/)*[a-z0-9_-]+", folder))
    key = args[5][1] if function == "getSecretByName" else None
    if key is not None:
        _need(re.fullmatch(r"[A-Z][A-Z0-9_]*", key))
    return operation, function, folder, key


def _simple_body(content, actions, start, end, value):
    result = []
    for index in range(start + 1, end + 1):
        previous, current = actions[index - 1], actions[index]
        text = content[previous.end:current.start]
        if previous.right:
            text = text.lstrip(" \t\r\n")
        if current.left:
            text = text.rstrip(" \t\r\n")
        result.append(text)
        if index != end:
            _need(current.expression == ".Value")
            result.append(value)
    return "".join(result)


def _selected(nodes, key):
    for node in nodes:
        if node[0] in ("text", "emit"):
            yield node
        elif node[0] == "if":
            body = next((body for name, body in node[1] if name == key), node[2])
            yield from _selected(body, key)
        else:
            raise TransformRefused("LOCAL_TEMPLATE_TRANSFORM_REFUSED")


def _local_body(nodes, key, value, semantic):
    result = []
    for kind, text in _selected(nodes, key):
        if kind == "emit" and text == '"' + semantic._VALUE + '"':
            result.append(_print(value, fmt="%q"))
        else:
            rendered = text.replace(semantic._KEY, key).replace(semantic._VALUE, value)
            if rendered:
                result.append(_print(rendered))
    return "".join(result)


def _range_transform(content, actions, start, end, signature, values, semantic):
    _need(semantic is not None)
    operation, function, folder, _ = signature
    block = content[actions[start].start:actions[end].end]
    # The approved parser's collection grammar uses the legacy function alias.
    if function == "listSecrets":
        header = actions[start].expression.replace("listSecrets", "secret", 1)
        block = _tag(header, left=actions[start].left, right=actions[start].right) + content[
            actions[start].end:actions[end].end]
    parser = semantic._Parser(block)
    tree = parser.block()
    _need(parser.position == len(parser.tokens) and len(tree) == 1 and tree[0][0] == operation)
    root = tree[0]
    if operation == "with":
        ranges = [node for node in root[2] if node[0] == "range"]
        _need(len(ranges) == 1 and ranges[0][1] is None
              and all(node[0] in ("range", "text") for node in root[2]))
        body = ranges[0][2]
        candidates = [index for index in range(start + 1, end)
                      if actions[index].expression == "range ."]
        _need(len(candidates) == 1)
        loop_start = candidates[0]
        loop_end = _closing(actions, loop_start)
    else:
        body, loop_start, loop_end = root[2], start, end
    semantic._branches(body)  # Reject nested or unsupported scope grammar.
    selected = {}
    for ref, value in values.items():
        path, key = ref.rsplit("/", 1)
        if path == folder and semantic._assignments(semantic._render(body, key)):
            selected[key] = value
    if not selected:
        return content[actions[start].start:actions[end].end]
    keys = sorted(selected)
    guard = _tag("if eq .Key " + json.dumps(keys[0]))
    for key in keys[1:]:
        guard += _tag("else if eq .Key " + json.dumps(key))
    guard += _tag("else", right=actions[loop_start].right)
    close = _tag("end", left=actions[loop_end].left)
    begin, finish = actions[start].start, actions[end].end
    transformed = (content[begin:actions[loop_start].end] + guard
                   + content[actions[loop_start].end:actions[loop_end].start] + close
                   + content[actions[loop_end].start:finish])
    appended = "".join(_local_body(body, key, selected[key], semantic) for key in keys)
    if actions[end].right and appended:
        last = _scan(appended)[-1]
        appended = appended[:last.start] + _tag(last.expression, right=True)
    return transformed + appended


def transform_template(content, local_values, semantic=None):
    """Replace only approved matching sources; keep credential sources intact.

    semantic is the explicitly supplied, reviewed verify-agent-dr-mappings
    module. It is required only when a matching AIO collection is transformed.
    """
    _need(isinstance(content, str) and _size(content) <= 1024 * 1024
          and isinstance(local_values, dict) and len(local_values) <= 512)
    for ref, value in local_values.items():
        _need(isinstance(ref, str) and len(ref) <= 512
              and re.fullmatch(r"/(?:[a-z0-9_-]+/)+[A-Z][A-Z0-9_]*", ref)
              and isinstance(value, str) and "\0" not in value and _size(value) <= 65536)
    if not local_values:
        return content
    try:
        actions = _scan(content)
        replacements = []
        for index, action in enumerate(actions):
            signature = _signature(action)
            if signature is None:
                continue
            operation, function, folder, key = signature
            if function == "getSecretByName":
                ref = folder + "/" + key
                if ref not in local_values:
                    continue
                end = _closing(actions, index)
                body = _simple_body(content, actions, index, end, local_values[ref])
                replacement = _print(body, left=action.left, right=actions[end].right)
            else:
                if not any(ref.rsplit("/", 1)[0] == folder for ref in local_values):
                    continue
                end = _closing(actions, index)
                replacement = _range_transform(content, actions, index, end, signature,
                                               local_values, semantic)
            replacements.append((action.start, actions[end].end, replacement))
        previous_end = 0
        parts = []
        for start, end, replacement in replacements:
            _need(start >= previous_end)
            parts.extend((content[previous_end:start], replacement))
            previous_end = end
        parts.append(content[previous_end:])
        result = "".join(parts)
        _need(_size(result) <= 1024 * 1024)
        return result
    except TransformRefused:
        raise
    except Exception:
        raise TransformRefused("LOCAL_TEMPLATE_TRANSFORM_REFUSED") from None
