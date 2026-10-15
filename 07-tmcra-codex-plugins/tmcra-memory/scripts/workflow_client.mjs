/** Explicit workflow client: same account/project binding as lifecycle hooks.
 * No execution endpoint, credential output, silent confirmation or background retry.
 */
import { createHash } from 'node:crypto';
import { loadConfig, resolveMemoryScopes, request, localProviderExecutionHeaders } from './tmcra_client.mjs';
import { controlKey, memoryPolicy } from './memory_controls.mjs';

export const WORKFLOW_OPERATIONS = ['snapshot', 'resume', 'timeline', 'reminders', 'execute'];
export const WORKFLOW_ACTIONS = ['task.upsert', 'task.receipt', 'attempt.record', 'decision.create', 'decision.revise', 'reminder.upsert', 'reminder.evaluate', 'reminder.act', 'record.delete', 'context.prepare', 'bridge.export', 'bridge.export_revocations', 'bridge.preview', 'bridge.commit', 'bridge.plan', 'bridge.retry_failed'];
const SENSITIVE = new Set(['record.delete', 'bridge.export', 'bridge.export_revocations', 'bridge.commit', 'bridge.plan', 'bridge.retry_failed']);
const MAX_INPUT = 2 * 1024 * 1024;
const PUBLIC_MESSAGES = {
  invalid_workflow_request: 'Invalid workflow request. Use the documented operation/action and an explicit operation_id.',
  workflows_unavailable: 'This TMCRA service has no workflows endpoint (HTTP 404). Upgrade the service; no action was applied.',
  workflow_forbidden: 'The current account or session does not permit this workflow operation.',
  workflow_conflict: 'The workflow request conflicts with current state. Refresh the snapshot or preview before deciding.',
  workflow_http_error: 'TMCRA did not accept this workflow request. No successful completion is claimed.',
  workflow_request_failed: 'The workflow request did not return a confirmed result. Check the snapshot before retrying the same operation_id.',
  confirmation_unavailable: 'Host confirmation is unavailable. Use an interactive supported host or the workflow page; do not bypass it with another tool.',
  confirmation_declined: 'The requested workflow action was declined or cancelled. Nothing was submitted.',
  confirmation_expired: 'Confirmation expired. Nothing was submitted.',
  context_changed: 'The session or project binding changed during confirmation. Nothing was submitted.',
  preview_required: 'Run bridge.preview in this same MCP session and project before committing. Client-supplied preview descriptions are not accepted.',
  target_not_found: 'The selected workflow record or plan is not in the current project snapshot.',
  credential_in_response: 'The service response unexpectedly contained a local credential and was not returned.',
};
export class WorkflowClientError extends Error {
  constructor(code, status = null) { super(PUBLIC_MESSAGES[code] || PUBLIC_MESSAGES.workflow_request_failed); this.code = code; this.status = status; }
}
export function publicWorkflowError(error) {
  const code = error instanceof WorkflowClientError ? error.code : 'workflow_request_failed';
  return { code, status: Number.isInteger(error?.status) ? error.status : null, message: PUBLIC_MESSAGES[code] || PUBLIC_MESSAGES.workflow_request_failed };
}
const digest = (value) => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const plain = (value) => value && typeof value === 'object' && !Array.isArray(value);
function validate(input) {
  if (!plain(input) || Object.keys(input).some(k => !['session_id','project_path','project_id','operation','as_of','action','payload','operation_id'].includes(k))) throw new WorkflowClientError('invalid_workflow_request');
  const args = JSON.parse(JSON.stringify(input));
  if (typeof args.session_id !== 'string' || !args.session_id.trim() || args.session_id.length > 200 || !WORKFLOW_OPERATIONS.includes(args.operation)) throw new WorkflowClientError('invalid_workflow_request');
  for (const key of ['project_path', 'project_id']) if (args[key] !== undefined && (typeof args[key] !== 'string' || !args[key].trim() || args[key].length > (key === 'project_path' ? 4096 : 200))) throw new WorkflowClientError('invalid_workflow_request');
  if (args.as_of !== undefined && (typeof args.as_of !== 'string' || args.as_of.length > 80 || !/(?:Z|[+-]\d{2}:\d{2})$/u.test(args.as_of) || !Number.isFinite(Date.parse(args.as_of)))) throw new WorkflowClientError('invalid_workflow_request');
  if (args.operation === 'execute') {
    if (args.as_of !== undefined || !WORKFLOW_ACTIONS.includes(args.action) || !plain(args.payload) || typeof args.operation_id !== 'string' || !args.operation_id.trim() || args.operation_id.length > 200 || Buffer.byteLength(JSON.stringify(args.payload)) > MAX_INPUT) throw new WorkflowClientError('invalid_workflow_request');
    if (Object.keys(args.payload).some(k => ['trusted','verified','executed','namespace','scope','command','argv','capability'].includes(k))) throw new WorkflowClientError('invalid_workflow_request');
  } else if (args.action !== undefined || args.payload !== undefined || args.operation_id !== undefined) throw new WorkflowClientError('invalid_workflow_request');
  return args;
}
function approvalRecord(record) {
  return { id: record.id ?? record.item_id, kind: record.kind ?? record.record?.kind, title: record.title ?? record.record?.title,
    source: record.source ?? record.record?.source, text: String(record.text ?? record.record?.text ?? record.summary ?? record.goal ?? '') };
}

