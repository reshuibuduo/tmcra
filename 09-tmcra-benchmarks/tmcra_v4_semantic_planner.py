"""LLM planners for evidence-independent task contracts and grounded resolution plans."""

from __future__ import annotations

import hashlib
import json
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from tmcra_v4_evidence_operations import OPERATION_TYPES
from tmcra_v4_semantic_evidence import (
    RESOLUTION_PLAN_SCHEMA,
    TASK_CONTRACT_SCHEMA,
    normalize_resolution_output,
    requires_exhaustive_resolution_review,
    resolution_payload,
    task_contract_payload,
    validate_resolution_plan,
    validate_task_contract,
)


TASK_PROMPT_VERSION = "tmcra-task-contract-2026-07-13.2"
RESOLUTION_PROMPT_VERSION = "tmcra-semantic-resolution-2026-07-13.5"
COMPLETENESS_REVIEW_PROMPT_VERSION = "tmcra-semantic-completeness-review-2026-07-13.1"

TASK_CONTRACT_PROMPT = f"""Compile one user request into an evidence-independent task contract.
You receive only the request, its date, and optionally a compact dialogue state. You do not receive memory candidates. Do not answer the request and do not predict what memory contains.

Return exactly one JSON object with schema {TASK_CONTRACT_SCHEMA}:
{{"schema_version":"{TASK_CONTRACT_SCHEMA}","target":{{"description":"requested result","subject":"entity whose state matters","relation":"relation between subject and result"}},"output":{{"shape":"scalar|list|set|count|boolean|date|duration|structured|free_text","cardinality":"one|zero_or_one|one_or_more|zero_or_more","ordering":"none|chronological|recency|ranked|question_order","unit":"unit or empty string","origin":"memory_direct|memory_derived|memory_conditioned|external_required"}},"scope":{{"temporal":"time constraint or empty string","entity":"entity constraint or empty string","context":"other scope constraint or empty string"}},"premises":[{{"premise_id":"P01","description":"one minimal fact or constraint needed to perform the request","role":"fact|operand|constraint|scope|counterevidence","necessity":"required|optional","source":"memory|query_context|model_knowledge|external_tool","context_quote":"exact request substring when source=query_context, otherwise empty string"}}],"allowed_derivations":["semantic_composition"]}}

The contract is compositional, not a question-type label or route selector.
- memory_direct means the requested payload itself should be remembered.
- memory_derived means the requested payload is computed from remembered premises.
- memory_conditioned means memory supplies constraints or background for a response whose final wording or candidates need not already exist in memory.
- external_required means a required current-world payload must come from a tool or external source.
Describe the minimum premises needed to execute the request. Do not make the final answer, recommendation, or generated text a premise when memory is only supposed to supply constraints.
Assign each premise its real source. Facts explicitly supplied by the current request or question date are query_context and need an exact context_quote. Remembered personal facts, experiences, states, and constraints are memory. General knowledge the answer model may supply is model_knowledge. Live facts requiring lookup are external_tool. Never require Source memory to prove current request text, the question date, general model knowledge, or a future tool result. A memory-conditioned contract must include at least one required memory premise that actually personalizes the response.
Allowed derivations are: {', '.join(sorted(set(OPERATION_TYPES) | {'semantic_composition', 'constraint_application'}))}.
Use no benchmark labels, dataset fields, evidence IDs, source IDs, or hidden answer assumptions."""

