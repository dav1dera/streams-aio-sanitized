#!/usr/bin/env python3
"""Versioned logical DR import into NEW SQLite files, never an existing DB.

Consumes operator-supplied PRIVATE_DR_PAYLOAD and Infisical snapshots. Does not
export/decrypt source databases, contact an application API or start containers.
Storage compatibility: AIOStreams 2.35.3; AIOMetadata 3.3.0 (see contract).
"""
import argparse,base64,copy,hashlib,json,os,re,sqlite3,subprocess,sys,tempfile,uuid,zlib
from pathlib import Path
import bcrypt
from cryptography.hazmat.primitives.ciphers import Cipher,algorithms,modes
from cryptography.hazmat.primitives.padding import PKCS7
import dr

SERVICES={'aiostreams':'2.35.3','aiometadata':'3.3.0'}
DB_PATHS={s:f'data/{s}/data/db.sqlite' for s in SERVICES}

def packed(obj):return json.dumps(obj,ensure_ascii=False,separators=(',',':'))
def private_json(path):
 p=Path(path);dr.require(p.is_file() and not p.is_symlink(),'PRIVATE_PAYLOAD_FILE_REQUIRED')
 dr.require(not p.stat().st_mode&0o077,'PRIVATE_PAYLOAD_REQUIRES_0600')
 return dr.load(p)
def public_schema(root,name,obj):
 import jsonschema
 try:jsonschema.Draft202012Validator(dr.load(root/'config'/name)).validate(obj)
 except jsonschema.ValidationError:raise dr.Refused('PRIVATE_INPUT_SCHEMA_MISMATCH')
def resolve(value,values):
 if isinstance(value,dict) and set(value)=={'ref'}:
  dr.require(value['ref'] in values,'INFISICAL_BINDING_MISSING');return values[value['ref']]
 if isinstance(value,dict):return {k:resolve(v,values) for k,v in value.items()}
 if isinstance(value,list):return [resolve(v,values) for v in value]
 return value

def pointer_parent(obj,pointer):
 dr.require(isinstance(pointer,str) and pointer.startswith('/') and pointer!='/' and '~' not in pointer.replace('~0','').replace('~1',''),'INVALID_JSON_POINTER')
 parts=[p.replace('~1','/').replace('~0','~') for p in pointer[1:].split('/')];parent=obj
 for key in parts[:-1]:
  if isinstance(parent,list):dr.require(key.isdigit() and int(key)<len(parent),'POINTER_MISSING');parent=parent[int(key)]
  else:dr.require(isinstance(parent,dict) and key in parent,'POINTER_MISSING');parent=parent[key]
 return parent,parts[-1]
def put(obj,pointer,value,remove=False):
 parent,key=pointer_parent(obj,pointer)
 if isinstance(parent,list):
  dr.require(key.isdigit() and int(key)<len(parent),'POINTER_MISSING')
  if remove:raise dr.Refused('ARRAY_REMOVAL_REQUIRES_EXPLICIT_PAYLOAD_EDIT')
  parent[int(key)]=value
 else:
  dr.require(isinstance(parent,dict),'POINTER_NOT_OBJECT')
  if remove:dr.require(key in parent,'REAUTH_POINTER_MISSING');del parent[key]
  else:parent[key]=value

def encrypt(text,key):
 # Same zlib -> AES-256-CBC -> URL-safe JSON envelope as pinned AIOStreams.
 iv=os.urandom(16);pad=PKCS7(128).padder();raw=zlib.compress(text.encode(),9);padded=pad.update(raw)+pad.finalize()
 enc=Cipher(algorithms.AES(key),modes.CBC(iv)).encryptor();cipher=enc.update(padded)+enc.finalize()
 b64=lambda v:base64.b64encode(v).decode()
 return base64.urlsafe_b64encode(packed({'i':b64(iv),'e':b64(cipher),'t':'a'}).encode()).decode().rstrip('=')
def password_hash(password,rounds):
 dr.require(isinstance(password,str) and 1<=len(password) and len(password.encode())<=72,'CONFIG_PASSWORD_LENGTH_UNSUPPORTED')
 return bcrypt.hashpw(password.encode(),bcrypt.gensalt(rounds)).decode()
