// Real Chromium + production BFF handler, with explicitly synthetic auth/catalog/upstream.
// PLAYWRIGHT_MODULE=/path/to/playwright/index.mjs node tests/personal-workflows.browser.mjs
import assert from 'node:assert/strict';
import { createServer } from 'node:http';
import { readFile, mkdir } from 'node:fs/promises';
import { join } from 'node:path';
import { workflowHandler, workflowProjects } from '../app/api/personal/workflows/handler.mjs';
const { chromium } = await import(process.env.PLAYWRIGHT_MODULE || 'playwright');
const prefix = 'personal-3baf70b4c7ac2268';
const projectScope = `${prefix}-project-code-agent-1234567890abcdef`;
const catalog = [{scope_name:`${prefix}-global`}, {scope_name:projectScope}, {scope_name:'personal-other-project-private'}];
const projects = await workflowProjects(prefix, catalog);
const selected = projects.find((item) => item.scope === projectScope);
const states = new Map([
  [`${prefix}-global`, {revision:1,tasks:[{id:'global-task',title:'全局任务'}],decisions:[],attempts:[],reminders:[],bridge_plans:[],available_records:[{id:'global-source',kind:'source',title:'全局来源'}]}],
  [projectScope, {revision:4,tasks:[{id:'plugin-task',title:'插件原有任务'}],decisions:[],attempts:[],reminders:[],bridge_plans:[],available_records:[{id:'plugin-source',kind:'source',title:'插件项目来源'}]}],
]);
const calls=[], errors=[];
const handler = workflowHandler({
  requirePersonalAccess:async()=>({space:{id:'space-test',scopeName:prefix}}),
  personalMemoryBinding:()=>({apiKey:'server-only-fixture'}),
  personalMemoryRequest:async(binding,path,init)=>{
    calls.push({path,method:init.method,body:init.body&&JSON.parse(init.body)});
    assert.equal(binding.apiKey,'server-only-fixture');
    if(path.startsWith('/v1/scopes?')) return catalog;
    const scope=decodeURIComponent(path.match(/^\/v1\/scopes\/([^/]+)\/workflows$/u)[1]);
    const state=states.get(scope); assert.ok(state,`must target actual scope: ${scope}`);
    if(init.method==='GET') return structuredClone(state);
    const body=JSON.parse(init.body);
    if(body.action==='task.upsert') {state.tasks.push({...body.payload,verification_state:'unverified'});state.revision++;return {record:body.payload};}
    throw new Error('unexpected mutation');
  },
  noStoreHeaders:{'cache-control':'no-store'},
  contractError:(status,code,message)=>({status,code,message}),
  personalErrorResponse:(e)=>Response.json({error:{message:e.message,code:e.code}},{status:e.status||500}),
});
const server=createServer(async(req,res)=>{
  try {
    const origin=`http://127.0.0.1:${server.address().port}`;
    if(req.url.startsWith('/api/personal/workflows')){
      let body='';for await(const chunk of req)body+=chunk;
      const request=new Request(origin+req.url,{method:req.method,headers:req.headers,...(body?{body}:{})});
      const response=await handler(request,req.method==='POST');
      res.writeHead(response.status,Object.fromEntries(response.headers));return res.end(await response.text());
    }
    const name=new URL(req.url,origin).pathname.split('/').pop();
    if(!['workflow-center.html','workflow-center.js','workflow-center.css'].includes(name)){res.writeHead(404);return res.end();}
    res.setHeader('content-type',name.endsWith('.js')?'text/javascript':name.endsWith('.css')?'text/css':'text/html');
    res.end(await readFile(new URL('../public/workflows/'+name,import.meta.url)));
  } catch(error){errors.push(error.message);res.writeHead(500);res.end(error.message);}
});
await new Promise((resolve)=>server.listen(0,'127.0.0.1',resolve));
const browser=await chromium.launch({headless:true});
try{
  const page=await browser.newPage({viewport:{width:1360,height:900}});page.on('pageerror',(error)=>errors.push(error.message));
  await page.goto(`http://127.0.0.1:${server.address().port}/workflows/workflow-center.html?mode=personal`);
  await page.getByText('全局任务',{exact:true}).waitFor();
  assert.equal(await page.locator('#local-connection').isVisible(),false);
  assert.equal(await page.locator('#personal-project option').count(),2);
  await page.locator('#export-mode').selectOption('selected_sources');
  await page.getByText('全局来源 · source',{exact:true}).waitFor();
  await page.locator('#task-form').evaluate((el)=>el.closest('details').open=true);
  await page.locator('#task-form [name=id]').fill('unsaved-global');
  await page.locator('#personal-project').selectOption(selected.id);
  await page.getByText('插件原有任务',{exact:true}).waitFor();
  assert.equal(await page.getByText('全局任务',{exact:true}).count(),0);
  assert.equal(await page.locator('#task-form [name=id]').inputValue(),'');
  await page.locator('#export-mode').selectOption('selected_sources');
  await page.getByText('插件项目来源 · source',{exact:true}).waitFor();
  for(const [key,value] of Object.entries({id:'ui-created',title:'界面同项目新任务',version:'fixture-v1'}))await page.locator(`#task-form [name=${key}]`).fill(value);
  await page.locator('#task-form button[type=submit]').click();
  await page.getByText('界面同项目新任务',{exact:true}).waitFor();
  const mutation=calls.find((item)=>item.method==='POST');
  assert.equal(mutation.path,`/v1/scopes/${projectScope}/workflows`);
  assert.equal(mutation.body.payload.expected_revision,4);
  assert.ok(states.get(projectScope).tasks.some((task)=>task.id==='ui-created'));
  assert.ok(!states.get(`${prefix}-global`).tasks.some((task)=>task.id==='ui-created'));
  await page.locator('#personal-project').selectOption('global');await page.getByText('全局任务',{exact:true}).waitFor();
  assert.equal(await page.getByText('界面同项目新任务',{exact:true}).count(),0);
  await page.locator('#personal-project').selectOption(selected.id);await page.getByText('界面同项目新任务',{exact:true}).waitFor();
  const foreign=(await workflowProjects('personal-other',catalog)).find((item)=>item.id!=='global');
  const denied=await page.request.get(`http://127.0.0.1:${server.address().port}/api/personal/workflows?project=${foreign.id}`);assert.equal(denied.status(),403);
  assert.equal(calls.filter((item)=>item.path.includes('personal-other')&&!item.path.includes('?')).length,0);
  assert.deepEqual(await page.evaluate(()=>({local:localStorage.length,session:sessionStorage.length})),{local:0,session:0});
  assert.ok(!(await page.content()).includes('server-only-fixture'));
  if(process.env.WORKFLOW_SCREENSHOT_DIR){await mkdir(process.env.WORKFLOW_SCREENSHOT_DIR,{recursive:true});await page.screenshot({path:join(process.env.WORKFLOW_SCREENSHOT_DIR,'personal-workflow-project-desktop.png'),fullPage:true});}
  await page.setViewportSize({width:390,height:844});assert.ok(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth+1));
  if(process.env.WORKFLOW_SCREENSHOT_DIR)await page.screenshot({path:join(process.env.WORKFLOW_SCREENSHOT_DIR,'personal-workflow-project-mobile.png'),fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({browser:'chromium',bff:'production_handler',upstream:'synthetic_authenticated_scope_catalog_and_workflow_state',project_scope_matches_plugin:true,default_global:true,cross_account_selector_denied:true,switch_clears_prior_state:true,mutation_scope_and_sources:'matched',mobile:'passed',page_errors:errors.length}));
}finally{await browser.close();server.close();}