RESOLUTION_PROMPT = f"""Resolve one task contract against an immutable Source evidence reservoir.
Do not answer the user. Produce a grounded claim ledger, bind every contract premise, and request only deterministic operations whose operands exist.

Return exactly one JSON object with schema {RESOLUTION_PLAN_SCHEMA}:
{{"schema_version":"{RESOLUTION_PLAN_SCHEMA}","claims":[{{"claim_id":"M01","subject":"entity","predicate":"atomic relation","object":"atomic value or empty string for an intransitive event","valid_time":"ISO date/time or empty string","polarity":"positive|negative|unknown","modality":"asserted|experienced|preferred|planned|recommended|uncertain","evidence_id":"E01","source_quote":"exact contiguous substring copied from E01"}}],"bindings":[{{"premise_id":"P01","claim_ids":["M01"],"evidence_ids":["E01"],"operation_ids":[],"coverage":"complete|partial|conflicting|absent","relation":"direct|derived|constraint|context|counterevidence"}}],"operations":[{{"operation_id":"O01","operation_type":"supported operation","input_atom_ids":["D001"],"input_claim_ids":[],"input_evidence_ids":["E01"],"parameters":{{}},"output_refs":["TARGET"]}}]}}

Rules:
1. Emit only claims that are actually used by a premise binding or operation. Do not catalog, summarize, or transcribe irrelevant reservoir evidence. Every emitted claim is one atomic proposition and must contain an exact contiguous Source quote. Never paraphrase source_quote.
2. Bind every task premise whose source is memory exactly once. Do not emit bindings or Source claims for query_context, model_knowledge, or external_tool premises. Evidence IDs alone are not semantic support: complete coverage needs grounded claims or a valid operation.
3. A memory-conditioned task is complete when its required remembered constraints are grounded; the generated downstream response itself need not appear in memory.
4. Use partial when only part of a premise is supported, conflicting for unresolved incompatible claims, and absent only when no semantic support exists.
5. Keep counterevidence and historical/current distinctions explicit. Never transfer values across entities, events, activities, or time states.
6. Deterministic operations are optional and limited to: {', '.join(sorted(OPERATION_TYPES))}. Use only supplied atom, grounded claim, and evidence IDs. Do not mix atom and claim operands in one operation. For count_distinct, ordered_unique_list, latest_state, entity_exact_match, or entity_mismatch over grounded claims, set input_claim_ids and set parameters.field to subject, predicate, object, or valid_time. Every claim operand's evidence ID must appear in input_evidence_ids. Set output_refs to existing premise IDs when an operation derives a premise, and include TARGET when it derives the final requested payload. Never invent a new premise ID for a final result.
7. For a count, list, or set target, audit every Source item in scope before declaring coverage complete. Include both explicit relations and grounded state transitions expressed across nearby sentences, such as an old item being removed or donated while its successor is introduced as an upgrade. Do not infer a replacement from a merely new item without evidence of the predecessor/successor transition.
8. Do not classify by benchmark question type. Do not emit an answer, summary, gold field, or outside knowledge."""

COMPLETENESS_REVIEW_PROMPT = f"""Audit one proposed resolution for exhaustive Source coverage.
This review is invoked only when the task contract requests a memory-derived or memory-direct count, list, or set. Return one complete replacement JSON object using schema {RESOLUTION_PLAN_SCHEMA}; never return comments or a patch.

Inspect every Source evidence item against the contract target, entity, temporal scope, and relation. Check both explicit matches and grounded multi-sentence state transitions. In particular, an old object being removed, donated, discarded, or retired together with a successor described as an upgrade can establish replacement even when the verb 'replace' is absent. A merely new object without a linked predecessor does not establish replacement. Deduplicate repeated mentions of the same event or item. Ensure TARGET operations consume every distinct in-scope claim and no out-of-scope claim.

Preserve exact contiguous source_quote spans, valid evidence IDs, premise bindings, and all normal resolution constraints. Do not use benchmark labels, hidden answers, or outside knowledge."""

REPAIR_INSTRUCTION = """The previous JSON failed deterministic validation or a source-assignment audit. Return one complete corrected replacement object. Correct schema, source assignment, exact quote, identity, and binding defects only; do not invent evidence or change the evidence-independent task intent. A fact copied from the current request must remain query_context and must not also be required from memory."""


class SemanticPlannerError(RuntimeError):
    def __init__(self, message: str, *, metadata: Mapping[str, Any] | None = None) -> None:
        super().__init__(message)
        self.metadata = dict(metadata or {})


def _text(value: Any) -> str:
    return value.strip() if isinstance(value, str) else ""


