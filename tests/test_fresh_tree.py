import ast,base64,hashlib,hmac,importlib.util,io,json,os,shutil,subprocess,tarfile,tempfile,urllib.parse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];EVID=Path(tempfile.mkdtemp(prefix='dr-fresh-test-'));EVID.chmod(0o700)
class Failed(Exception):pass
def need(value,code):
 if not value:raise Failed(code)
def call(args,cwd,expected=0):
 env={k:v for k,v in os.environ.items() if not k.startswith(('COMPOSE_','INFISICAL_'))};env['PYTHONDONTWRITEBYTECODE']='1'
 p=subprocess.run(args,cwd=cwd,env=env,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=120)
 need(p.returncode==expected,'COMMAND_FAILED:'+Path(args[0]).name)
 return p.stdout
def unpack(raw,target):
 with tarfile.open(fileobj=io.BytesIO(raw)) as archive:
  for f in archive.getmembers():need(not f.issym() and not f.islnk() and not Path(f.name).is_absolute() and '..' not in Path(f.name).parts,'UNSAFE_ARCHIVE')
  archive.extractall(target)
base=Path(tempfile.mkdtemp(prefix='fresh-archive-',dir=EVID));source=base/'source';source.mkdir()
files=sorted(set(subprocess.run(['git','ls-files','--cached','--others','--exclude-standard'],cwd=ROOT,capture_output=True,text=True,check=True).stdout.splitlines()))
for name in files:
 parts=Path(name).parts
 need(name!='.env' and not any(p in ('.git','.generated','.secrets','__pycache__') for p in parts),'EXCLUDED_FILE')
 need(not name.endswith(('.db','.sqlite','.sqlite3','.pem','.key','.backup','.before')),'RUNTIME_FILE')
 file=ROOT/name;need(file.is_file() and not file.is_symlink(),'PUBLIC_SOURCE_MISSING')
 dest=source/name;dest.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(file,dest)
call(['git','init','-q','-b','main'],source);call(['git','add','-A'],source);tree=call(['git','write-tree'],source).decode().strip();archive=call(['git','archive',tree],source)
target=base/'fresh';target.mkdir();unpack(archive,target)
plan=json.loads((target/'config/dr-render-plan.json').read_text());script=target/'scripts/dr.py'
spec=importlib.util.spec_from_file_location('dr',script);dr=importlib.util.module_from_spec(spec);spec.loader.exec_module(dr)
args=['python3','-B',str(script)]
call(args+['init','--target',str(target)],target)
call(args+['agent-config','--target',str(target),'--project-id','synthetic-project','--address','https://infisical.example.invalid','--client-id-file',str(base/'client-id'),'--client-secret-file',str(base/'client-secret')],target)
values={ref:'synthetic-test' for ref in plan['required_values']}
for ref in values:
 key=ref.rsplit('/',1)[1]
 if any(word in key for word in ('URL','URI','ORIGIN','HREF','ICON')):values[ref]='https://service.example.invalid'
 if key=='REGISTRY_AUTH_CONFIG':values[ref]='{"auths":{}}'
 if key=='ALLOWED_EMAILS':values[ref]='operator@example.invalid\n'
 if key=='LISTEN_ADDRESSES_0':values[ref]='0.0.0.0:53'
 if key=='MONITORING_UI_LISTEN_ADDRESS':values[ref]='0.0.0.0:80'
 if 'CIDRS' in key:values[ref]='198.18.0.0/24'
 if key=='COMMAND_ARG_1':values[ref]='-L=http://:8080'
for index,(key,row) in enumerate(plan['root_env'].items()):
 ref=row['path']+'/'+row['key'];prefix='198.19.0' if 'VPN_NET' in key else '198.18.0'
 if 'IPV4_ADDRESS' in key:values[ref]=prefix+'.'+str(index+10)
 if 'SUBNET' in key:values[ref]=prefix+'.0/24'
def scram(password):
 salt=b'synthetic-test-salt';n=4096;salted=hashlib.pbkdf2_hmac('sha256',password.encode(),salt,n)
 b64=lambda s:base64.b64encode(s).decode()
 return 'SCRAM-SHA-256$'+str(n)+':'+b64(salt)+'$'+b64(hashlib.sha256(hmac.new(salted,b'Client Key',hashlib.sha256).digest()).digest())+':'+b64(hmac.new(salted,b'Server Key',hashlib.sha256).digest())
password="synthetic'password=#$";verifier=scram(password);userlist=[]
for connection in plan['postgres_bootstrap']['connections']:
 svc=connection['service'];uri=svc+':'+urllib.parse.quote(password,safe='')+'@pgbouncer:5432/'+svc
 values[connection['ref']]=uri if svc=='comet' else 'postgresql://'+uri
 userlist.append('"'+svc+'" "'+verifier+'"')