def streams_row(config,identity,secret):
 cfg=copy.deepcopy(config)
 for field in ('uuid','trusted','ip','activeVariants','autoVariants','healthResults','variantSelectorLocation'):cfg.pop(field,None)
 jf=cfg.get('jellyfin') or {};personas=[jf.get('primary')]+(jf.get('personas') or [])
 for persona in personas:
  if isinstance(persona,dict) and persona.get('lock') and not re.fullmatch(r'\$2[aby]\$\d{2}\$[./A-Za-z0-9]{53}',persona['lock']):
   dr.require(len(persona['lock'].encode())<=72,'PERSONA_PIN_TOO_LONG')
   persona['lock']=bcrypt.hashpw(persona['lock'].encode(),bcrypt.gensalt(10)).decode()
 password=identity['password'];salt=bcrypt.gensalt(10).decode()
 # Pinned upstream passes its bcrypt salt to Buffer.from(salt, 'hex'). The
 # leading '$' yields an EMPTY byte buffer in Node. Preserve this exact codec;
 # a future upstream KDF change requires an explicitly tested version update.
 key=hashlib.pbkdf2_hmac('sha512',(password+':'+secret).encode(),b'',100000,32)
 return (identity['uuid'],password_hash(password,10),encrypt(packed(cfg),key),salt)

def inspect_unmounted(root):
 result=subprocess.run(['docker','ps','-aq'],capture_output=True,text=True)
 dr.require(result.returncode==0,'DOCKER_MOUNT_GUARD_UNAVAILABLE')
 ids=result.stdout.split()
 if not ids:return
 result=subprocess.run(['docker','inspect',*ids],capture_output=True,text=True)
 dr.require(result.returncode==0,'DOCKER_MOUNT_GUARD_UNAVAILABLE')
 for container in json.loads(result.stdout):
  work=container.get('Config',{}).get('Labels',{}).get('com.docker.compose.project.working_dir')
  dr.require(not work or Path(work).resolve()!=root,'MOUNTED_DEPLOYMENT_REFUSED')
  for mount in container.get('Mounts',[]):
   source=mount.get('Source')
   if source and mount.get('Type')=='bind':
    source=Path(source).resolve()
    dr.require(not(source==root or source.is_relative_to(root) or root.is_relative_to(source)),'MOUNTED_DEPLOYMENT_REFUSED')