def _usage(value: Any) -> dict[str, int]:
    if not isinstance(value, Mapping):
        raise SemanticPlannerError("planner response lacks usage")

    def count(name: str, *aliases: str) -> int:
        raw = next((value.get(key) for key in (name, *aliases) if value.get(key) is not None), 0)
        if isinstance(raw, bool) or not isinstance(raw, (int, float)) or raw < 0:
            raise SemanticPlannerError(f"usage.{name} is invalid")
        return int(raw)

    prompt = count("prompt_tokens", "input_tokens")
    completion = count("completion_tokens", "output_tokens")
    hit = count("prompt_cache_hit_tokens", "cache_read_input_tokens", "cached_tokens")
    miss_present = any(value.get(key) is not None for key in ("prompt_cache_miss_tokens", "cache_miss_input_tokens"))
    miss = count("prompt_cache_miss_tokens", "cache_miss_input_tokens")
    if hit > prompt or (miss_present and hit + miss != prompt):
        raise SemanticPlannerError("planner cache usage is inconsistent")
    if not miss_present:
        miss = prompt - hit
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "prompt_cache_hit_tokens": hit,
        "prompt_cache_miss_tokens": miss,
        "total_tokens": count("total_tokens") or prompt + completion,
    }


class SemanticJsonPlanner:
    def __init__(
        self,
        *,
        base_url: str,
        api_keys: Sequence[str],
        timeout: float = 180.0,
        max_tokens: int = 4096,
        model: str = "deepseek-v4-pro",
        provider: str = "deepseek",
    ) -> None:
        self.base_url = _text(base_url).rstrip("/")
        self.api_keys = list(dict.fromkeys(_text(key) for key in api_keys if _text(key)))
        self.timeout = float(timeout)
        self.max_tokens = int(max_tokens)
        self.model = _text(model)
        self.provider = _text(provider)
        self.call_index = 0
        if (
            not self.base_url
            or not self.api_keys
            or not self.model
            or self.provider not in {"deepseek", "xiaomi_mimo"}
            or self.timeout <= 0
            or self.max_tokens <= 0
        ):
            raise SemanticPlannerError("planner base URL, key pool, timeout, and max tokens are required")

    def _call(
        self,
        *,
        stage: str,
        prompt_version: str,
        system_prompt: str,
        payload: Mapping[str, Any],
        validator: Callable[[Mapping[str, Any]], Any],
        repair_context: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        user_payload = dict(payload)
        if repair_context is not None:
            user_payload["repair_context"] = dict(repair_context)
        body = {
            "model": self.model,
            "messages": [
                {
                    "role": "system",
                    "content": system_prompt + ("\n\n" + REPAIR_INSTRUCTION if repair_context is not None else ""),
                },
                {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False, separators=(",", ":"))},
            ],
            "temperature": 0,
            "max_tokens": self.max_tokens,
            "response_format": {"type": "json_object"},
            **(
                {"thinking": {"type": "disabled"}, "enable_thinking": False}
                if self.provider == "deepseek"
                else {"stream": False}
            ),
        }
        encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
        key_index = self.call_index % len(self.api_keys)
        self.call_index += 1
        call_id = "sem_" + uuid.uuid4().hex
        started = time.time()
        base_metadata = {
            "physical_call_id": call_id,
            "physical_api_call": True,
            "physical_api_calls": 1,
            "stage": stage,
            "model": self.model,
            "provider": self.provider,
            "prompt_version": prompt_version,
            "repair_call": repair_context is not None,
            "api_key_index": key_index,
            "request_sha256": hashlib.sha256(encoded).hexdigest(),
        }
        request = urllib.request.Request(
            f"{self.base_url}/chat/completions",
            data=encoded,
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {self.api_keys[key_index]}"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                status = int(response.getcode())
                raw_http = response.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:2000]
            raise SemanticPlannerError(
                f"planner HTTP {exc.code}: {detail}",
                metadata={**base_metadata, "status": "http_error", "http_status": int(exc.code), "latency_seconds": round(time.time() - started, 3)},
            ) from exc
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            raise SemanticPlannerError(
                f"planner request failed: {exc}",
                metadata={**base_metadata, "status": "request_error", "latency_seconds": round(time.time() - started, 3)},
            ) from exc
        try:
            response_body = json.loads(raw_http)
            choice = response_body["choices"][0]
            content = choice["message"]["content"]
            finish_reason = _text(choice.get("finish_reason"))
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as exc:
            raise SemanticPlannerError(
                "planner returned a malformed response envelope",
                metadata={**base_metadata, "status": "invalid_response", "http_status": status, "latency_seconds": round(time.time() - started, 3)},
            ) from exc
        if not isinstance(content, str):
            raise SemanticPlannerError(
                "planner response content is not text",
                metadata={**base_metadata, "status": "invalid_response", "http_status": status, "latency_seconds": round(time.time() - started, 3)},
            )
        metadata = {
            **base_metadata,
            "status": "completed",
            "http_status": status,
            "latency_seconds": round(time.time() - started, 3),
            "finish_reason": finish_reason,
            "response_sha256": hashlib.sha256(content.encode("utf-8")).hexdigest(),
            "usage": _usage(response_body.get("usage")),
        }
        if status != 200:
            raise SemanticPlannerError("planner did not return one complete JSON object", metadata=metadata)
        if finish_reason == "length":
            raise SemanticPlannerError(
                "planner output was truncated at the token limit",
                metadata={
                    **metadata,
                    "validation_error": "finish_reason=length; return a compact plan containing only claims used by bindings or operations",
                    "raw_response": content[:2000],
                },
            )
        if finish_reason != "stop":
            raise SemanticPlannerError("planner did not return one complete JSON object", metadata=metadata)
        try:
            raw_value = json.loads(content)
        except json.JSONDecodeError as exc:
            raise SemanticPlannerError(
                "planner returned non-JSON content",
                metadata={
                    **metadata,
                    "validation_error": f"strict JSON decode failed: {exc}",
                    "raw_response": content[:16000],
                },
            ) from exc
        if not isinstance(raw_value, Mapping):
            raise SemanticPlannerError(
                "planner returned a non-object JSON value",
                metadata={**metadata, "validation_error": "root JSON value must be an object", "raw_response": raw_value},
            )
        try:
            validated_result = validator(raw_value)
            if (
                isinstance(validated_result, tuple)
                and len(validated_result) == 2
                and isinstance(validated_result[0], Mapping)
                and isinstance(validated_result[1], list)
            ):
                validated = dict(validated_result[0])
                metadata["normalization_warnings"] = list(validated_result[1])
            else:
                validated = validated_result
        except Exception as exc:
            raise SemanticPlannerError(
                f"planner returned invalid {stage}: {exc}",
                metadata={**metadata, "validation_error": str(exc), "raw_response": raw_value},
            ) from exc
        return validated, metadata

    def plan_task_contract(
        self,
        row: Mapping[str, Any],
        *,
        repair_context: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        payload = task_contract_payload(row)
        return self._call(
            stage="task_contract_planner",
            prompt_version=TASK_PROMPT_VERSION,
            system_prompt=TASK_CONTRACT_PROMPT,
            payload=payload,
            validator=lambda value: validate_task_contract(value, payload),
            repair_context=repair_context,
        )

    def plan_resolution(
        self,
        row: Mapping[str, Any],
        contract: Mapping[str, Any],
        catalog: Mapping[str, Any],
        *,
        repair_context: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        def validate(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
            normalized, warnings = normalize_resolution_output(value, contract)
            return validate_resolution_plan(normalized, contract, catalog), warnings

        return self._call(
            stage="semantic_evidence_resolver",
            prompt_version=RESOLUTION_PROMPT_VERSION,
            system_prompt=RESOLUTION_PROMPT,
            payload=resolution_payload(row, contract, catalog),
            validator=validate,
            repair_context=repair_context,
        )
    def review_resolution_completeness(
        self,
        row: Mapping[str, Any],
        contract: Mapping[str, Any],
        catalog: Mapping[str, Any],
        resolution: Mapping[str, Any],
        *,
        repair_context: Mapping[str, Any] | None = None,
    ) -> tuple[dict[str, Any], dict[str, Any]]:
        if not requires_exhaustive_resolution_review(contract):
            raise SemanticPlannerError("completeness review is not required for this contract")

        def validate(value: Mapping[str, Any]) -> tuple[dict[str, Any], list[str]]:
            normalized, warnings = normalize_resolution_output(value, contract)
            return validate_resolution_plan(normalized, contract, catalog), warnings

        payload = resolution_payload(row, contract, catalog)
        payload["proposed_resolution"] = validate_resolution_plan(
            resolution, contract, catalog
        )
        return self._call(
            stage="semantic_resolution_completeness_reviewer",
            prompt_version=COMPLETENESS_REVIEW_PROMPT_VERSION,
            system_prompt=COMPLETENESS_REVIEW_PROMPT,
            payload=payload,
            validator=validate,
            repair_context=repair_context,
        )
