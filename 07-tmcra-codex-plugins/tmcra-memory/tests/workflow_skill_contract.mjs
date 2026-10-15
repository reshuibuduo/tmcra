import assert from 'node:assert/strict';
import { readFile, mkdtemp, mkdir, copyFile, rm } from 'node:fs/promises';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
const root=resolve(dirname(fileURLToPath(import.meta.url)),'..');
const codex=JSON.parse(await readFile(join(root,'.codex-plugin/plugin.json')));
const claude=JSON.parse(await readFile(join(root,'.claude-plugin/plugin.json')));
assert.equal(codex.skills,'./skills/');
assert.equal(claude.name,'tmcra-memory');
const required=['skills/tmcra-workflows/SKILL.md','skills/tmcra-workflows/agents/openai.yaml','skills/tmcra-workflows/references/api.md','docs/workflow-mcp-api.zh-CN.md','scripts/workflow_client.mjs','scripts/workflow_cli.mjs'];
const release=await readFile(join(root,'scripts/build_release.ps1'),'utf8');
const entries=[...release.matchAll(/Source = "([^"]+)"; Archive = "([^"]+)"/gu)].map(m=>({source:m[1],target:m[2]}));
const temp=await mkdtemp(join(tmpdir(),'tmcra-full-workflow-package-'));
try {
 for(const path of required){
  const e=entries.find(e=>e.source===path);assert.ok(e,`release missing ${path}`);
  const destination=join(temp,e.target);await mkdir(dirname(destination),{recursive:true});await copyFile(join(root,path),destination);
  assert.deepEqual(await readFile(destination),await readFile(join(root,path)));
 }
 const skill=await readFile(join(temp,'plugins/tmcra-memory/skills/tmcra-workflows/SKILL.md'),'utf8');
 assert.match(skill,/name: tmcra-workflows/u);assert.match(skill,/Never use a shell fallback/u);
 assert.doesNotMatch(await readFile(join(root,'skills/manage-tmcra-memory/SKILL.md'),'utf8'),/current MCP toolset does not yet expose those operations/u);
 console.log(JSON.stringify({ok:true,packagedFiles:6,codexSkillDiscovery:true,claudeNamespace:'tmcra-memory:tmcra-workflows',hostInstallationTested:false}));
} finally {await rm(temp,{recursive:true,force:true});}
