/** JSON values returned by the service. Evidence and job results are intentionally open-ended. */
export type JsonValue =
  | string
  | number
  | boolean
  | null
  | JsonValue[]
  | { [key: string]: JsonValue };

export type Timestamp = number;
export type MemoryRole = "user" | "assistant" | "system" | "tool";
export type Consistency = "eventual" | "read_your_writes";
export type SlowPolicy = "auto" | "deferred" | "force";
export type EvidenceMode = "raw" | "auto" | "compiled";
export type RecallProfile = "quality" | "interactive";
export type MemoryGraphLayer = "slow" | "fast" | "source";
export type JobStatus = "pending" | "running" | "succeeded" | "failed" | "cancelled";

export interface MemoryMessage {
  message_id: string;
  role: MemoryRole;
  content: string;
  /** ISO-8601 is the wire format accepted by FastAPI/Pydantic. */
  timestamp: string | Date;
  /** Immutable actor/source attribution preserved with this individual message. */
  metadata?: Record<string, JsonValue>;
}

export interface IngestRequest {
  session_id: string;
  messages: readonly MemoryMessage[];
  consistency?: Consistency;
  slow_policy?: SlowPolicy;
  metadata?: Record<string, JsonValue>;
}

export interface BulkIngestItem extends IngestRequest {
  idempotency_key: string;
}

export interface BulkIngestRequest {
  items: readonly BulkIngestItem[];
}

export interface BulkIngestResponse {
  scope_name: string;
  jobs: JobView[];
}

export interface ScopeTokenCreateRequest {
  label: string;
  subject?: string;
  permissions: readonly string[];
  scope_names?: readonly string[];
  scope_prefixes?: readonly string[];
  expires_in_seconds: number;
  provisional_delivery_seconds?: number;
}

export interface ScopeTokenView {
  token_id: string;
  tenant_id: string;
  permissions: string[];
  scope_names: string[];
  scope_prefixes: string[];
  label: string;
  subject: string | null;
  created_by_key_id: string | null;
  created_at: number;
  expires_at: number;
  revoked_at: number | null;
  last_used_at: number | null;
}

export interface IssuedScopeToken extends ScopeTokenView {
  access_token: string;
}

export interface RetentionPolicyRequest {
  enabled: boolean;
  inactive_days: number;
}

export interface RetentionPolicy {
  scope_name: string;
  enabled: boolean;
  inactive_days: number;
  created_at: number | null;
  updated_at: number | null;
}

export type FeedbackRating = "helpful" | "incorrect" | "stale" | "unsafe" | "missing";

export interface FeedbackRequest {
  query_id?: string;
  rating: FeedbackRating;
  memory_ids?: readonly string[];
  comment?: string;
  metadata?: Record<string, JsonValue>;
}

export interface FeedbackResponse {
  feedback_id: string;
  scope_name: string;
  rating: FeedbackRating | string;
  created_at: number;
}

export type WebhookEvent =
  | "job.succeeded"
  | "job.failed"
  | "job.cancelled"
  | "ingest.completed"
  | "consolidation.completed"
  | "index.completed"
  | "export.ready"
  | "scope.deleted";

export interface WebhookCreateRequest {
  label: string;
  url: string;
  events: readonly WebhookEvent[];
}

export interface WebhookView {
  endpoint_id: string;
  label: string;
  url: string;
  events: string[];
  enabled: boolean;
  created_at: number;
  updated_at: number | null;
}

export interface IssuedWebhook extends WebhookView {
  signing_secret: string;
}

export interface ScopeLifecycle {
  scope_name: string;
  state: "active" | "deleting" | "deleted";
}

export interface ScopeCatalogView {
  scope_name: string;
  created_at: number;
  last_seen_at: number;
  session_count: number;
  ingest_request_count: number;
  recall_request_count: number;
  message_count: number;
  last_ingest_at: number | null;
  last_recall_at: number | null;
}

export interface ScopeSessionView {
  session_id: string;
  created_at: number;
  last_ingest_at: number;
  ingest_request_count: number;
  message_count: number;
}

export interface ScopeSummaryView {
  scope: ScopeCatalogView;
  sessions: ScopeSessionView[];
}

export interface SessionScopeRestrictionsView {
  unrestricted: boolean;
  names: string[];
  prefixes: string[];
}

export interface SessionCredentialView {
  type: "api_key" | "scope_token";
  tenant_id: string;
  principal: string;
  permissions: string[];
  scope_restrictions: SessionScopeRestrictionsView;
  subject: string | null;
  expires_at: number | null;
}

export interface SessionServiceView {
  name: "tmcra-memory" | string;
  version: string;
  capabilities: string[];
}

