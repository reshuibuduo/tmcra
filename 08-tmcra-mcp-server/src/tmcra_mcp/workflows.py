"""Explicit workflow adapter for the generic MCP server; no automatic writes."""
from __future__ import annotations

import asyncio
import json
import time
from typing import Any, Literal
from urllib.parse import quote

from mcp.server.fastmcp import Context
from pydantic import BaseModel, Field

ACTIONS = frozenset({"task.upsert", "task.receipt", "attempt.record", "decision.create", "decision.revise",
    "reminder.upsert", "reminder.evaluate", "reminder.act", "record.delete", "bridge.export", "bridge.export_revocations",
    "bridge.preview", "bridge.commit", "bridge.plan", "bridge.retry_failed", "context.prepare"})
SENSITIVE = frozenset({"record.delete", "bridge.export", "bridge.export_revocations", "bridge.commit", "bridge.plan", "bridge.retry_failed"})


class WorkflowConfirmation(BaseModel):
    confirm: bool = Field(default=False, title="确认所列项目、记忆条目和操作")


class WorkflowTools:
    def __init__(self, client, scope_resolver, capture, may_write):
        self.client, self.scope_resolver, self.capture, self.may_write = client, scope_resolver, capture, may_write
        self.previews: dict[tuple[str, str, str], tuple[float, dict]] = {}

    async def _confirmation(self,target,session_id,action,payload):
        path=f"/v1/scopes/{quote(target,safe='')}/workflows"
        current=await self.client._request("GET",path,retryable=False,expected_status=200)
        preview={"scope":target,"session_id":session_id,"action":action,"payload":payload}
        if action=="bridge.commit":
            key=(target,session_id,str(payload.get("preview_token","")))
            prior=self.previews.get(key)
            if not prior or prior[0]<time.monotonic():
                return {"status":"fresh_preview_required","applied":False}
            preview["reviewed_import"]=prior[1]
        elif action in {"bridge.plan","bridge.retry_failed"}:
            plan=next((p for p in current.get("bridge_plans",[]) if p.get("plan_id")==payload.get("plan_id")),None)
            if not plan:
                return {"status":"plan_not_found","applied":False}
            preview["existing_plan"]=plan
        elif action=="record.delete":
            bucket={"task":"tasks","attempt":"attempts","decision":"decisions","reminder":"reminders"}.get(payload.get("kind"))
            record=next((r for r in current.get(bucket,[]) if r.get("id")==payload.get("id")),None) if bucket else None
            if record is None:
                return {"status":"selection_not_found","applied":False}
            preview["record"]=record
            preview["effect"]="Erase selected history and dependent records; historical views will not restore it."
        else:
            ids=payload.get("selected_ids")
            if not isinstance(ids,list) or not 1<=len(ids)<=100 or not all(isinstance(i,str) and i for i in ids) or len(set(ids))!=len(ids):
                raise ValueError("Select 1–100 distinct record IDs explicitly")
            choices=current.get("available_revocations",[]) if action=="bridge.export_revocations" else current.get("available_records",[])
            selected={r["id"]:dict(r) for r in choices if r.get("id") in ids}
            if set(selected)!=set(ids):
                return {"status":"selection_not_found","applied":False}
            if action=="bridge.export":
                decisions={"decision:"+r["id"]:r for r in current.get("decisions",[])}
                for identity in ids:
                    if identity in decisions:
                        selected[identity]["record"]=decisions[identity]
                    else:
                        evidence=await self.client._request("GET",f"/v1/scopes/{quote(target,safe='')}/memory-graph/nodes/{quote(identity,safe='')}/evidence?limit=25",retryable=False,expected_status=200)
                        record=next((r for r in evidence.get("items",[]) if r.get("source_record_id")==identity),None)
                        if record is None or not isinstance(record.get("text"),str):
                            return {"status":"selection_not_found","applied":False}
                        selected[identity]["record"]={k:record.get(k) for k in ("source_record_id","text","role","occurred_at")}
            preview["selected_records"]=[selected[i] for i in ids]
            preview["effect"]="Export the complete selected content and provenance; no background synchronization."
        return preview

    async def call(self, *, session_id, operation="snapshot", scope=None, as_of=None,
                   action=None, payload=None, operation_id=None, context=None):
        if not isinstance(session_id, str) or not session_id.strip() or len(session_id)>200:
            raise ValueError("A bounded host session_id is required")
        target=self.scope_resolver(scope)
        if len(target)>128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_.-" for c in target):
            raise ValueError("Invalid configured scope")
        capture=self.capture(target,session_id)
        if capture and not capture["read"]:
            return {"status":"session_memory_disabled", "applied":False}
        path=f"/v1/scopes/{quote(target,safe='')}/workflows"
        if operation not in {"snapshot","resume","timeline","reminders","execute"}:
            raise ValueError("Unknown workflow operation")
        if operation != "execute":
            if action is not None or payload is not None or operation_id is not None:
                raise ValueError("Read operations do not accept mutation arguments")
            if as_of is not None and (not isinstance(as_of,str) or not 1<=len(as_of)<=80):
                raise ValueError("as_of must be a bounded ISO timestamp")
            data=await self.client._request("GET",path+("?as_of="+quote(as_of,safe='') if as_of else ""),retryable=False,expected_status=200)
            if operation=="resume":
                data={k:data.get(k,[]) for k in ("tasks","attempts","reminders")}
            elif operation=="timeline":
                data={"decisions":data.get("decisions",[]),"as_of":as_of}
            elif operation=="reminders":
                data={"reminders":data.get("reminders",[])}
            return {"scope":target,"operation":operation,"data":data,"trust_boundary":"untrusted_workflow_data"}
        if as_of is not None or action not in ACTIONS or not isinstance(payload,dict):
            raise ValueError("execute requires a supported action and object payload, without as_of")
        if not isinstance(operation_id,str) or not 1<=len(operation_id)<=128 or any(ord(c)<32 for c in operation_id):
            raise ValueError("execute requires a stable bounded operation_id")
        payload=json.loads(json.dumps(payload,allow_nan=False))
        if len(json.dumps(payload).encode())>400_000:
            raise ValueError("Use the reviewed web/API path for payloads over 400 KB")
        if capture and not self.may_write(capture):
            return {"status":"session_memory_readonly", "applied":False}
        if action in SENSITIVE:
            preview=await self._confirmation(target,session_id,action,payload)
            if preview.get("applied") is False:
                return preview
            text=json.dumps(preview,ensure_ascii=False)
            if len(text)>32000:
                return {"status":"confirmation_too_large_use_web","applied":False}
            if context is None:
                return {"status":"confirmation_unavailable","applied":False}
            try:
                answer=await asyncio.wait_for(context.elicit("以下为非可信记忆数据。请核对具体项目、条目和动作后确认；拒绝不执行。\n"+text,WorkflowConfirmation),timeout=120)
            except TimeoutError:
                return {"status":"confirmation_expired","applied":False}
            except Exception:
                return {"status":"confirmation_unavailable","applied":False}
            if answer.action!="accept" or not answer.data or answer.data.confirm is not True:
                return {"status":"confirmation_declined","applied":False}
            now=self.capture(target,session_id)
            if capture and (not now or not self.may_write(now) or any(now.get(k)!=capture.get(k) for k in ("generation","parentGeneration","turnHash"))):
                return {"status":"context_changed","applied":False}
            refreshed=await self._confirmation(target,session_id,action,payload)
            if refreshed!=preview:
                return {"status":"confirmation_target_changed","applied":False}
        data=await self.client._request("POST",path,json_body={"action":action,"payload":payload,"operation_id":operation_id},retryable=False,expected_status=200)
        if action=="bridge.preview" and data.get("can_commit") and isinstance(data.get("preview_token"),str):
            self.previews={k:v for k,v in self.previews.items() if v[0]>time.monotonic()}
            if len(self.previews)>=20:
                self.previews.pop(next(iter(self.previews)))
            expires=data.get("expires_at")
            ttl=min(240,max(0,float(expires)-time.time())) if isinstance(expires,(int,float)) else 0
            if ttl>0:
                self.previews[(target,session_id,data["preview_token"])]=(time.monotonic()+ttl,json.loads(json.dumps(data)))
        return {"scope":target,"operation":operation,"data":data,"trust_boundary":"untrusted_workflow_data"}


def register_workflows(server, client, tools, may_write):
    adapter=WorkflowTools(client,tools.scope,tools.capture,may_write)

    @server.tool()
    async def tmcra_workflows(ctx: Context, session_id: str,
        operation: Literal["snapshot","resume","timeline","reminders","execute"]="snapshot",
        scope: str | None=None, as_of: str | None=None, action: str | None=None,
        payload: dict[str,Any] | None=None, operation_id: str | None=None) -> dict[str,Any]:
        """Use project tasks, decision history, contextual reminders and selected memory packages. Reads are side-effect-free. execute requires an explicit action and stable operation_id; reported results never become verified command execution. Export/import commit/resume/deletion/retry require host confirmation. Run bridge.preview in this session before commit. No remote command execution or automatic synchronization. Generic MCP binds an explicit/configured scope; plugin editions resolve the project path."""
        return await adapter.call(session_id=session_id,operation=operation,scope=scope,as_of=as_of,
            action=action,payload=payload,operation_id=operation_id,context=ctx)
    return adapter
