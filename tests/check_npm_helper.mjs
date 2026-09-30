import vm from 'node:vm';
import fs from 'node:fs';
import assert from 'node:assert/strict';
const code=fs.readFileSync(new URL('../scripts/npm-dr-helper.mjs',import.meta.url),'utf8');
async function runScenario({missing=false,issued=false,action='reissue',failIssue=false,phase='prepared',active=false}={}) {
  const domain='*.example.invalid';let stdout='',issueCalls=0,reloads=0;let exitCode=null;const generated=[];
  const cert={id:1,provider:'letsencrypt',owner_user_id:1,is_deleted:true,domain_names:[domain],meta:{dns_challenge:true,dns_provider:'cloudflare'}};
  const host={id:1,certificate_id:1,is_deleted:false,enabled:active,domain_names:['app.example.invalid'],ssl_forced:true,access_list_id:9,forward_host:'synthetic-upstream',forward_port:8080};
  const saved={id:1,certificate_id:1,enabled:1};const data={access_list:[{id:9,is_deleted:false,items:[],clients:[]}],certificate:missing?[]:[structuredClone(cert)],user:[{id:1,is_deleted:false,is_disabled:false,email:'operator@example.invalid'}],proxy_host:[host,{id:8,certificate_id:0,is_deleted:false,enabled:true,domain_names:['plain.example.invalid'],forward_host:'unrelated-upstream'}],redirection_host:[],dead_host:[],stream:[]};
  const payload={action,cloudflare_token:'synthetic-token',plan:{schema:1,phase,certificate:cert,owner_email:'operator@example.invalid',hosts:{proxy_host:[saved],redirection_host:[],dead_host:[],stream:[]}}};
  class Query {
    constructor(table){this.table=table;this.criteria=[];this.id=null;}
    where(k,v){this.criteria.push([k,v]);return this;}
    findById(id){this.id=id;return this;}
    allowGraph(){return this;}withGraphFetched(){return this;}
    then(resolve,reject){try{const rows=data[this.table].filter(r=>this.criteria.every(([k,v])=>r[k]===v));return Promise.resolve(this.id===null?rows:rows.find(r=>r.id===this.id)).then(resolve,reject);}catch(e){return Promise.reject(e).then(resolve,reject);}}
    async insertAndFetch(obj){const row={...structuredClone(obj),id:2};data[this.table].push(row);return row;}
    async patchAndFetchById(id,patch){const row=data[this.table].find(x=>x.id===id);assert(row);Object.assign(row,patch);return row;}
  }
  const model=table=>({query:()=>new Query(table),defaultAllowGraph:'*',defaultExpand:[]});
  const packageJson=JSON.stringify({version:'2.16.0'});
  const mockedFs={mkdirSync(){},chownSync(){},chmodSync(){},statSync:()=>({uid:1000,gid:1000}),existsSync:()=>true,readFileSync:path=>{
    if(path==='/app/package.json')return packageJson;
    assert(issued,'certificate not issued');return Buffer.from('synthetic-certificate-fixture');
  }};
  const mockedCrypto={X509Certificate:class{constructor(){this.subjectAltName='DNS:'+domain;this.validTo='Jan 1 2050 GMT';this.publicKey={export:()=>Buffer.from('same-synthetic-key')};}},createPrivateKey:()=>({}),createPublicKey:()=>({export:()=>Buffer.from('same-synthetic-key')})};
  const mockedTls={connect:(_opts,cb)=>{queueMicrotask(cb);return {end(){},setTimeout(){},on(){}};}};
  const mockedHttps={get:(_opts,cb)=>{queueMicrotask(()=>cb({statusCode:200,resume(){}}));return {setTimeout(){},on(){}};}};
  const nginx={generateConfig:async(table,row)=>{assert.equal(row.enabled,true);generated.push(table+':'+row.id);},test:async()=>{},reload:async()=>{reloads++;}};
  const imports={
    'node:fs':{default:mockedFs},'node:tls':{default:mockedTls},'node:https':{default:mockedHttps},'node:crypto':mockedCrypto,
    '/app/models/access_list.js':{default:model('access_list')},'/app/internal/access-list.js':{default:{build:async()=>{},getFilename:list=>'/data/access/'+list.id}},'/app/models/certificate.js':{default:model('certificate')},'/app/models/user.js':{default:model('user')},
    '/app/internal/nginx.js':{default:nginx},'/app/internal/certificate.js':{default:{requestLetsEncryptSslWithDnsChallenge:async(c,email)=>{assert.equal(email,'operator@example.invalid');assert.equal(c.meta.dns_provider_credentials,'dns_cloudflare_api_token = synthetic-token\n');issueCalls++;if(failIssue)throw Error('synthetic failure');issued=true;}}}
  };
  for(const table of ['proxy_host','redirection_host','dead_host','stream'])imports['/app/models/'+table+'.js']={default:model(table)};
  const context=vm.createContext({Buffer,Date,JSON,Promise,Error,console,queueMicrotask,process:{env:{},stdout:{write:v=>{stdout+=v;}},stderr:{write(){}},stdin:{async *[Symbol.asyncIterator](){yield JSON.stringify(payload);}},exit:n=>{exitCode=n;}}});
  const cache=new Map();async function dependency(name){if(cache.has(name))return cache.get(name);assert(name in imports,name);const obj=imports[name];const m=new vm.SyntheticModule(Object.keys(obj),function(){for(const [k,v]of Object.entries(obj))this.setExport(k,v);},{context});cache.set(name,m);await m.link(()=>{});await m.evaluate();return m;}
  const mod=new vm.SourceTextModule(code,{context,importModuleDynamically:dependency});await mod.link(dependency);
  try{await mod.evaluate();}catch(e){assert.equal(e.message,'EXIT');}
  const responses=stdout.trim().split('\n').filter(Boolean).map(JSON.parse);assert.equal(responses.length,1);const first=responses[0];assert.equal(exitCode,failIssue?1:0);
  assert.equal(host.ssl_forced,true);assert.equal(host.access_list_id,9);assert.equal(host.forward_host,'synthetic-upstream');assert.equal(host.forward_port,8080);
  if(failIssue){assert.equal(first.status,'FAIL');assert.equal(host.enabled,false);assert.equal(reloads,0);}
  else {assert.equal(first.status,'PASS');assert.equal(host.enabled,true);assert.equal(host.certificate_id,missing?2:1);assert.equal(first.certificate_id,missing?2:1);if(action==='reissue')assert(generated.includes('proxy_host:8'));assert.equal(data.proxy_host[1].forward_host,'unrelated-upstream');assert.equal(issueCalls,issued&&action==='verify'?0:(action==='reissue'&&phase==='prepared'?1:0));}
  return {status:first.status,issueCalls};
}
await runScenario();console.log('NPM_SAME_CERTIFICATE_ID PASS');
await runScenario({missing:true});console.log('NPM_NEW_CERTIFICATE_ID_ASSOCIATIONS PASS');
await runScenario({issued:true,phase:'complete',active:true});console.log('NPM_IDEMPOTENT_REISSUE PASS');
await runScenario({failIssue:true});console.log('NPM_ISSUANCE_FAILURE_CLOSED PASS');
await runScenario({issued:true,action:'verify',phase:'complete',active:true});console.log('NPM_HTTPS_VERIFIER PASS');
