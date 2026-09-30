"""Synthetic logical-export/storage compatibility tests. No source DB access."""
import copy,importlib.util,json,os,sqlite3,subprocess,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'scripts'))
import dr
spec=importlib.util.spec_from_file_location('aio_dr',ROOT/'scripts/aio-dr.py');aio=importlib.util.module_from_spec(spec);spec.loader.exec_module(aio)

def fixture():
 registry={s:{} for s in aio.SERVICES};payload={'schema':1,'aiostreams':[],'aiometadata':[]}
 for service,count in [('aiostreams',2),('aiometadata',4)]:
  for number in range(count):
   name='profile'+str(number+1);uid=str(uuid.UUID(int=(number+1)*(100 if service=='aiostreams' else 1000)))
   registry[service][name]={'uuid':uid,'password':'synthetic-password-'+name}
   if service=='aiometadata':registry[service][name]['trusted']=True
   cfg={'addonName':'Synthetic test','catalogs':[],'apiKeys':{'tmdb':'synthetic-public-fixture'},'nested':{'id':''}}
   row={'profile':name,'version':aio.SERVICES[service],'export':cfg if service=='aiostreams' else {'version':aio.SERVICES[service],'config':cfg,'metadata':{'apiKeysExcluded':False}},'bindings':[],'dependencies':[],'reauthorize':[]}
   if service=='aiostreams':row['linked_accounts']='reauthorize'
   payload[service].append(row)
 payload['aiostreams'][1]['export']['parentConfig']={'uuid':'','password':''}
 for field in ['uuid','password']:payload['aiostreams'][1]['dependencies'].append({'pointer':'/parentConfig/'+field,'service':'aiostreams','profile':'profile1','field':field})
 payload['aiostreams'][0]['export']['jellyfin']={'primary':{'lock':'1234'}}
 registry['aiostreams']['profile1']['profile']={'id':'synthetic-profile','owner':'synthetic-operator','label':'Primary','alias':'synthetic-alias'}
 registry['aiometadata']['profile1']['alias']='synthetic-metadata'
 payload['aiometadata'][0]['export']['config']['apiKeys']['traktTokenId']='synthetic-expired-reference'
 payload['aiometadata'][0]['reauthorize']=[{'provider':'trakt','remove_pointers':['/apiKeys/traktTokenId']}]
 values={'/dr/AIO_PROFILE_BINDINGS':json.dumps(registry),'/aiostreams/SECRET_KEY':'ab'*32,'/shared/TMDB_API_KEY':'synthetic-tmdb-binding'}
 payload['aiometadata'][0]['bindings']=[{'pointer':'/apiKeys/tmdb','ref':'/shared/TMDB_API_KEY'}]
 return payload,values,registry