def prepare(root,plan,payload,values):
 public_schema(root,'aio-payload.schema.json',payload)
 dr.require(payload['schema']==1,'PAYLOAD_VERSION_UNSUPPORTED')
 registry=json.loads(values['/dr/AIO_PROFILE_BINDINGS']);public_schema(root,'aio-bindings.schema.json',registry)
 registry=resolve(registry,values);entries=[];seen=set();reauth=[]
 for service in SERVICES:
  dr.require(set(registry[service])=={e['profile'] for e in payload[service]},'PAYLOAD_REGISTRY_PROFILE_SET_MISMATCH')
  for entry in payload[service]:
   dr.require(entry['version']==SERVICES[service],'EXPORT_VERSION_UNSUPPORTED')
   name=entry['profile'];identity=registry[service][name];uid=identity['uuid']
   dr.require(str(uuid.UUID(uid))==uid.lower() and uid not in seen,'DUPLICATE_OR_INVALID_CONFIG_UUID');seen.add(uid)
   password_hash(identity['password'],4)  # Validate before touching any DB.
   export=entry['export'];cfg=copy.deepcopy(export if service=='aiostreams' else export['config'])
   if service=='aiometadata':dr.require(export['version']==SERVICES[service],'EXPORT_VERSION_UNSUPPORTED')
   dr.require(isinstance(cfg,dict) and cfg,'CONFIG_OBJECT_REQUIRED')
   dr.require('REPLACE_WITH_OFFICIAL_PRIVATE_EXPORT' not in cfg and 'REPLACE_WITH_OFFICIAL_PRIVATE_CONFIG' not in cfg,'EXAMPLE_IS_NOT_A_PRIVATE_PAYLOAD')
   if cfg.get('uuid'):dr.require(cfg['uuid']==uid,'EXPORTED_UUID_CONFLICT')
   for binding in entry['bindings']:
    dr.require(binding['ref'] in values,'INFISICAL_BINDING_MISSING');value=values[binding['ref']]
    if binding.get('encoding','string')=='json':value=json.loads(value)
    put(cfg,binding['pointer'],value)
   for dependency in entry['dependencies']:
    target=registry[dependency['service']][dependency['profile']]
    put(cfg,dependency['pointer'],target[dependency['field']])
   for dependency in entry['reauthorize']:
    for pointer in dependency['remove_pointers']:put(cfg,pointer,None,remove=True)
    reauth.append({'service':service,'profile':name,'provider':dependency['provider']})
   if service=='aiometadata':
    api=cfg.get('apiKeys') or {}
    dr.require(not any(v for k,v in api.items() if k.endswith('TokenId')),'OAUTH_REFERENCE_REQUIRES_EXPLICIT_REAUTHORIZATION')
    for key in ('managerAccounts','jellyfinUsers'):
     def dead_reference(value):
      if isinstance(value,dict):return any((k=='keyId' and bool(v)) or dead_reference(v) for k,v in value.items())
      if isinstance(value,list):return any(dead_reference(v) for v in value)
      return False
     dr.require(not dead_reference(cfg.get(key)),'LINKED_KEY_REFERENCE_REQUIRES_EXPLICIT_REAUTHORIZATION')
    for manager in (cfg.get('managers') or {}).values():
     dr.require(not manager or not manager.get('instanceUrl') or bool(manager.get('apiKey')),'MANAGER_KEY_BINDING_REQUIRED')
   else:
    dr.require(entry['linked_accounts']=='reauthorize','LINKED_ACCOUNT_POLICY_REQUIRED')
   entries.append((service,name,identity,cfg))
 ids={identity['uuid']:identity for _,_,identity,_ in entries}
 for service,name,identity,cfg in entries:
  if service=='aiostreams' and cfg.get('parentConfig',{}).get('uuid'):
   parent=cfg['parentConfig'];dr.require(parent['uuid'] in ids and parent.get('password')==ids[parent['uuid']]['password'],'PARENT_PROFILE_DEPENDENCY_NOT_RECONCILED')
 # IDs are kept, never regenerated: external addon links, templates, trust and
 # encrypted-password links remain valid with the same SECRET_KEY/password.
 secret=values['/aiostreams/SECRET_KEY'];dr.require(re.fullmatch(r'[a-fA-F0-9]{64}',secret),'AIOSTREAMS_SECRET_KEY_FORMAT')
 return entries,reauth,secret

def build_databases(root,entries,secret,paths):
 for service,path in paths.items():
  db=sqlite3.connect(path);db.execute('PRAGMA foreign_keys=ON')
  schema=(root/'config/dr-templates/aio'/f'{service}-bootstrap.sql').read_text();db.executescript(schema)
  with db:
   for svc,name,identity,cfg in entries:
    if svc!=service:continue
    uid=identity['uuid']
    if service=='aiostreams':
     db.execute('INSERT INTO users(uuid,password_hash,config,config_salt) VALUES(?,?,?,?)',streams_row(cfg,identity,secret))
     if identity.get('profile'):
      p=identity['profile'];db.execute('INSERT INTO config_profiles(id,owner,uuid,encrypted_password,label,alias) VALUES(?,?,?,?,?,?)',(p['id'],p['owner'],uid,encrypt(identity['password'],bytes.fromhex(secret)),p['label'],p.get('alias')))
    else:
     cfg=copy.deepcopy(cfg);cfg.pop('configHash',None)
     cfg['configHash']=hashlib.md5(packed(cfg).encode()).hexdigest()[:16]
     db.execute('INSERT INTO user_configs(user_uuid,password_hash,config_data) VALUES(?,?,?)',(uid,password_hash(identity['password'],12),packed(cfg)))
     if identity.get('trusted',False):db.execute('INSERT INTO trusted_uuids(user_uuid) VALUES(?)',(uid,))
     if identity.get('alias'):db.execute('INSERT INTO user_aliases(alias_lower,alias,user_uuid) VALUES(?,?,?)',(identity['alias'].lower(),identity['alias'],uid))
  dr.require(db.execute('PRAGMA integrity_check').fetchone()[0]=='ok','IMPORTED_SQLITE_INTEGRITY_FAILED');db.close();path.chmod(0o600)