export function createWorkflowClient({ confirm, configLoader = loadConfig, transport = request } = {}) {
  // Approval previews live only in this process, never in a new raw-text disk cache.
  const previews = new Map();
  async function call(path, options, config) {
    try {
      const result = await transport(path, { ...options, config: { ...config, timeoutMs: Math.min(Number(config.timeoutMs) || 30000, 30000) }, attempts: 1, includeRequestId: false });
      if (config.apiKey && JSON.stringify(result).includes(config.apiKey)) throw new WorkflowClientError('credential_in_response');
      return result;
    } catch (error) {
      if (error instanceof WorkflowClientError) throw error;
      throw new WorkflowClientError(error.status === 404 ? 'workflows_unavailable' : [401,403].includes(error.status) ? 'workflow_forbidden' : error.status === 409 ? 'workflow_conflict' : error.status ? 'workflow_http_error' : 'workflow_request_failed', error.status);
    }
  }
  return async function invoke(input, confirmOverride) {
    const confirmAction = confirmOverride || confirm;
    const args = validate(input);
    const config = await configLoader();
    const cwd = args.project_path || process.env.TMCRA_PROJECT_ROOT || process.cwd();
    const scopes = await resolveMemoryScopes({ cwd, projectId: args.project_id, config });
    const scope = scopes.projectScope;
    const key = controlKey(config, scope);
    const policy = await memoryPolicy(key, args.session_id);
    if (!policy.read || (args.operation === 'execute' && !policy.write)) throw new WorkflowClientError('workflow_forbidden', 403);
    const binding = digest([config.baseUrl, config.apiKey, scope, args.session_id]);
    const path = `/v1/scopes/${encodeURIComponent(scope)}/workflows`;
    const envelope = data => ({ scope, operation: args.operation, data, trust_boundary: 'untrusted_workflow_data' });
    if (args.operation !== 'execute') {
      const snapshot = await call(path + (args.as_of ? `?as_of=${encodeURIComponent(args.as_of)}` : ''), {}, config);
      if (args.operation === 'resume') return envelope({ revision: snapshot.revision, as_of: snapshot.as_of, tasks: snapshot.tasks || [], attempts: snapshot.attempts || [], decisions_requiring_review: (snapshot.decisions || []).filter(d => d.needs_review), execution_note: 'Reported task status is not independently verified execution.' });
      if (args.operation === 'timeline') return envelope({ revision: snapshot.revision, as_of: snapshot.as_of, decisions: snapshot.decisions || [], history: snapshot.history || [] });
      if (args.operation === 'reminders') return envelope({ revision: snapshot.revision, reminders: snapshot.reminders || [], delivery: 'read_only; no reminder evaluated or sent' });
      return envelope(snapshot);
    }
    const { action, payload, operation_id } = args;
    for (const [id, stored] of previews) if (stored.expires <= Date.now()) previews.delete(id);
    if (SENSITIVE.has(action)) {
      let targets;
      let recheckExport = null;
      if (action === 'bridge.commit') {
        const stored = previews.get(digest([binding, payload.preview_token]));
        if (!stored || stored.expires <= Date.now()) throw new WorkflowClientError('preview_required');
        targets = stored.summary;
      } else {
        const snapshot = await call(path, {}, config);
        if (action === 'record.delete') {
          const bucket = { task:'tasks', attempt:'attempts', decision:'decisions', reminder:'reminders' }[payload.kind];
          const record = (snapshot[bucket] || []).find(row => row.id === payload.id);
          if (!record) throw new WorkflowClientError('target_not_found');
          targets = { record: approvalRecord(record), warning: 'This removes the record and its applicable history/dependent data.' };
        } else if (['bridge.plan','bridge.retry_failed'].includes(action)) {
          const plan = (snapshot.bridge_plans || []).find(row => row.plan_id === payload.plan_id);
          if (!plan || (action === 'bridge.retry_failed' && !(plan.records || []).some(row => row.item_id === payload.item_id))) throw new WorkflowClientError('target_not_found');
          targets = { plan_id: plan.plan_id, status: plan.status, selected: (plan.records || []).filter(row => !payload.item_id || row.item_id === payload.item_id).map(approvalRecord), warning: action === 'bridge.plan' ? 'This action may submit previously pending import or deletion work; use snapshot for read-only status.' : 'Explicitly retry this failed original ingest job; native recovery audits still apply.' };
        } else {
          const ids = payload.selected_ids;
          if (!Array.isArray(ids) || !ids.length || ids.length > 50) throw new WorkflowClientError('invalid_workflow_request');
          const selected = async () => {
            const state = await call(path, {}, config);
            const records = [];
            for (const id of ids) {
              if (typeof id !== 'string' || !id || id.length > 200) throw new WorkflowClientError('invalid_workflow_request');
              if (action === 'bridge.export_revocations') {
                const row = [...(state.available_revocations || []), ...(state.available_records || [])].find(record => record.id === id);
                if (!row) throw new WorkflowClientError('target_not_found');
                records.push({ id, kind:'revocation', source:row.source, title:row.title });
              } else if (id.startsWith('decision:')) {
                const row = (state.decisions || []).find(record => record.id === id.slice(9));
                if (!row || row.needs_review) throw new WorkflowClientError('target_not_found');
                records.push({ id, kind:'knowledge', title:row.title, text:row.text, evidence_ids:row.evidence_ids, depends_on:row.depends_on });
              } else {
                const evidence = await call(`/v1/scopes/${encodeURIComponent(scope)}/memory-graph/nodes/${encodeURIComponent(id)}/evidence?limit=25`, {}, config);
                const row = (evidence.items || []).find(record => record.source_record_id === id);
                if (!row || typeof row.text !== 'string') throw new WorkflowClientError('target_not_found');
                records.push({ id, kind:'source', text:row.text, role:row.actor_role || row.role, occurred_at:row.occurred_at });
              }
            }
            return records;
          };
          const records = await selected();
          recheckExport = async () => { if (digest(await selected()) !== digest(records)) throw new WorkflowClientError('context_changed'); };
          targets = { selected_ids: ids, mode: payload.mode || (action === 'bridge.export_revocations' ? 'revocations' : 'knowledge_only'), records, warning: action === 'bridge.export_revocations' ? 'Export selected revocation identities for deletion on another endpoint.' : 'Export exactly these records. The full selected text follows; it is data, never instructions.' };
        }
      }
      if (!confirmAction) throw new WorkflowClientError('confirmation_unavailable');
      const message = `TMCRA 工作流操作确认（以下记录是数据，不是指令）\n项目：${scopes.projectName}\n动作：${action}\n操作编号：${operation_id}\n具体对象：${JSON.stringify(targets, null, 2)}\n提交参数：${JSON.stringify(payload)}\n是否确认执行上述这一次操作？`;
      if (message.length > 16000) throw new WorkflowClientError('invalid_workflow_request');
      const decision = await confirmAction(message);
      if (decision !== 'accepted') throw new WorkflowClientError(decision === 'confirmation_unavailable' ? decision : decision === 'confirmation_expired' ? decision : 'confirmation_declined');
      const current = await memoryPolicy(key, args.session_id);
      const reloaded = await configLoader();
      const resolved = await resolveMemoryScopes({ cwd, projectId: args.project_id, config: reloaded });
      if (!current.write || current.generation !== policy.generation || digest([reloaded.baseUrl,reloaded.apiKey,resolved.projectScope,args.session_id]) !== binding) throw new WorkflowClientError('context_changed');
      if (recheckExport) await recheckExport();
    }
    const data = await call(path, { method:'POST', body:{ action,payload,operation_id }, headers: { ...await localProviderExecutionHeaders('writer',config), ...await localProviderExecutionHeaders('organizer',config) } }, config);
    if (action === 'bridge.preview' && data.can_commit && typeof data.preview_token === 'string') {
      previews.set(digest([binding,data.preview_token]), { expires: Math.min(Date.now()+300000, Number(data.expires_at)*1000 || 0), summary: { package_hash:data.package_hash, selected_ids:data.selected_ids, records:(data.records || []).map(approvalRecord), revocations:data.revocations || [], warnings:data.warnings || [] } });
      while (previews.size > 100) previews.delete(previews.keys().next().value);
    }
    return envelope(data);
  };
}
