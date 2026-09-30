#!/usr/bin/env python3
"""Read only: inspect applied Agent mappings; never read auth credential files."""
import argparse,json,re,sys
from pathlib import Path
import yaml
import dr

# Semantic subset of Go templates. Never evaluates templates or fetches secrets.
_KEY = '\x01KEY\x02'
_VALUE = '\x01VALUE\x02'
_NAME = re.compile(r'[A-Z][A-Z0-9_]*\Z')
_PATH = re.compile(r'/(?:[a-z0-9_-]+/)*[a-z0-9_-]+\Z')


def _arguments(expression):
    """Preserve quoted strings as a distinct token type; reject other Go syntax."""
    result = []
    decoder = json.JSONDecoder()
    while expression:
        expression = expression.lstrip()
        if not expression:
            break
        if expression.startswith('"'):
            try:
                value, end = decoder.raw_decode(expression)
            except ValueError:
                raise dr.Refused('UNSUPPORTED_AGENT_STRING')
            dr.require(isinstance(value, str), 'UNSUPPORTED_AGENT_STRING')
            result.append(('string', value))
        else:
            match = re.match(r'[^\s"]+', expression)
            dr.require(match is not None, 'UNSUPPORTED_AGENT_TOKEN')
            end = match.end()
            result.append(('symbol', match[0]))
        dr.require(end == len(expression) or expression[end].isspace(),
                   'UNSUPPORTED_AGENT_TOKEN_BOUNDARY')
        expression = expression[end:]
    return result


def _tokens(content):
    dr.require(isinstance(content, str) and len(content) <= 1000000,
               'INVALID_AGENT_TEMPLATE')
    dr.require(all(ord(c) >= 32 or c in '\t\r\n' for c in content),
               'UNSUPPORTED_AGENT_CONTROL_CHARACTER')
    tokens = []
    position = 0
    trim_next = False
    for match in re.finditer(r'\{\{.*?\}\}', content, re.S):
        literal = content[position:match.start()]
        dr.require('{{' not in literal and '}}' not in literal,
                   'UNBALANCED_AGENT_DELIMITER')
        raw = match[0][2:-2]
        if trim_next:
            literal = literal.lstrip(' \t\r\n')
        if raw.startswith('-'):
            dr.require(len(raw) > 1 and raw[1].isspace(), 'INVALID_TRIM_MARKER')
            literal = literal.rstrip(' \t\r\n')
            raw = raw[1:]
        trim_next = raw.endswith('-')
        if trim_next:
            dr.require(len(raw) > 1 and raw[-2].isspace(), 'INVALID_TRIM_MARKER')
            raw = raw[:-1]
        if literal:
            tokens.append(('text', literal))
        expression = raw.strip()
        if expression.startswith('/*'):
            dr.require(expression.endswith('*/') and '*/' not in expression[2:-2],
                       'UNSUPPORTED_AGENT_COMMENT')
        else:
            tokens.append(('action', _arguments(expression)))
        position = match.end()
    literal = content[position:]
    dr.require('{{' not in literal and '}}' not in literal,
               'UNBALANCED_AGENT_DELIMITER')
    if trim_next:
        literal = literal.lstrip(' \t\r\n')
    if literal:
        tokens.append(('text', literal))
    return tokens