def run(args):
 root,plan=dr.context(args);dr.require((root/'.generated/dr-render-complete.json').is_file() and dr.load(root/'.generated/dr-render-complete.json').get('root')==str(root),'PRIVATE_RENDER_REQUIRED')
 inspect_unmounted(root)
 # Check the resolved replacement Compose, including private env overrides.
 result=subprocess.run(['docker','compose','--profile','all','config','--format','json'],cwd=root,capture_output=True,text=True,env={k:v for k,v in os.environ.items() if not k.startswith(('COMPOSE_','INFISICAL_'))})
 dr.require(result.returncode==0,'FUTURE_COMPOSE_INVALID');compose=json.loads(result.stdout)
 images=dr.load(root/'config/image-lock.json')
 expected_uris={'aiostreams':{'sqlite://./data/db.sqlite','sqlite:///app/data/db.sqlite'},'aiometadata':{'sqlite://addon/data/db.sqlite','sqlite:///app/addon/data/db.sqlite'}}
 for service,rel in DB_PATHS.items():
  cfg=compose['services'][service];dr.require(cfg['environment'].get('DATABASE_URI') in expected_uris[service],'SQLITE_LAYOUT_UNSUPPORTED')
  dr.require(cfg['image']==images[service]['image'],'PINNED_APPLICATION_IMAGE_REQUIRED')
  mount_target='/app/data' if service=='aiostreams' else '/app/addon/data'
  mounts=[v for v in cfg['volumes'] if v['target']==mount_target]
  dr.require(len(mounts)==1 and mounts[0]['type']=='bind' and Path(mounts[0]['source']).resolve()==root/Path(rel).parent,'DATABASE_BIND_LAYOUT_UNSUPPORTED')
  dr.require(not dr.path_for(root,rel).exists(),'EXISTING_DATABASE_REFUSED')
 marker=dr.path_for(root,'.generated/aio-import-complete.json');dr.require(not marker.exists(),'EXISTING_IMPORT_MARKER_REFUSED')
 dr.require(not Path(args.payload).resolve().is_relative_to(root),'PRIVATE_PAYLOAD_MUST_BE_OUTSIDE_PUBLIC_TREE')
 payload=private_json(args.payload);values=dr.read_values(root,plan,args.input_dir)
 entries,reauth,secret=prepare(root,plan,payload,values)
 if args.command=='validate':print('AIO_PRIVATE_DR_PAYLOAD PASS');return
 dr.require(os.geteuid()==0 or args.synthetic,'ROOT_REQUIRED_FOR_DATABASE_OWNER')
 # Build under private staging first; install with no-replace hard links. A
 # crash never overwrites an existing DB. No source database is opened/read.
 stage=Path(tempfile.mkdtemp(prefix='aio-import-',dir=root/'.generated'));stage.chmod(0o700)
 paths={s:stage/f'{s}.sqlite' for s in SERVICES};build_databases(root,entries,secret,paths)
 for service,path in paths.items():
  destination=dr.path_for(root,DB_PATHS[service]);destination.parent.mkdir(parents=True,exist_ok=True,mode=0o750)
  if not args.synthetic:os.chown(path,1000,1000)
  os.link(path,destination);path.unlink()  # removes only this freshly built staging link
 stage.rmdir()
 evidence={'schema':1,'root':str(root),'versions':SERVICES,'imported_counts':{s:sum(e[0]==s for e in entries) for s in SERVICES},'stable_ids':'PRESERVED','reauthorization':reauth,'source_runtime_db_read':False}
 dr.exclusive(marker,(packed(evidence)+'\n').encode(),0o600)
 for service in SERVICES:print(service,'PRIVATE_DR_IMPORT PASS')
 print('DEPENDENCY_IDS PRESERVED');print('NORMAL_REAUTHORIZATION_TASKS',len(reauth));print('TOTAL PASS')
def main():
 p=argparse.ArgumentParser();p.add_argument('command',choices=['validate','import']);p.add_argument('--target',required=True);p.add_argument('--payload',required=True);p.add_argument('--input-dir');p.add_argument('--synthetic',action='store_true');args=p.parse_args();os.umask(0o077)
 try:run(args)
 except Exception as exc:
  print('AIO_DR FAIL',str(exc) if isinstance(exc,dr.Refused) else type(exc).__name__);return 1
 return 0
if __name__=='__main__':sys.exit(main())