values[plan['postgres_bootstrap']['admin_password_ref']]=password;userlist.append('"postgres" "'+verifier+'"')
values[plan['postgres_bootstrap']['userlist_ref']]='\n'.join(userlist)+'\n'
for item in plan['service_folder_exports'].values():values[item['path']+'/SYNTHETIC_ONLY']='synthetic-test'
sys_path=str(ROOT/'tests')
import sys
sys.path.insert(0,sys_path)
from test_aio_dr import fixture
payload,aio_values,registry=fixture();values.update(aio_values)
payload_path=base/'private-dr-fixture.json';payload_path.write_text(json.dumps(payload));payload_path.chmod(0o600)
inputdir=target/'.secrets/dr-inputs'
for folder in dr.all_paths(plan):
 f=inputdir/dr.folder_file(folder);f.write_text(''.join(k.rsplit('/',1)[1]+'='+repr(v)+'\n' for k,v in sorted(values.items()) if k.rsplit('/',1)[0]==folder));f.chmod(0o600)
missing=[item['template'] for item in plan['files'] if item.get('template') and not(target/item['template']).is_file()]
need(not missing,'PUBLIC_TEMPLATE_MISSING')
# Missing allowlist fails closed, before partial outputs (not a fake substitute).
oauth=inputdir/dr.folder_file('/oauth2-proxy');saved=oauth.read_text();oauth.write_text(''.join(line+'\n' for line in saved.splitlines() if not line.startswith('ALLOWED_EMAILS=')))
call(args+['render','--target',str(target),'--synthetic'],target,1);need(not(target/'.env').exists(),'MISSING_ALLOWLIST_PARTIAL_WRITES');oauth.write_text(saved)
# Explicit wildcard/empty allowlists may not widen authentication.
for invalid in ('','*','*@example.invalid'):
 oauth.write_text(''.join(line+'\n' for line in saved.splitlines() if not line.startswith('ALLOWED_EMAILS='))+'ALLOWED_EMAILS='+repr(invalid)+'\n')
 call(args+['render','--target',str(target),'--synthetic'],target,1);need(not(target/'.env').exists(),'INVALID_ALLOWLIST_PARTIAL_WRITES')
oauth.write_text(saved)
actual={'FRESH_DEPLOY_TEST':'PENDING','missing_templates':missing,'partial_outputs_written':False,'archive_only':True,'real_values_used':False}
# All actual public template sources exist; no structural stubs are added.
# Preexisting render marker must refuse before writing any output.
marker=target/'.generated/dr-render-complete.json';marker.write_text('{}')
call(args+['render','--target',str(target),'--synthetic'],target,1);need(not(target/'.env').exists(),'MARKER_GUARD_PARTIAL_WRITES');marker.unlink()
call(args+['render','--target',str(target),'--synthetic'],target)
call(args+['prepare-state','--target',str(target),'--synthetic'],target)
# Synthetic stand-in for the ONE required encrypted-runtime backup artifact.
# No database is read or copied from the running deployment.
import sqlite3
with sqlite3.connect(target/'data/npm/data/database.sqlite') as db:
 db.executescript('CREATE TABLE certificate(id INTEGER PRIMARY KEY,provider TEXT,is_deleted INTEGER,owner_user_id INTEGER,domain_names TEXT,meta TEXT);CREATE TABLE user(id INTEGER PRIMARY KEY,email TEXT,is_deleted INTEGER,is_disabled INTEGER);')
 db.execute('INSERT INTO user VALUES(1,?,0,0)',('operator@example.invalid',))
 db.execute('INSERT INTO certificate VALUES(1,?,0,1,?,?)',('letsencrypt',json.dumps(['*.example.invalid']),json.dumps({'dns_challenge':True,'dns_provider':'cloudflare','dns_provider_credentials':'synthetic-old-token'})))
 for table in ('proxy_host','redirection_host','dead_host','stream'):
  db.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,certificate_id INTEGER,enabled INTEGER,is_deleted INTEGER)');db.execute(f'INSERT INTO {table} VALUES(1,1,1,0)')
call(['python3','-B',str(target/'scripts/npm-dr.py'),'prepare','--target',str(target)],target)

# Fresh-only AIO import from an EXTERNAL synthetic logical payload. No source DB.
importer=['python3','-B',str(target/'scripts/aio-dr.py')]
call(importer+['validate','--target',str(target),'--payload',str(payload_path)],target)
call(importer+['import','--target',str(target),'--payload',str(payload_path),'--synthetic'],target)
# A repeated import refuses existing DBs; their contents remain byte-identical.
before={service:(target/f'data/{service}/data/db.sqlite').read_bytes() for service in ('aiostreams','aiometadata')}
call(importer+['import','--target',str(target),'--payload',str(payload_path),'--synthetic'],target,1)
need(all(before[service]==(target/f'data/{service}/data/db.sqlite').read_bytes() for service in before),'EXISTING_AIO_DB_CHANGED')
call(args+['preflight','--target',str(target)],target)
call(['docker','compose','--profile','all','config','--quiet'],target)
parsed=json.loads(call(['docker','compose','--profile','all','config','--format','json'],target))
need(all(not v.get('external',False) for v in parsed['volumes'].values()),'EXTERNAL_VOLUME_GATE')
for dest,bindings in plan['env_files'].items():
 for key,row in bindings.items():need(parsed['services'][Path(dest).stem]['environment'][key].replace('$$','$')==values[row['path']+'/'+row['key']],'ENV_ROUNDTRIP:'+Path(dest).stem+':'+key)
