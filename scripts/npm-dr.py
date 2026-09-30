#!/usr/bin/env python3
"""Fresh replacement host only. Prepare a restored NPM DB, reissue, then verify.

Never call against the original deployment. No backup/restore or container
start/restart is performed. Private inputs and child output are never printed.
"""
import argparse,ast,fcntl,json,os,re,sqlite3,stat,subprocess,sys
from pathlib import Path
import dr

TABLES=('proxy_host','redirection_host','dead_host','stream')
DB='data/npm/data/database.sqlite'
JOURNAL='.generated/npm-recovery.json'
def command(args,**kw):
    p=subprocess.run(args,stdout=subprocess.PIPE,stderr=subprocess.PIPE,timeout=kw.pop('timeout',60),**kw)
    dr.require(p.returncode==0,'SUBPROCESS_FAILED');return p.stdout
def containers():
    ids=command(['docker','ps','-aq']).decode().split()
    return json.loads(command(['docker','inspect',*ids])) if ids else []
def state_root(args):
    root=Path(args.target).resolve()
    for relative in ('.dr-fresh-target.json','.generated/dr-render-complete.json'):
        p=dr.path_for(root,relative)
        dr.require(p.is_file() and dr.load(p).get('root')==str(root),'FRESH_RENDERED_TARGET_REQUIRED')
    dr.require(dr.path_for(root,DB).is_file(),'RESTORED_NPM_DATABASE_REQUIRED')
    return root
def store(path,obj):
    temp=path.with_name(path.name+'.new')
    dr.exclusive(temp,(json.dumps(obj,indent=2)+'\n').encode(),0o600)
    os.replace(temp,path)
def load_private(path):
    dr.require(path.is_file() and not path.is_symlink(),'PRIVATE_FILE_REQUIRED')
    dr.require(stat.S_IMODE(path.stat().st_mode)&0o077==0,'PRIVATE_JOURNAL_MODE_REQUIRED')
    return dr.load(path)
def read_plan(db):
    db.row_factory=sqlite3.Row
    dr.require([r[0] for r in db.execute('PRAGMA integrity_check')]==['ok'],'NPM_DATABASE_INTEGRITY_FAILED')
    certs=list(db.execute('SELECT * FROM certificate WHERE is_deleted=0'))
    dr.require(len(certs)==1,'EXACTLY_ONE_ACTIVE_WILDCARD_REQUIRED')
    cert=dict(certs[0]);cert['meta']=json.loads(cert['meta']);cert['domain_names']=json.loads(cert['domain_names'])
    dr.require(cert['provider']=='letsencrypt' and cert['meta'].get('dns_challenge') and cert['meta'].get('dns_provider')=='cloudflare','CLOUDFLARE_DNS01_REQUIRED')
    dr.require(len(cert['domain_names'])==1 and re.fullmatch(r'\*\.[A-Za-z0-9.-]+',cert['domain_names'][0]),'SINGLE_WILDCARD_REQUIRED')
    owner=db.execute('SELECT email FROM user WHERE id=? AND is_deleted=0 AND is_disabled=0',(cert['owner_user_id'],)).fetchone()
    dr.require(owner is not None and isinstance(owner[0],str) and '@' in owner[0],'ACTIVE_CERTIFICATE_OWNER_EMAIL_REQUIRED')
    cert['meta'].pop('dns_provider_credentials',None)
    dr.require(not any(k in cert['meta'] for k in ('certificate_key','letsencrypt_certificate')),'UNEXPECTED_CERTIFICATE_BLOB')
    hosts={table:[dict(r) for r in db.execute(f'SELECT id,certificate_id,enabled FROM {table} WHERE is_deleted=0 AND certificate_id=?',(cert['id'],))] for table in TABLES}
    dr.require(any(hosts.values()),'CERTIFICATE_ASSOCIATIONS_REQUIRED')
    return {'schema':1,'phase':'planned','certificate':cert,'owner_email':owner[0],'hosts':hosts}
def prepare_db(path,journal):
    # Called only after the wrapper proves this is an unmounted fresh target.
    with sqlite3.connect(path) as db:
        db.execute('PRAGMA foreign_keys=ON');db.execute('BEGIN IMMEDIATE')
        if journal.exists():
            plan=load_private(journal);dr.require(plan['phase'] in ('planned','prepared'),'PREPARATION_ALREADY_COMPLETED')
        else:
            plan=read_plan(db);store(journal,plan)
        old=plan['certificate']['id']
        for table,rows in plan['hosts'].items():
            dr.require(table in TABLES,'INVALID_HOST_TABLE')
            for row in rows:
                current=db.execute(f'SELECT certificate_id,enabled FROM {table} WHERE id=? AND is_deleted=0',(row['id'],)).fetchone()
                dr.require(current is not None and current[0]==old and current[1] in (0,row['enabled']),'RESTORE_PLAN_DRIFT')
                db.execute(f'UPDATE {table} SET enabled=0 WHERE id=?',(row['id'],))
        # Avoid automatic renewal racing initial issuance. This is reversible
        # quarantine on the replacement DB, not deletion of any record/file.
        db.execute('UPDATE certificate SET is_deleted=1 WHERE id=?',(old,))
        db.commit()
    plan['phase']='prepared';store(journal,plan)
