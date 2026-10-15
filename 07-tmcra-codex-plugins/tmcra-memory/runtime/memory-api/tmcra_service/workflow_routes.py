"""Scoped workflow UI/API; reuse existing auth, ingest admission and deletion jobs."""
from __future__ import annotations
import hashlib
import json
import sqlite3
from contextlib import closing
from pathlib import Path
from datetime import datetime, timezone
from typing import Any
from fastapi import Depends, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field
from .api_models import IngestRequest, MemoryMessage, MessageDeleteRequest
from .graph_projection import MemoryGraphProjection, GraphProjectionError
from .workflow_service import WorkflowService, namespace_key
from .workflows import WorkflowError
from .memory_bridge import BridgeError


class WorkflowIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: str = Field(min_length=1, max_length=64, description="task.upsert/receipt, attempt.record, decision.create/revise, reminder.upsert/evaluate/act, record.delete, bridge.export/export_revocations/preview/commit/plan/retry_failed, context.prepare. No remote execution action.")
    operation_id: str = Field(min_length=1, max_length=200, description="Caller-generated stable operation identity. Reuse the same identity and identical inputs after an uncertain result; changing inputs requires a new identity.")
    payload: dict[str, Any] = Field(default_factory=dict, description="Action-specific fields; unknown fields are rejected. Bridge imports require explicit selected_ids, a bound preview_token and a separate commit. Execution reports from this API remain unverified.")


