#!/usr/bin/env python3
"""Read only: inspect applied Agent mappings; never read auth credential files."""
import argparse,json,re,shlex,sys
from pathlib import Path
import yaml
import dr

def extract(content):
    stack=[];folders=[];overrides={};previous='';dynamic_lhs=False
    for token in re.split(r'(\{\{.*?\}\})',content,flags=re.S):
        if not token.startswith('{{'):
            previous=token
            if token.strip():dynamic_lhs=token.strip()=='=' and dynamic_lhs
            continue
        expr=token[2:-2].strip().strip('-').strip()
        if expr.startswith('range secret '):
            parts=shlex.split(expr);dr.require(len(parts)==5,'UNSUPPORTED_AGENT_RANGE')
            folder=parts[4];dr.require(re.fullmatch(r'/[a-z0-9/-]+',folder),'UNSUPPORTED_FOLDER')
            stack.append(('folder',folder))
        elif expr.startswith('if eq .Key '):
            parts=shlex.split(expr);dr.require(len(parts)==4 and re.fullmatch(r'[A-Z][A-Z0-9_]*',parts[3]),'UNSUPPORTED_AGENT_CONDITION');stack.append(('key',parts[3]))
        elif expr=='end':
            dr.require(stack,'UNBALANCED_AGENT_TEMPLATE');stack.pop()
        elif expr=='.Key':dynamic_lhs=True
        elif expr in ('.Value','printf "%q" .Value'):
            folder=next((v for k,v in reversed(stack) if k=='folder'),None);key=next((v for k,v in reversed(stack) if k=='key'),None)
            dr.require(folder is not None,'VALUE_WITHOUT_FOLDER')
            if dynamic_lhs and previous.strip()=='=':
                dr.require(key is None,'UNSUPPORTED_FILTERED_FOLDER');folders.append(folder)
            else:
                match=re.search(r'(?:^|\n)([A-Z][A-Z0-9_]*)=\s*$',previous);dr.require(match is not None and key is not None,'UNSUPPORTED_EXPLICIT_ASSIGNMENT')
                name=match[1];dr.require(name not in overrides,'DUPLICATE_OVERRIDE');overrides[name]={'path':folder,'key':key}
            dynamic_lhs=False
        elif not expr.startswith('/*'):raise dr.Refused('UNSUPPORTED_AGENT_EXPRESSION')
    dr.require(not stack,'UNBALANCED_AGENT_TEMPLATE');return {'folders':sorted(set(folders)),'overrides':overrides}
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
            for key,row in sorted(actual['overrides'].items()):print(service,row['path']+'/'+row['key'],'->',key)
        except Exception as exc:
            passed=False;results[service]={'status':'BLOCKED','reason':str(exc) if isinstance(exc,dr.Refused) else type(exc).__name__};print(service,'AGENT_MAPPING BLOCKED')
    print('TOTAL','PASS' if passed else 'BLOCKED');return 0 if passed else 1
if __name__=='__main__':
    try:sys.exit(main())
    except PermissionError:print('AGENT_MAPPING EXECUTION_REQUIRES_ROOT');sys.exit(1)
    except Exception:print('AGENT_MAPPING BLOCKED');sys.exit(1)