def prepare(root):
    data=(root/'data/npm/data').resolve()
    dr.require(not any(Path(m.get('Source','/')).resolve()==data for c in containers() for m in c.get('Mounts',[])),'NPM_TARGET_ALREADY_MOUNTED')
    path=dr.path_for(root,DB);journal=dr.path_for(root,JOURNAL)
    prepare_db(path,journal)
    # Secure the restored replacement artifact only; no live mode changes.
    os.chmod(path,0o600)
    print('NPM DATABASE_PREPARED PASS')
def selected_container(root):
    matches=[]
    for c in containers():
        labels=c['Config'].get('Labels') or {}
        if labels.get('com.docker.compose.service')!='npm':continue
        mounts={m['Destination']:m for m in c['Mounts']}
        if mounts.get('/data',{}).get('Source')==str(root/'data/npm/data'):matches.append(c)
    dr.require(len(matches)==1,'UNIQUE_REPLACEMENT_NPM_REQUIRED');c=matches[0]
    dr.require(c['State']['Running'],'REPLACEMENT_NPM_NOT_RUNNING')
    labels=c['Config']['Labels'];dr.require(labels.get('com.docker.compose.project.working_dir')==str(root),'COMPOSE_TARGET_MISMATCH')
    mounts={m['Destination']:m for m in c['Mounts']}
    for target,source in (('/data',root/'data/npm/data'),('/etc/letsencrypt',root/'data/npm/data/letsencrypt')):
        dr.require(mounts.get(target,{}).get('Type')=='bind' and mounts[target]['Source']==str(source),'REPLACEMENT_MOUNT_MISMATCH')
    expected=dr.load(root/'config/image-lock.json')['npm']['image']
    dr.require(c['Config']['Image']==expected,'PINNED_NPM_IMAGE_REQUIRED')
    return c

def cloudflare_token(root):
    path=dr.path_for(root,'.secrets/dr-inputs/shared.goenv')
    dr.require(path.is_file() and not path.stat().st_mode&0o007,'PRIVATE_SHARED_SNAPSHOT_REQUIRED')
    result=[]
    for line in path.read_text().splitlines():
        key,sep,value=line.partition('=')
        if sep and key=='CLOUDFLARE_API_TOKEN':result.append(ast.literal_eval(value))
    dr.require(len(result)==1 and isinstance(result[0],str) and re.fullmatch(r'[A-Za-z0-9_-]+',result[0]),'CANONICAL_CLOUDFLARE_TOKEN_REQUIRED')
    return result[0]
def execute(root,action):
    journal=dr.path_for(root,JOURNAL);plan=load_private(journal)
    dr.require(plan['phase'] in ('prepared','complete'),'NPM_PREPARATION_REQUIRED')
    c=selected_container(root);payload={'action':action,'plan':plan}
    if action=='reissue':payload['cloudflare_token']=cloudflare_token(root)
    helper=(root/'scripts/npm-dr-helper.mjs').read_text()
    raw=command(['docker','exec','-i','--user','0','--workdir','/app',c['Id'],'node','--input-type=module','-e',helper],input=json.dumps(payload).encode(),timeout=1200)
    result=json.loads(raw);dr.require(result.get('status')=='PASS','NPM_RECOVERY_FAILED')
    if action=='reissue':
        plan['phase']='complete';plan['current_certificate_id']=result['certificate_id'];store(journal,plan)
    for field in ('CERTIFICATE','ASSOCIATIONS','NGINX','TLS','HTTPS'):
        if field in result:print('NPM',field,result[field])
    print('NPM',action.upper(),'PASS')
def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('action',choices=['prepare','reissue','verify']);parser.add_argument('--target',required=True);args=parser.parse_args()
    os.umask(0o077);root=state_root(args)
    lock=dr.path_for(root,'.generated/npm-recovery.lock')
    fd=os.open(lock,os.O_RDWR|os.O_CREAT|os.O_NOFOLLOW,0o600)
    with os.fdopen(fd,'w') as file:
        fcntl.flock(file,fcntl.LOCK_EX|fcntl.LOCK_NB)
        if args.action=='prepare':prepare(root)
        else:execute(root,args.action)
if __name__=='__main__':
    try:main()
    except Exception as exc:
        print('NPM DR_REFUSED',str(exc) if isinstance(exc,dr.Refused) else type(exc).__name__);sys.exit(1)