export interface AuthenticatedSessionView {
  ok: true | boolean;
  authenticated: true | boolean;
  service: SessionServiceView;
  credential: SessionCredentialView;
}

export interface QuotaMetricView {
  used: number;
  limit: number | null;
  remaining: number | null;
}

export interface BillingQuotaGroupView {
  group_id: string;
  display_name: string;
  status: "active" | "suspended" | "cancelled";
  period_id: string;
  period_status: "scheduled" | "active" | "expired" | "cancelled";
  billing_interval: "monthly" | "yearly" | "custom";
  starts_at: number;
  ends_at: number;
  max_members: number;
  currency: string;
  price_minor_units: number | null;
}

export interface QuotaView {
  tenant_id: string;
  principal: string;
  plan: "pilot" | string;
  plan_version: string | null;
  billing_group: BillingQuotaGroupView | null;
  ingest_raw_tokens: QuotaMetricView;
  recall_requests: QuotaMetricView;
  member_usage: Record<string, Record<string, number>>;
}

export interface BillingProfileView {
  tenant_id: string;
  subject: string | null;
  consumer_principal: string;
  quota_principal: string;
  membership: Record<string, unknown> | null;
  quota: QuotaView;
}

export interface EntitlementUpdateRequest {
  ingest_raw_tokens: number | null;
  recall_requests: number | null;
}

export interface RecallRequest {
  query: string;
  query_time?: string | Date;
  evidence_mode?: EvidenceMode;
  recall_profile?: RecallProfile;
  response_projection?: "full" | "prompt_only";
  max_windows?: 8;
  wait_for_job_id?: string;
  debug?: boolean;
}

export interface ConsistencyContract {
  mode: Consistency;
  visible_after_job_id: string;
  recall_wait_for_job_id: string | null;
}

export interface JobView {
  job_id: string;
  tenant_id: string;
  scope_name: string;
  job_type: string;
  status: JobStatus | string;
  attempts: number;
  created_at: Timestamp;
  updated_at: Timestamp;
  started_at: Timestamp | null;
  finished_at: Timestamp | null;
  heartbeat_at: Timestamp | null;
  lease_expires_at: Timestamp | null;
  result: JsonValue | null;
  error: JsonValue | null;
  status_url: string;
  idempotent_replay?: boolean;
  consistency_contract?: ConsistencyContract;
  idempotent_retry?: boolean;
  resume_mode?: "committed_writer" | "reindex" | string;
}

export interface EvidenceRoute {
  requested: EvidenceMode;
  selected: string;
  reasons: string[];
  [key: string]: JsonValue;
}

export interface RecallResponse {
  query_id: string;
  scope_name: string;
  index_job_id: string;
  evidence_route: EvidenceRoute;
  evidence: JsonValue;
  prompt_evidence: JsonValue;
  debug: JsonValue | null;
}

/** Watermarks exposed by the service, when the selected route provides them. */
export interface WatermarkView {
  sourceEventSeq: number | null;
  promotedEventSeq: number | null;
  indexedEventSeq: number | null;
  sourceRawTokenEstimate: number | null;
  available: boolean;
}

export type ReceiptStatus = "submitted" | "pending" | "running" | "succeeded" | "failed" | "cancelled";
export type TerminalReceiptStatus = "succeeded" | "failed" | "cancelled";

/** Stable, client-side evidence receipt for one synchronous recall. */
export interface RecallReceipt {
  queryId: string;
  scopeName: string;
  indexJobId: string;
  evidenceHash: string | null;
  submittedStatus: "completed";
  finalStatus: "completed";
  submitted: true;
  final: true;
  statusUrl: string | null;
  watermarks: WatermarkView;
}

/** Stable, client-side receipt for the asynchronous ingest job. */
export interface IngestReceipt {
  scopeName: string;
  messageIds: readonly string[];
  jobId: string | null;
  submittedStatus: "submitted";
  observedStatus: JobStatus | string;
  finalStatus: TerminalReceiptStatus | null;
  submitted: true;
  final: boolean;
  statusUrl: string | null;
  watermarks: WatermarkView;
}

/** One complete automatic lifecycle turn, including both recall and write facts. */
export interface LifecycleTurnReceipt {
  turnIdempotencyKey: string;
  sessionId: string;
  recalls: readonly RecallReceipt[];
  ingest: IngestReceipt;
  messageIds: readonly string[];
  submittedStatus: "submitted";
  finalStatus: TerminalReceiptStatus | null;
  jobId: string | null;
  submitted: true;
  final: boolean;
  statusUrl: string | null;
  watermarks: WatermarkView;
}