class ImportTests(unittest.TestCase):
 def setUp(self):self.payload,self.values,self.registry=fixture()
 def prepared(self):return aio.prepare(ROOT,{},self.payload,self.values)
 def test_semantics_dependencies_reauth(self):
  entries,tasks,key=self.prepared();self.assertEqual(len(entries),6);self.assertEqual(len(tasks),1)
  self.assertEqual(entries[1][3]['parentConfig']['uuid'],entries[0][2]['uuid']);self.assertEqual(entries[1][3]['parentConfig']['password'],entries[0][2]['password'])
  self.assertNotIn('traktTokenId',entries[2][3]['apiKeys']);self.assertEqual(entries[2][3]['apiKeys']['tmdb'],self.values['/shared/TMDB_API_KEY'])
 def test_native_node_storage_codec(self):
  node=os.environ.get('DR_TEST_NODE','node');entries,tasks,key=self.prepared()
  with tempfile.TemporaryDirectory() as directory:
   paths={s:Path(directory)/(s+'.sqlite') for s in aio.SERVICES};aio.build_databases(ROOT,entries,key,paths)
   with sqlite3.connect(paths['aiostreams']) as db:
    rows=db.execute('SELECT uuid,password_hash,config,config_salt FROM users ORDER BY uuid').fetchall();self.assertEqual(len(rows),2)
    self.assertTrue(aio.bcrypt.checkpw(entries[0][2]['password'].encode(),rows[0][1].encode()))
    self.assertEqual(db.execute('SELECT owner,alias FROM config_profiles').fetchone(),('synthetic-operator','synthetic-alias'))
   with sqlite3.connect(paths['aiometadata']) as db:
    self.assertEqual(db.execute('SELECT count(*) FROM user_configs').fetchone()[0],4);self.assertEqual(db.execute('SELECT count(*) FROM trusted_uuids').fetchone()[0],4)
    self.assertEqual(db.execute('SELECT alias FROM user_aliases').fetchone()[0],'synthetic-metadata')
   # Independent native Node decoder with the exact pinned application's codec.
   code="""const fs=require('node:fs'),c=require('node:crypto'),z=require('node:zlib');
const p=JSON.parse(fs.readFileSync(0,'utf8'));
for(const row of p.rows){const key=c.pbkdf2Sync(Buffer.from(p.passwords[row[0]]+':'+p.secret),Buffer.from(row[3],'hex'),100000,32,'sha512');const data=JSON.parse(Buffer.from(row[2],'base64url'));const d=c.createDecipheriv('aes-256-cbc',key,Buffer.from(data.i,'base64'));const obj=JSON.parse(z.inflateSync(Buffer.concat([d.update(Buffer.from(data.e,'base64')),d.final()])));if(obj.addonName!=='Synthetic test')process.exit(1);if(obj.uuid||obj.trusted)process.exit(2);}
process.stdout.write('CODEC PASS');"""
   request={'rows':rows,'passwords':{e[2]['uuid']:e[2]['password'] for e in entries if e[0]=='aiostreams'},'secret':key}
   result=subprocess.run([node,'-e',code],input=json.dumps(request).encode(),capture_output=True);self.assertEqual(result.returncode,0);self.assertEqual(result.stdout,b'CODEC PASS')
 def test_duplicate_uuid_refused(self):
  d=json.loads(self.values['/dr/AIO_PROFILE_BINDINGS']);d['aiometadata']['profile1']['uuid']=d['aiostreams']['profile1']['uuid'];self.values['/dr/AIO_PROFILE_BINDINGS']=json.dumps(d)
  with self.assertRaises(dr.Refused):self.prepared()
 def test_export_version_refused(self):
  self.payload['aiostreams'][0]['version']='0.0.0'
  with self.assertRaises(dr.Refused):self.prepared()
 def test_missing_binding_refused(self):
  del self.values['/shared/TMDB_API_KEY']
  with self.assertRaises(dr.Refused):self.prepared()
 def test_token_reference_not_silently_lost(self):
  self.payload['aiometadata'][0]['reauthorize']=[]
  with self.assertRaises(dr.Refused):self.prepared()
 def test_uuid_conflict_refused(self):
  self.payload['aiostreams'][0]['export']['uuid']='conflicting-fixture'
  with self.assertRaises(dr.Refused):self.prepared()
 def test_examples_are_not_real_payloads(self):
  self.payload['aiostreams'][0]['export']={'REPLACE_WITH_OFFICIAL_PRIVATE_EXPORT':True}
  with self.assertRaises(dr.Refused):self.prepared()
 def test_mounted_target_guard(self):
  result1=subprocess.CompletedProcess([],0,stdout='fixture-id\n');result2=subprocess.CompletedProcess([],0,stdout=json.dumps([{'Config':{'Labels':{}},'Mounts':[{'Type':'bind','Source':'/synthetic-root'}]}]))
  with patch.object(aio.subprocess,'run',side_effect=[result1,result2]):
   with self.assertRaises(dr.Refused):aio.inspect_unmounted(Path('/synthetic-root'))
 def test_private_payload_permissions(self):
  with tempfile.TemporaryDirectory() as directory:
   p=Path(directory)/'fixture.json';p.write_text('{}');p.chmod(0o644)
   with self.assertRaises(dr.Refused):aio.private_json(p)
 def test_schema_and_no_overwrite_sql(self):
  entries,_,key=self.prepared()
  with tempfile.TemporaryDirectory() as directory:
   paths={s:Path(directory)/(s+'.sqlite') for s in aio.SERVICES};aio.build_databases(ROOT,entries,key,paths)
   with self.assertRaises(sqlite3.IntegrityError):aio.build_databases(ROOT,entries,key,paths)
   for p in paths.values():self.assertEqual(p.stat().st_mode&0o777,0o600)

if __name__=='__main__':unittest.main()
