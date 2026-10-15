#!/usr/bin/env node
/** Human/local auxiliary client. This does not manufacture host approval. */
import { readFile } from 'node:fs/promises';
import { createInterface } from 'node:readline/promises';
import { createWorkflowClient, publicWorkflowError, WorkflowClientError } from './workflow_client.mjs';

const HELP = `TMCRA current-project workflows
  node scripts/workflow_cli.mjs status|resume|timeline|reminders [--as-of ISO]
  node scripts/workflow_cli.mjs request --input FILE|- [--confirm]
  Common: --session-id ID --project-path PATH --project-id ID

request input: {"action":"task.upsert","payload":{"id":"t1","title":"Review"},"operation_id":"unique-logical-request"}
Uses existing private TMCRA config and the same project binding as hooks.
status/resume/timeline/reminders only read; they never run commands or submit jobs.
Public receipts remain reported, not verified execution.
--confirm only opens a real human TTY prompt for sensitive actions; it is not consent.
Sensitive MCP cancellation/unavailability must not be bypassed by this CLI.
bridge.commit needs a real preview in the same MCP process; use MCP or the workflow page.
`;
function parse(argv) {
  if (!argv.length || argv.includes('--help') || argv.includes('-h')) return null;
  const [command, ...rest] = argv;
  if (!['status','resume','timeline','reminders','request'].includes(command)) throw new WorkflowClientError('invalid_workflow_request');
  const values = {};
  for (let i=0; i<rest.length; i++) {
    const key = rest[i];
    if (key === '--confirm') { if (values.confirm) throw new WorkflowClientError('invalid_workflow_request'); values.confirm=true; continue; }
    if (!['--input','--session-id','--project-path','--project-id','--as-of'].includes(key) || values[key] !== undefined || !rest[i+1] || rest[i+1].startsWith('--')) throw new WorkflowClientError('invalid_workflow_request');
    values[key] = rest[++i];
  }
  if ((command === 'request') !== Boolean(values['--input'])) throw new WorkflowClientError('invalid_workflow_request');
  return { command, values };
}
try {
  const parsed = parse(process.argv.slice(2));
  if (!parsed) process.stdout.write(HELP);
  else {
    const {command,values} = parsed;
    let input = {};
    if (values['--input']) {
      let bytes;
      if (values['--input'] === '-') { const chunks=[]; let total=0; for await (const chunk of process.stdin) { total+=chunk.length; if (total>2*1024*1024) throw new WorkflowClientError('invalid_workflow_request'); chunks.push(chunk); } bytes=Buffer.concat(chunks); }
      else bytes=await readFile(values['--input']);
      if (bytes.length > 2*1024*1024) throw new WorkflowClientError('invalid_workflow_request');
      input = JSON.parse(bytes.toString('utf8'));
      if (!input || Array.isArray(input) || typeof input !== 'object' || Object.keys(input).some(k=>!['action','payload','operation_id'].includes(k))) throw new WorkflowClientError('invalid_workflow_request');
    }
    const invoke = createWorkflowClient({ confirm: async message => {
      if (!values.confirm || !process.stdin.isTTY || !process.stderr.isTTY || values['--input'] === '-') return 'confirmation_unavailable';
      process.stderr.write(message + '\n');
      const terminal = createInterface({input:process.stdin,output:process.stderr});
      try { const answer = await terminal.question('输入 yes 确认这一次操作，其他输入取消: '); return answer.trim() === 'yes' ? 'accepted' : 'declined'; }
      finally { terminal.close(); }
    }});
    const result = await invoke({ ...input, session_id:values['--session-id'] || process.env.CODEX_THREAD_ID || process.env.CLAUDE_SESSION_ID || 'human-cli', operation:command==='status'?'snapshot':command==='request'?'execute':command,
      ...(values['--project-path']?{project_path:values['--project-path']}:{}), ...(values['--project-id']?{project_id:values['--project-id']}:{}), ...(values['--as-of']?{as_of:values['--as-of']}:{}), });
    process.stdout.write(JSON.stringify(result)+'\n');
  }
} catch (error) {
  process.stderr.write(JSON.stringify(publicWorkflowError(error))+'\n');
  process.exitCode = 1;
}