def bind_workflows(components, tenant, scope, *, importer=None, deleter=None, authorize_records=None, retry_ingest=None):
    def projection():
        return MemoryGraphProjection.from_available_storage(components.storage, tenant_id=tenant, scope_name=scope)

    def resolve(namespace, identifier):
        if namespace != namespace_key(tenant, scope):
            return None
        effects = components.commercial.feedback_effects(tenant, scope)
        if identifier in effects:
            return None  # Suppressed/corrected sources cannot become fresh evidence.
        try:
            items = projection().evidence(identifier, limit=25)["items"]
        except GraphProjectionError as exc:
            if exc.status_code == 404 or exc.code == "graph_snapshot_unavailable":
                return None
            raise
        row = next((r for r in items if r["source_record_id"] == identifier), None)
        if not row or row.get("message_id") in effects:
            return None
        return {"id": identifier, "message_id": row.get("message_id") or identifier,
                "text": row["text"], "role": row.get("actor_role") or row.get("role") or "user", "occurred_at": row.get("occurred_at") or ""}

    def sources(namespace):
        try:
            nodes = projection().overview(layers=("source",), limit=50)["nodes"]
        except GraphProjectionError as exc:
            if exc.status_code == 404 or exc.code == "graph_snapshot_unavailable":
                return []
            raise
        output = []
        for node in nodes:
            ident = node.get("id") or node.get("memory_id")
            row = resolve(namespace, ident)
            if row:
                output.append({"id": ident, "kind": "source", "title": row["text"][:100], "role": row["role"], "occurred_at": row["occurred_at"]})
        return output

    def revision(namespace):
        if namespace != namespace_key(tenant, scope):
            raise WorkflowError("namespace_mismatch", "Project binding differs", 403)
        paths = components.storage.scope_paths(tenant, scope)
        databases = {paths.database}
        binding = None
        try:
            binding = projection().binding
            databases.add(binding.database)
        except GraphProjectionError as exc:
            if exc.code != "graph_snapshot_unavailable":
                raise
        output = {"snapshot_id": binding.snapshot_id if binding else None, "files": [], "storage_revisions": []}
        files = set(databases) | {Path(str(path) + "-wal") for path in databases} | {paths.active_index, paths.active_delta}
        for path in sorted(files):
            try:
                stat = path.stat()
            except FileNotFoundError:
                output["files"].append({"name": str(path.relative_to(paths.root)), "exists": False})
                continue
            item = {"name": str(path.relative_to(paths.root)), "size": stat.st_size, "mtime_ns": stat.st_mtime_ns, "inode": stat.st_ino}
            if path in {paths.active_index, paths.active_delta}:
                item["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
            output["files"].append(item)
        for path in sorted(databases):
            if not path.is_file():
                continue
            with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
                if db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='meta'").fetchone():
                    row = db.execute("SELECT value_json FROM meta WHERE scope_id=? AND key='storage_revision'", (paths.scope_id,)).fetchone()
                    output["storage_revisions"].append({"name": str(path.relative_to(paths.root)), "revision": row[0] if row else None})
        return output

    def receipt(namespace, value):
        job = components.jobs.get(value.get("receipt_id", ""), tenant_id=tenant)
        if not job or job.scope_name != scope:
            return None
        if job.state == "succeeded":
            # A successful job alone isn't proof the source exists in the graph.
            try:
                found = components.storage.resolve_source_memory_ids_for_messages(tenant_id=tenant, scope_name=scope, message_ids=[value["local_message_id"]])
            except Exception:
                found = []
            if found:
                try:
                    committed = MemoryGraphProjection.from_storage(components.storage, tenant_id=tenant, scope_name=scope)
                    indexed = any(any(item.get("message_id") == value["local_message_id"] for item in committed.evidence(source_id)["items"]) for source_id in found)
                except GraphProjectionError:
                    indexed = False
                if indexed:
                    return {**value, "indexed": True, "engine_state": "succeeded"}
        # Only a bounded job state crosses this adapter; provider errors may
        # contain secrets and must never be copied into workflow metadata.
        return {**value, "indexed": False, "engine_state": job.state}
    return WorkflowService(components.settings.state_dir / "workflows", resolve, sources, importer, deleter, receipt, revision_resolver=revision, authorize_records=authorize_records, retry_ingest=retry_ingest)


def revoke_deleted_session(components, tenant, scope, session_id):
    """Erase only workflows bound to a deleted session, without paginated scans.

    Both mutable source storage and the committed projection can hold identities
    cited by workflow history. Resolve internal message identities to external
    bridge IDs; no unrelated project's or session's workflow is purged.
    """
    paths = components.storage.scope_paths(tenant, scope)
    databases = {paths.database}
    try:
        databases.add(MemoryGraphProjection.from_available_storage(components.storage, tenant_id=tenant, scope_name=scope).binding.database)
    except GraphProjectionError as exc:
        if exc.code != "graph_snapshot_unavailable":
            raise
    source_ids, message_ids = set(), set()
    for path in databases:
        if not path.is_file():
            continue
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.row_factory = sqlite3.Row
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            local_message_ids = set()
            if "tmcra_service_messages" in tables:
                columns = {row[1] for row in db.execute("PRAGMA table_info(tmcra_service_messages)")}
                fields = "message_id,internal_message_id" if "internal_message_id" in columns else "message_id"
                for row in db.execute(f"SELECT {fields} FROM tmcra_service_messages WHERE scope_id=? AND session_id=?", (paths.scope_id, session_id)):
                    message_ids.add(row["message_id"])
                    local_message_ids.add(row["message_id"])
                    if "internal_message_id" in columns and row["internal_message_id"]:
                        local_message_ids.add(row["internal_message_id"])
            if "records" not in tables:
                continue
            for row in db.execute("SELECT memory_id,metadata_json FROM records WHERE scope_id=?", (paths.scope_id,)):
                metadata = json.loads(row["metadata_json"])
                if not isinstance(metadata, dict):
                    raise WorkflowError("invalid_source_metadata", "Source metadata requires repair before workflow revocation", 409)
                if metadata.get("session_id") == session_id or metadata.get("message_id") in local_message_ids:
                    source_ids.add(row["memory_id"])
    service = bind_workflows(components, tenant, scope)
    service.revoke_sources(namespace_key(tenant, scope), sorted(source_ids), sorted(message_ids))
    return {"source_count": len(source_ids), "message_count": len(message_ids)}


def install_workflow_routes(app, components, require_permission, require_active_scope, ingest, delete_messages, retry_job):
    @app.exception_handler(WorkflowError)
    async def workflow_error(_, exc):
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": str(exc)})

    @app.exception_handler(BridgeError)
    async def bridge_error(_, exc):
        return JSONResponse(status_code=409, content={"error": "bridge_rejected", "detail": str(exc)})

    @app.get("/v1/scopes/{scope_name}/workflows", tags=["memory-workflows"], operation_id="getMemoryWorkflows",
             summary="Read project tasks, decisions, reminders and import states",
             description="Requires memory:read. Optional as_of is an ISO8601 timezone-aware observation cutoff. Deleted content stays erased in historical views. Reading import states never submits or retries jobs.")
    def get_workflows(scope_name: str, as_of: str | None = Query(default=None, max_length=80), context=Depends(require_permission("memory:read"))):
        require_active_scope(context.tenant_id, scope_name)
        return bind_workflows(components, context.tenant_id, scope_name).snapshot(namespace_key(context.tenant_id, scope_name), as_of)

    @app.post("/v1/scopes/{scope_name}/workflows", tags=["memory-workflows"], operation_id="applyMemoryWorkflowAction",
              summary="Apply a bounded task, decision, reminder or selective transfer action",
              description="Requires memory:read and memory:write. Export additionally requires administrative memory:export; revocation requires administrative memory:delete. accepted_pending is not indexed. bridge.plan can resume unsubmitted records; bridge.retry_failed explicitly requests native audited retry. No action executes shell commands. See WORKFLOW_CENTER and WORKFLOW_MCP_API for action payloads.",
              dependencies=[Depends(require_permission("memory:read"))])
    def post_workflows(scope_name: str, body: WorkflowIn, request: Request, context=Depends(require_permission("memory:write"))):
        require_active_scope(context.tenant_id, scope_name)
        if body.action in {"bridge.export", "bridge.export_revocations"}:
            if "memory:export" not in context.scopes or context.credential_type != "api_key" or "memory:export" not in components.database.get_tenant_scopes(context.tenant_id):
                raise HTTPException(status_code=403, detail="Memory export requires an administrative export key")
        def authorize_records(namespace, records):
            if namespace != namespace_key(context.tenant_id, scope_name):
                raise HTTPException(status_code=403, detail="Project binding differs")
            if any(record["kind"] == "revocation" for record in records):
                if ("memory:delete" not in context.scopes or context.credential_type != "api_key"
                        or "memory:delete" not in components.database.get_tenant_scopes(context.tenant_id)):
                    raise HTTPException(status_code=403, detail="Revocation approval requires an administrative deletion key")

        def importer(namespace, entry):
            record = entry["record"]
            text = "[Imported memory: external_unverified; original role=" + record["role"] + "]\n" + record["text"]
            command = IngestRequest(session_id="memory-package-import", messages=[MemoryMessage(
                message_id=entry["local_id"], role="user", content=text,
                timestamp=datetime.fromisoformat(record["occurred_at"].replace("Z", "+00:00")) if record["occurred_at"] else datetime.now(timezone.utc),
                metadata={"import_verification": "external_unverified", "source_identity": record["source"]})])
            result = ingest(scope_name, command, request, idempotency_key="bridge-" + entry["local_id"],
                writer_execution=request.headers.get("X-TMCRA-Writer-Execution"), organizer_execution=request.headers.get("X-TMCRA-Organizer-Execution"), context=context, _={})
            return {"local_message_id": entry["local_id"], "indexed": False, "receipt_id": result["job_id"]}

        def retry_ingest(namespace, receipt, operation_id):
            if namespace != namespace_key(context.tenant_id, scope_name):
                raise HTTPException(status_code=403, detail="Project binding differs")
            prior = components.jobs.get(receipt.get("receipt_id", ""), tenant_id=context.tenant_id)
            if prior is None or prior.scope_name != scope_name:
                raise HTTPException(status_code=404, detail="Import job not found in this project")
            payload = dict(prior.payload or {})
            if payload.get("job_type") != "ingest" or not any(
                    message.get("message_id") == receipt.get("local_message_id")
                    for message in payload.get("messages", []) if isinstance(message, dict)):
                raise HTTPException(status_code=409, detail="Import receipt does not match its original ingest job")
            # Reuse native retry: it verifies current credentials, original job,
            # Source/journal audit and resumable durable Writer artifacts. Never
            # bypass that gate by creating a new job or calling resume_failed here.
            result = retry_job(prior.job_id, idempotency_key="bridge-retry-" + hashlib.sha256(operation_id.encode()).hexdigest(), context=context)
            return {"local_message_id": receipt["local_message_id"], "receipt_id": result["job_id"], "indexed": False}

        def deleter(namespace, entry):
            # Same administrative requirement as the existing message deletion API.
            if "memory:delete" not in context.scopes or context.credential_type != "api_key":
                raise HTTPException(status_code=403, detail="Revocation application requires an administrative deletion key")
            jobs, deleted = [], []
            for ident in entry.get("revoked_local_ids", []):
                try:
                    components.storage.resolve_source_memory_ids_for_messages(tenant_id=context.tenant_id, scope_name=scope_name, message_ids=[ident])
                except Exception as exc:
                    from .adapters.v4 import ContentDeletionTargetNotFound
                    if not isinstance(exc, ContentDeletionTargetNotFound):
                        raise
                    deleted.append(ident)
                    continue
                result = delete_messages(scope_name, MessageDeleteRequest(message_ids=[ident]),
                    idempotency_key="bridge-revoke-" + entry["local_id"] + "-" + ident[-16:], confirm_count=1, context=context)
                jobs.append(result["job_id"])
            return {"deleted_message_ids": deleted, "pending": jobs}

        service = bind_workflows(components, context.tenant_id, scope_name, importer=importer, deleter=deleter, authorize_records=authorize_records, retry_ingest=retry_ingest)
        return service.execute(namespace_key(context.tenant_id, scope_name), body.action, body.payload, body.operation_id)