export interface MemoryGraphNode {
  id: string;
  layer: MemoryGraphLayer;
  kind: string;
  category: string;
  label: string;
  summary: string;
  relation: string;
  state: string;
  status: string;
  confidence: number;
  salience: number;
  turn_index: number;
  occurred_at: string | null;
  subject_id: string | null;
  cluster_id: string | null;
  source_kind: string | null;
  evidence_count: number;
  visible_neighbor_count: number;
  expandable: boolean;
  attributes: Record<string, JsonValue>;
}

export interface MemoryGraphEdge {
  id: string;
  source: string;
  target: string;
  type: string;
  weight: number;
  origin: "stored" | "derived";
}

export interface MemoryGraphCounts {
  nodes: number;
  edges: number;
  slow: number;
  fast: number;
  source: number;
}

export interface MemoryGraphPage {
  limit: number;
  offset: number;
  truncated: boolean;
  next_cursor: string | null;
  returned_neighbors: number | null;
}

export interface MemoryGraphResponse {
  schema_version: string;
  scope_name: string;
  snapshot_id: string;
  view: "overview" | "neighbors" | "recall_trace";
  requested_layers: MemoryGraphLayer[];
  resolved_layers: MemoryGraphLayer[];
  fallback_layer: MemoryGraphLayer | null;
  nodes: MemoryGraphNode[];
  edges: MemoryGraphEdge[];
  counts: MemoryGraphCounts;
  page: MemoryGraphPage;
  root_id: string | null;
  depth: number | null;
  selected_memory_ids: string[];
  missing_memory_ids: string[];
}

export interface MemoryGraphEvidenceItem {
  source_record_id: string;
  relationship: string;
  session_id: string | null;
  message_id: string | null;
  role: string | null;
  occurred_at: string | null;
  text: string;
  text_sha256: string;
  source_text_verbatim: boolean;
  evidence_char_start: number | null;
  evidence_char_end: number | null;
}

export interface MemoryGraphEvidenceResponse {
  schema_version: string;
  scope_name: string;
  snapshot_id: string;
  memory_id: string;
  items: MemoryGraphEvidenceItem[];
  page: MemoryGraphPage;
}

export interface MemoryGraphTraceRequest {
  query: string;
  query_time?: string | Date;
  max_windows?: 8;
  debug?: boolean;
}

export interface MemoryGraphTraceResponse extends MemoryGraphResponse {
  query_id: string;
  index_job_id: string;
  retrieval_summary: Record<string, JsonValue>;
  debug: Record<string, JsonValue> | null;
}

export interface UsageCallTotals {
  registered_call_count: number;
  completed_call_count: number;
  failed_call_count: number;
  unknown_call_count: number;
  in_flight_call_count: number;
  unpriced_completed_call_count: number;
  input_tokens: number;
  cache_hit_tokens: number;
  cache_miss_tokens: number;
  output_tokens: number;
  known_cost_micro_cny: number;
}

export interface UsageStageTotals {
  registered_call_count: number;
  completed_call_count: number;
  unknown_or_unpriced_call_count: number;
  input_tokens: number;
  output_tokens: number;
  known_cost_micro_cny: number;
}

export interface UsageCosts {
  tenant_id: string;
  scope_name: string | null;
  scope_prefix?: string | null;
  from_timestamp?: number | null;
  to_timestamp?: number | null;
  currency: "CNY" | string;
  ledger_coverage: "registered_calls_only" | string;
  source_ledger_coverage?: string;
  complete_for_registered_calls: boolean;
  source: {
    scope_count: number;
    ingested_raw_token_estimate: number;
    ingested_user_turns: number;
    source_event_count: number;
  };
  calls: UsageCallTotals;
  known_cost_cny: number;
  known_model_api_cny_per_million_ingested_raw_tokens: number | null;
  uncertain_cost_call_count: number;
  by_stage: Record<string, UsageStageTotals>;
  quota_events?: { ingest_raw_tokens: number; recall_requests: number };
  quota_event_scope_coverage?: Record<string, string>;
  attribution_coverage?: Record<string, {
    provider_call_count: number;
    usage_event_count: number;
    ingest_raw_tokens: number;
    recall_requests: number;
    known_cost_micro_cny: number;
  }>;
  group_by?: string | null;
  buckets?: Array<UsageCallTotals & {
    key: string;
    ingest_raw_tokens: number;
    recall_requests: number;
    known_cost_cny: number;
  }>;
}

export interface HealthResponse {
  status: "ok" | string;
  service: string;
  version: string;
}

export interface ReadinessResponse extends HealthResponse {
  status: "ready" | "not_ready" | string;
  checks: Record<string, boolean>;
  worker_alive: boolean;
  adapter_compatibility: Record<string, boolean>;
  online_engine_loaded: boolean;
}

export function isTerminalJobStatus(status: string): boolean {
  return status === "succeeded" || status === "failed" || status === "cancelled";
}
