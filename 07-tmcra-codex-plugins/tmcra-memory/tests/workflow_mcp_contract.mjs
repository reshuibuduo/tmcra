import assert from 'node:assert/strict';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { createServer } from 'node:http';
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { controlKey, setMemoryMode } from '../scripts/memory_controls.mjs';

const root=await mkdtemp(join(tmpdir(),'tmcra-workflow-mcp-'));
const previousStateDir=process.env.TMCRA_MEMORY_STATE_DIR;process.env.TMCRA_MEMORY_STATE_DIR=join(root,'controls');
const calls=[]; let httpStatus=200; const secret='synthetic-private-workflow-api-key';
const snapshot={ revision:7, tasks:[{id:'t1',title:'Specific test task',goal:'run tests',verified:false,verification_state:'reported'}], decisions:[{id:'d1',text:'Prefer option A',needs_review:true}], reminders:[{id:'r1',text:'Review before release'}], attempts:[], bridge_plans:[{plan_id:'plan-1',status:'failed',records:[{item_id:'source-a',record:{id:'source-a',kind:'source',text:'Imported test fact'}}]}], available_records:[{id:'source-a',kind:'source',title:'Selected source example'}] };
const http=createServer(async(req,res)=>{
  let body='';for await(const chunk of req)body+=chunk;
  const parsed=body?JSON.parse(body):null;
  calls.push({path:req.url,method:req.method,body:parsed,auth:req.headers.authorization});
  res.writeHead(httpStatus,{'Content-Type':'application/json','X-Request-ID':'http-metadata-not-package-content'});
  if(httpStatus!==200){res.end(JSON.stringify({detail:secret+' private config error'}));return;}
  if(req.url.includes('/evidence?'))res.end(JSON.stringify({items:[{source_record_id:'source-a',text:'Selected source example full original text'}]}));
  else if(req.method==='GET')res.end(JSON.stringify(snapshot));
  else if(parsed?.action==='bridge.preview')res.end(JSON.stringify({can_commit:true,preview_token:'actual-preview-token',expires_at:Date.now()/1000+60,package_hash:'package-hash',selected_ids:['source-a'],records:[{item_id:'source-a',record:{id:'source-a',kind:'source',text:'Exact selected test fact',source:{system:'other',namespace:'source-project',id:'a'}}}],revocations:[]}));
  else res.end(JSON.stringify({ok:true,action:parsed?.action,recorded:true,verified:false,verification_state:'reported'}));
});
await new Promise(r=>http.listen(0,'127.0.0.1',r));
const config={baseUrl:`http://127.0.0.1:${http.address().port}`,apiKey:secret,projectScope:'project-exact-current',projectId:'workflow-test',timeoutMs:1000};
await writeFile(join(root,'config.json'),JSON.stringify(config));
const env={...process.env};for(const key of Object.keys(env))if(key.startsWith('TMCRA_')||key.startsWith('CLAUDE_PLUGIN_OPTION_'))delete env[key];
Object.assign(env,{TMCRA_CONFIG_FILE:join(root,'config.json'),TMCRA_LOCAL_BINDING_FILE:join(root,'absent-binding'),PLUGIN_DATA:join(root,'plugin'),TMCRA_LOCAL_PROVIDER_CONFIG:join(root,'absent-provider'),TMCRA_MEMORY_STATE_DIR:join(root,'controls')});
function client(capabilities={},answer=()=>({action:'accept',content:{confirm:true}})){
  const child=spawn(process.execPath,[resolve('scripts/mcp_server.mjs')],{env,cwd:process.cwd(),stdio:['pipe','pipe','pipe']});let seq=0,asked=0;const pending=new Map();
  const send=value=>child.stdin.write(JSON.stringify({jsonrpc:'2.0',...value})+'\n');
  createInterface({input:child.stdout}).on('line',line=>{const msg=JSON.parse(line);if(msg.method==='elicitation/create'){asked++;void Promise.resolve(answer(msg)).then(result=>send({id:msg.id,result}));return;}const p=pending.get(msg.id);if(p){pending.delete(msg.id);clearTimeout(p.timer);msg.error?p.reject(Error(msg.error.message)):p.resolve(msg.result);}});
  const request=(method,params)=>new Promise((resolve,reject)=>{const id=++seq;const timer=setTimeout(()=>reject(Error('MCP timeout')),12000);pending.set(id,{resolve,reject,timer});send({id,method,params});});
  return {request,get asked(){return asked;},async init(){await request('initialize',{protocolVersion:'2025-11-25',capabilities,clientInfo:{name:'workflow-contract',version:'1'}});},async close(){child.stdin.end();child.kill();for(const p of pending.values())clearTimeout(p.timer);}};
}
const base={session_id:'workflow-session',project_path:root,project_id:'workflow-test'};
const tool=(c,args)=>c.request('tools/call',{name:'tmcra_workflows',arguments:{...base,...args}});
const execute=(c,action,payload,operation_id='op-'+action)=>tool(c,{operation:'execute',action,payload,operation_id});
const posts=()=>calls.filter(c=>c.method==='POST').length;
async function cli(args,input){return new Promise((resolveResult,reject)=>{const child=spawn(process.execPath,[resolve('scripts/workflow_cli.mjs'),...args],{env,cwd:process.cwd(),stdio:['pipe','pipe','pipe']});let stdout='',stderr='';child.stdout.on('data',x=>stdout+=x);child.stderr.on('data',x=>stderr+=x);child.once('error',reject);child.once('close',code=>resolveResult({code,stdout,stderr}));child.stdin.end(input);});}
let c;
try{
  c=client();await c.init();
  const listed=await c.request('tools/list',{});const schema=listed.tools.find(t=>t.name==='tmcra_workflows');assert.ok(schema);assert.equal(schema.inputSchema.additionalProperties,false);assert.equal(schema.inputSchema.properties.scope,undefined);
  const before=posts();
  for(const operation of ['snapshot','resume','timeline','reminders']){
    const result=await tool(c,{operation,...(operation==='timeline'?{as_of:'2026-10-15T00:00:00Z'}:{})});assert.equal(result.isError,false,JSON.stringify(result));assert.equal(result.structuredContent.trust_boundary,'untrusted_workflow_data');assert.equal(result.structuredContent.scope,config.projectScope);assert.equal(result.structuredContent.data.request_id,undefined);assert.doesNotMatch(JSON.stringify(result),new RegExp(secret));
  }
  assert.equal(posts(),before);assert.ok(calls.some(x=>x.path.includes('as_of=2026-10-15T00%3A00%3A00Z')));
  for(const [action,payload] of [['task.upsert',{id:'t1',title:'Test task'}],['decision.create',{id:'d1',text:'Option A'}],['reminder.evaluate',{event:'project_open'}],['bridge.preview',{package:{format:'example-test-package'},selected_ids:['source-a']}]] ){
    const result=await execute(c,action,payload);assert.equal(result.isError,false,JSON.stringify(result));assert.deepEqual(calls.at(-1).body,{action,payload,operation_id:'op-'+action});
  }
  assert.equal(c.asked,0);assert.ok(calls.every(x=>x.path.startsWith('/v1/scopes/project-exact-current/workflows')));assert.ok(calls.every(x=>x.auth==='Bearer '+secret));
  const after=posts();for(const invalid of [{operation:'execute',action:'_task.executed',payload:{},operation_id:'bad'}, {operation:'execute',action:'task.receipt',payload:{id:'t1',trusted:true},operation_id:'bad'}, {operation:'snapshot',scope:'other'}, {operation:'execute',action:'task.upsert',payload:{id:'t',command:'rm -rf /'},operation_id:'bad'}])assert.equal((await tool(c,invalid)).isError,true);
  assert.equal(posts(),after);
  assert.equal((await execute(c,'record.delete',{kind:'task',id:'t1'})).structuredContent.code,'confirmation_unavailable');assert.equal(posts(),after);
  // The model cannot approve by adding confirmed:true to arguments.
  assert.equal((await tool(c,{operation:'execute',action:'record.delete',payload:{kind:'task',id:'t1'},operation_id:'fake',confirmed:true})).isError,true);assert.equal(posts(),after);
  await c.close();c=null;
  for(const decision of ['decline','cancel','unchecked','accept']){
    const count=posts();c=client({elicitation:{form:{}}},msg=>{assert.equal(posts(),count);assert.match(msg.params.message,/Specific test task/u);assert.match(msg.params.message,/record.delete/u);return {action:decision==='unchecked'?'accept':decision,content:{confirm:decision==='accept'}};});await c.init();
    const result=await execute(c,'record.delete',{kind:'task',id:'t1'});assert.equal(posts(),count+(decision==='accept'?1:0));assert.equal(result.isError,decision!=='accept');assert.equal(c.asked,1);await c.close();c=null;
  }
  c=client({elicitation:{form:{}}},msg=>{assert.match(msg.params.message,/Exact selected test fact|Imported test fact|Selected source example/u);return {action:'accept',content:{confirm:true}};});await c.init();
  const missing=await execute(c,'bridge.commit',{preview_token:'actual-preview-token'});assert.equal(missing.structuredContent.code,'preview_required');
  await execute(c,'bridge.preview',{package:{format:'example-test-package'},selected_ids:['source-a']});
  assert.equal((await execute(c,'bridge.commit',{preview_token:'actual-preview-token'})).isError,false);
  assert.equal((await execute(c,'bridge.plan',{plan_id:'plan-1'})).isError,false);
  assert.equal((await execute(c,'bridge.retry_failed',{plan_id:'plan-1',item_id:'source-a',confirmed:true})).isError,false);
  assert.equal((await execute(c,'bridge.export',{selected_ids:['source-a'],mode:'selected_sources'})).isError,false);
  // A different session cannot borrow this session's preview approval metadata.
  const cross=await c.request('tools/call',{name:'tmcra_workflows',arguments:{...base,session_id:'other-session',operation:'execute',action:'bridge.commit',payload:{preview_token:'actual-preview-token'},operation_id:'cross'}});assert.equal(cross.structuredContent.code,'preview_required');
  await c.close();c=null;
  // Session read-only/off modes prevent network writes; a mode change while
  // the host confirmation is open also invalidates that pending request.
  const policyKey=controlKey(config,config.projectScope);
  await setMemoryMode(policyKey,base.session_id,'recall_only');c=client();await c.init();
  assert.equal((await tool(c,{operation:'snapshot'})).isError,false);let currentCalls=calls.length;
  assert.equal((await execute(c,'task.upsert',{id:'blocked',title:'No write'})).structuredContent.code,'workflow_forbidden');assert.equal(calls.length,currentCalls);
  await setMemoryMode(policyKey,base.session_id,'off');assert.equal((await tool(c,{operation:'snapshot'})).structuredContent.code,'workflow_forbidden');assert.equal(calls.length,currentCalls);await c.close();c=null;
  await setMemoryMode(policyKey,base.session_id,'normal');const postCount=posts();
  c=client({elicitation:{form:{}}},async()=>{await setMemoryMode(policyKey,base.session_id,'off');return {action:'accept',content:{confirm:true}};});await c.init();
  assert.equal((await execute(c,'record.delete',{kind:'task',id:'t1'})).structuredContent.code,'context_changed');assert.equal(posts(),postCount);await c.close();c=null;await setMemoryMode(policyKey,base.session_id,'normal');
  // CLI is real subprocess + HTTP, and flags/pipes cannot simulate a human TTY.
  const read=await cli(['status','--session-id','human-test']);assert.equal(read.code,0,read.stderr);assert.equal(JSON.parse(read.stdout).scope,config.projectScope);
  const recorded=await cli(['request','--input','-','--session-id','human-test'],JSON.stringify({action:'task.receipt',payload:{id:'t1',summary:'Agent report',outcome:'passed'},operation_id:'cli-receipt'}));assert.equal(recorded.code,0,recorded.stderr);assert.equal(JSON.parse(recorded.stdout).data.verified,false);
  const count=posts();const denied=await cli(['request','--input','-','--confirm'],JSON.stringify({action:'record.delete',payload:{kind:'task',id:'t1'},operation_id:'cli-no-tty'}));assert.notEqual(denied.code,0);assert.equal(posts(),count);assert.match(denied.stderr,/confirmation_unavailable/u);
  httpStatus=404;c=client();await c.init();const old=await tool(c,{operation:'snapshot'});assert.equal(old.structuredContent.code,'workflows_unavailable');assert.equal(old.isError,true);assert.doesNotMatch(JSON.stringify(old),new RegExp(secret));await c.close();c=null;
  const oldCli=await cli(['status']);assert.notEqual(oldCli.code,0);assert.match(oldCli.stderr,/workflows_unavailable/u);assert.doesNotMatch(oldCli.stderr,new RegExp(secret));
  httpStatus=500;const failedCli=await cli(['status']);assert.notEqual(failedCli.code,0);assert.doesNotMatch(failedCli.stderr,new RegExp(secret));
  console.log(JSON.stringify({ok:true,stdioDiscovery:true,currentProjectRouting:true,historicalGet:true,fourFeatureActions:true,hostElicitation:true,noModelApproval:true,sameSessionPreviewBinding:true,noRemoteExecution:true,cliHttp:true,cliNonTtyDenied:true,oldService404:true,httpErrorsNonzero:true,sessionModes:true,confirmationContextChange:true}));
}finally{if(previousStateDir===undefined)delete process.env.TMCRA_MEMORY_STATE_DIR;else process.env.TMCRA_MEMORY_STATE_DIR=previousStateDir;await c?.close();http.closeAllConnections();await new Promise(r=>http.close(r));await rm(root,{recursive:true,force:true});}