class _Parser:
    def __init__(self, content):
        self.tokens = _tokens(content)
        self.position = 0
        self.secret_scope = None

    def block(self):
        nodes = []
        while self.position < len(self.tokens):
            kind, args = self.tokens[self.position]
            if kind == 'action' and args and args[0] in (
                    ('symbol', 'end'), ('symbol', 'else')):
                break
            self.position += 1
            if kind == 'text':
                if nodes and nodes[-1][0] == 'text':
                    nodes[-1] = ('text', nodes[-1][1] + args)
                else:
                    nodes.append(('text', args))
                continue
            if (len(args) == 5 and args[0] in (
                    ('symbol', 'range'), ('symbol', 'with'))
                    and args[1] == ('symbol', 'secret')
                    and all(t == 'string' for t, _ in args[2:])):
                project, environment, path = (v for _, v in args[2:])
                dr.require(re.fullmatch(r'[A-Za-z0-9_-]+', project)
                           and re.fullmatch(r'[A-Za-z0-9_-]+', environment)
                           and _PATH.fullmatch(path),
                           'UNSUPPORTED_SECRET_PATH')
                scope = (project, environment)
                dr.require(self.secret_scope in (None, scope),
                           'AMBIGUOUS_SECRET_PROJECT_OR_ENVIRONMENT')
                self.secret_scope = scope
                body = self.block()
                self.end()
                nodes.append((args[0][1], path, body))
            elif args == [('symbol', 'range'), ('symbol', '.')]:
                body = self.block()
                self.end()
                nodes.append(('range', None, body))
            elif args and args[0] == ('symbol', 'if'):
                branches = []
                condition = self.condition(args[1:])
                while True:
                    dr.require(condition not in [key for key, _ in branches],
                               'DUPLICATE_AGENT_CONDITION')
                    branches.append((condition, self.block()))
                    dr.require(self.position < len(self.tokens),
                               'UNBALANCED_AGENT_TEMPLATE')
                    kind, tail = self.tokens[self.position]
                    if tail[:2] == [('symbol', 'else'), ('symbol', 'if')]:
                        self.position += 1
                        condition = self.condition(tail[2:])
                        continue
                    otherwise = []
                    if tail == [('symbol', 'else')]:
                        self.position += 1
                        otherwise = self.block()
                    self.end()
                    nodes.append(('if', branches, otherwise))
                    break
            else:
                nodes.append(('emit', self.emission(args)))
        return nodes

    def end(self):
        dr.require(self.position < len(self.tokens) and self.tokens[self.position]
                   == ('action', [('symbol', 'end')]), 'UNBALANCED_AGENT_TEMPLATE')
        self.position += 1

    @staticmethod
    def condition(args):
        dr.require(len(args) == 3 and args[:2] == [
            ('symbol', 'eq'), ('symbol', '.Key')] and args[2][0] == 'string'
            and _NAME.fullmatch(args[2][1]), 'UNSUPPORTED_AGENT_CONDITION')
        return args[2][1]

    @staticmethod
    def emission(args):
        if args == [('symbol', '.Key')]:
            return _KEY
        if args == [('symbol', '.Value')]:
            return _VALUE
        if len(args) >= 2 and args[0] == ('symbol', 'printf') and args[1][0] == 'string':
            fmt = args[1][1]
            if args[2:] == [('symbol', '.Value')]:
                if fmt == '%q':
                    return '"' + _VALUE + '"'
                if fmt == '%s':
                    return _VALUE
            if args[2:] == [('symbol', '.Key'), ('symbol', '.Value')]:
                forms = {'%s=%s': _KEY + '=' + _VALUE,
                         "%s='%s'": _KEY + "='" + _VALUE + "'"}
                for form, output in forms.items():
                    if fmt == form:
                        return output
                    if fmt == form + '\n':
                        return output + '\n'
        raise dr.Refused('UNSUPPORTED_AGENT_EXPRESSION')


def _blank(text, complete_comments=False):
    # Literal comments are harmless. Secret emissions or assignments in ignored
    # text are NOT silently accepted, including inside comments.
    dr.require(all(not line.strip() or line.lstrip().startswith('#')
                   for line in text.splitlines()), 'UNSUPPORTED_AGENT_LITERAL')
    dr.require(_KEY not in text and _VALUE not in text, 'SECRET_EMISSION_IN_COMMENT')
    if complete_comments:
        dr.require(not text.strip() or text.endswith(('\n', '\r')),
                   'UNTERMINATED_OUTER_COMMENT')


def _branches(nodes):
    keys = set()
    for node in nodes:
        dr.require(node[0] in ('text', 'emit', 'if'), 'NESTED_SECRET_SCOPE_UNSUPPORTED')
        if node[0] == 'if':
            for key, body in node[1]:
                keys.add(key)
                keys.update(_branches(body))
            keys.update(_branches(node[2]))
    return keys


def _render(nodes, key):
    output = []
    for node in nodes:
        if node[0] in ('text', 'emit'):
            output.append(node[1])
        elif node[0] == 'if':
            body = next((body for name, body in node[1] if key == name), node[2])
            output.append(_render(body, key))
        else:
            raise dr.Refused('UNSUPPORTED_AGENT_SCOPE')
    return ''.join(output)