manifest=json.loads((target/'config/dr-manifest.yaml').read_text())
for row in manifest['references']:
 if row['TYPE']=='BIND_MOUNT':need((target/row['SOURCE']).exists(),'BIND_SOURCE_MISSING')
need(not any(u['blocks_quick_redeploy'] for u in manifest['state_units'] if u['classification'] in ('OPTIONAL_RUNTIME_STATE','DISPOSABLE_STATE')),'OPTIONAL_STATE_GATE')
sql=(target/plan['postgres_bootstrap']['destination']).read_text();need(sql.count('\\gexec')==6 and sql.count(verifier)==4 and password not in sql,'SCRAM_SQL_EXACT_CREDENTIAL')
need(dr.postgres_connection("comet:synthetic%27password%3D%23%24@pgbouncer/comet",'comet')==('comet',password,'comet'),'COMET_CONNECTION_PARSE')
need(dr.userlist_entries('"user""name" "password""value"\n')=={'user"name':'password"value'},'USERLIST_QUOTES')
def refuses(fn):
 try:fn()
 except dr.Refused:return
 raise Failed('EXPECTED_REFUSAL')
refuses(lambda:dr.verified_role_password('comet','wrong',verifier))
refuses(lambda:dr.verified_role_password('comet',password,None))
refuses(lambda:dr.userlist_entries('"user" "first"\n"user" "second"'))
need(dr.sql_literal("synthetic'quote") == "'synthetic''quote'",'SQL_QUOTE')
# Repeated provisioning cannot change existing state or configs, including mode.
state=target/'data/comet/data';sentinel=state/'synthetic-existing-state';sentinel.write_text('synthetic');sentinel.chmod(0o600);before=(sentinel.read_bytes(),sentinel.stat().st_mode,state.stat().st_mode)
call(args+['prepare-state','--target',str(target),'--synthetic'],target)
need(before==(sentinel.read_bytes(),sentinel.stat().st_mode,state.stat().st_mode),'STATE_CHANGED')
call(args+['render','--target',str(target),'--synthetic'],target,1);call(args+['init','--target',str(target)],target,1)
# Symlinks inside a future state source are never traversed.
slot=target/manifest['quick_redeploy']['empty_directories'][0]['path']
need(not any(slot.iterdir()),'SYNTHETIC_DIRECTORY_NOT_EMPTY');slot.rmdir();slot.symlink_to(base,target_is_directory=True)
call(args+['prepare-state','--target',str(target),'--synthetic'],target,1);slot.unlink();slot.mkdir()
result={'STRUCTURAL_HARNESS':'PASS','COMPOSE_SYNTHETIC_ALL_REAL_TEMPLATES':'PASS','RENDER_GUARDS':'PASS','SCRAM_BOOTSTRAP_TESTS':'PASS','STATE_DIRECTORY_IDEMPOTENCE':'PASS','OPTIONAL_BACKUP_GATES':0,'PRIVATE_RUNTIME_COPIED':False,'REAL_INFISICAL_VALUES_USED':False,'APP_RUNTIME_STARTUP_TESTED':False,'synthetic_stubs':len(missing),'directory':str(target),'actual_public_tree':actual,'public_files':len(files)}
actual['FRESH_DEPLOY_TEST']='PASS';actual['scope']='Public archive + synthetic Infisical + synthetic NPM backup + synthetic PRIVATE_DR_PAYLOAD; no running applications or real credentials.'
result['AIO_PRIVATE_DR_IMPORT']='PASS';result['OAUTH_ALLOWLIST_FAIL_CLOSED']='PASS';result['QUICK_REDEPLOY_WITH_MINIMAL_REQUIRED_BACKUP']='PASS';result['actual_public_tree']=actual
(EVID/'fresh-harness-test.json').write_text(json.dumps(result,indent=2));(EVID/'fresh-harness-test.json').chmod(0o600)
print('FRESH_DEPLOY_TEST PASS');print('COMPOSE_SYNTHETIC_ALL_REAL_TEMPLATES PASS');print('AIO_PRIVATE_DR_IMPORT PASS');print('SCRAM_BOOTSTRAP_TESTS PASS');print('EMPTY_STATE_PREPARATION PASS');print('OPTIONAL_BACKUP_GATES 0');print('QUICK_REDEPLOY_WITH_MINIMAL_REQUIRED_BACKUP PASS');print('EVIDENCE',EVID/'fresh-harness-test.json')
