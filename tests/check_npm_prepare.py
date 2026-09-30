import argparse,importlib.util,json,sqlite3,stat,sys,tempfile
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'scripts'));spec=importlib.util.spec_from_file_location('npm_dr',ROOT/'scripts/npm-dr.py');m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)
root=Path(tempfile.mkdtemp(prefix='npm-fixture-',dir=None));(root/'.generated').mkdir();db=root/m.DB;db.parent.mkdir(parents=True)
for marker in ('.dr-fresh-target.json','.generated/dr-render-complete.json'):(root/marker).write_text(json.dumps({'root':str(root)}))
with sqlite3.connect(db) as c:
 c.executescript('CREATE TABLE certificate(id INTEGER PRIMARY KEY,provider TEXT,is_deleted INTEGER,owner_user_id INTEGER,domain_names TEXT,meta TEXT);CREATE TABLE user(id INTEGER PRIMARY KEY,email TEXT,is_deleted INTEGER,is_disabled INTEGER);')
 c.execute('INSERT INTO user VALUES(1,?,0,0)',('operator@example.invalid',))
 c.execute('INSERT INTO certificate VALUES(1,?,0,1,?,?)',('letsencrypt',json.dumps(['*.example.invalid']),json.dumps({'dns_challenge':True,'dns_provider':'cloudflare','dns_provider_credentials':'synthetic-legacy-token'})))
 for table in m.TABLES:
  c.execute(f'CREATE TABLE {table}(id INTEGER PRIMARY KEY,certificate_id INTEGER,enabled INTEGER,is_deleted INTEGER,ssl_forced INTEGER,access_list_id INTEGER,forward_host TEXT)');c.execute(f'INSERT INTO {table} VALUES(1,1,1,0,1,9,?)',('synthetic-upstream',))
m.containers=lambda:[];assert m.state_root(argparse.Namespace(target=str(root)))==root;m.prepare(root);first=json.loads((root/m.JOURNAL).read_text());m.prepare(root)
assert first==json.loads((root/m.JOURNAL).read_text());assert stat.S_IMODE(db.stat().st_mode)==0o600;assert stat.S_IMODE((root/m.JOURNAL).stat().st_mode)==0o600
assert 'dns_provider_credentials' not in first['certificate']['meta']
with sqlite3.connect(db) as c:
 assert c.execute('SELECT is_deleted FROM certificate').fetchone()==(1,)
 for table in m.TABLES:assert c.execute(f'SELECT enabled,ssl_forced,access_list_id,forward_host FROM {table}').fetchone()==(0,1,9,'synthetic-upstream')
# No operation on a directory that has no valid fresh-host marker.
try:m.state_root(argparse.Namespace(target=str(ROOT)))
except m.dr.Refused:pass
else:raise AssertionError('LIVE_GUARD_FAILED')
# Mounted replacement targets must be refused, even if the container is stopped.
m.containers=lambda:[{'Mounts':[{'Source':str(root/'data/npm/data')}]}]
try:m.prepare(root)
except m.dr.Refused:pass
else:raise AssertionError('MOUNT_GUARD_FAILED')
print('NPM_RESTORE_QUARANTINE PASS');print('NPM_RESTORE_IDEMPOTENCE PASS');print('NPM_POLICY_PRESERVATION PASS');print('NPM_FRESH_AND_MOUNT_GUARDS PASS')