def _assignments(text):
    result = []
    value = re.escape(_VALUE)
    pattern = re.compile('(' + re.escape(_KEY) + r'|[A-Z][A-Z0-9_]*)=(?:'
                         + value + "|'" + value + "'|\"" + value + '\")')
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            _blank(line)
            continue
        match = pattern.fullmatch(line)
        dr.require(match is not None, 'AMBIGUOUS_AGENT_ASSIGNMENT')
        result.append(match[1])
    return result


def extract(content):
    parser = _Parser(content)
    tree = parser.block()
    dr.require(parser.position == len(parser.tokens), 'UNBALANCED_AGENT_TEMPLATE')
    folders = []
    overrides = {}

    def scope(nodes, collection=None):
        for node in nodes:
            kind = node[0]
            if kind == 'text':
                _blank(node[1], complete_comments=True)
            elif kind == 'with':
                dr.require(collection is None, 'NESTED_SECRET_SCOPE_UNSUPPORTED')
                scope(node[2], node[1])
            elif kind == 'range':
                path = node[1] or collection
                dr.require(path is not None and not (node[1] and collection),
                           'AMBIGUOUS_AGENT_RANGE')
                body = node[2]
                keys = _branches(body)
                # None represents EVERY key not named in a condition. All named
                # cases plus this residual class exhaust the supported language.
                cases = {key: _assignments(_render(body, key)) for key in [None, *sorted(keys)]}
                if all(output == [_KEY] or (key is not None and output == [key])
                       for key, output in cases.items()):
                    dr.require(path not in folders, 'DUPLICATE_FOLDER_EXPORT')
                    folders.append(path)
                else:
                    dr.require(not cases[None], 'UNCONDITIONAL_OVERRIDE_AMBIGUOUS')
                    dr.require(not any(_KEY in output for output in cases.values()),
                               'FILTERED_GENERIC_EXPORT_UNSUPPORTED')
                    dr.require(any(cases.values()), 'EMPTY_SECRET_EXPORT_UNSUPPORTED')
                    for source, destinations in cases.items():
                        for destination in destinations:
                            dr.require(destination not in overrides, 'DUPLICATE_OVERRIDE')
                            overrides[destination] = {'path': path, 'key': source}
            else:
                raise dr.Refused('EMISSION_OUTSIDE_SECRET_RANGE')

    scope(tree)
    return {'folders': sorted(folders), 'overrides': overrides}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--target',required=True);parser.add_argument('--agent-config',default='/etc/infisical/agent-config.yaml');args=parser.parse_args()
    root=Path(args.target).resolve();plan=dr.load(root/'config/dr-render-plan.json');cfg=yaml.safe_load(Path(args.agent_config).read_text());results={};passed=True
    for service in ('aiometadata','aiostreams'):
        destination=str(root/'.secrets'/f'{service}.env');items=[t for t in cfg.get('templates',[]) if t.get('destination-path')==destination]
        try:
            dr.require(len(items)==1,'UNIQUE_APPLIED_TEMPLATE_REQUIRED');content=items[0].get('template-content')
            dr.require(isinstance(content,str),'INLINE_TEMPLATE_REQUIRED');actual=extract(content)
            expected=plan['service_folder_exports'][f'.secrets/{service}.env'];ok=actual['folders']==[expected['path']] and actual['overrides']==expected['overrides']
            results[service]={'status':'PASS' if ok else 'DIFFERENT','mapping':actual};passed &= ok
            print(service,'AGENT_MAPPING',results[service]['status'])
            for folder in actual['folders']:print(service,folder,'->',f'.secrets/{service}.env')
            for key in (expected['overrides'] if ok else sorted(actual['overrides'])):
                row=actual['overrides'][key];print(service,row['path']+'/'+row['key'],'->',key)
        except Exception as exc:
            passed=False;results[service]={'status':'BLOCKED','reason':str(exc) if isinstance(exc,dr.Refused) else type(exc).__name__};print(service,'AGENT_MAPPING BLOCKED')
    print('TOTAL','PASS' if passed else 'BLOCKED');return 0 if passed else 1
if __name__=='__main__':
    try:sys.exit(main())
    except PermissionError:print('AGENT_MAPPING EXECUTION_REQUIRES_ROOT');sys.exit(1)
    except Exception:print('AGENT_MAPPING BLOCKED');sys.exit(1)
