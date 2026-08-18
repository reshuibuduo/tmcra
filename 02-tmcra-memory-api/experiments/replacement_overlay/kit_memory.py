from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import tempfile
import time
from typing import Any, Dict, Iterable, List

from experiments.replacement.adapters.base import MemoryAdapter, MemoryHit, MemoryRetrieval
from experiments.replacement.memory_graph import _clean_text, _estimate_tokens, _normalize

from .kit_runtime import load_tmcra_memory, tmcra_memory_available


def _slug(value: str, *, fallback: str) -> str:
    text = "".join(char if char.isalnum() or char in "._-" else "_" for char in _clean_text(value)).strip("._-")
    return text or fallback


def _slot_key(category: str, value: str, *, slot_key: str = "", anchors: Iterable[str] | None = None) -> str:
    resolved = _clean_text(slot_key)
    if resolved:
        return resolved
    clean_category = _clean_text(category) or "memory"
    anchor = _clean_text(next(iter(anchors or []), "")) or _clean_text(value).split(" ", 1)[0]
    if clean_category == "goal":
        return "goal.primary"
    if clean_category == "stage_state":
        return "stage.current"
    if clean_category == "constraint":
        return f"constraint.{_slug(anchor, fallback='primary')}"
    if clean_category == "preference":
        return f"preference.{_slug(anchor, fallback='default')}"
    if clean_category == "terminology":
        return f"term.{_slug(anchor, fallback='term')}"
    return f"{clean_category}.{_slug(anchor, fallback='value')}"


def _relation_hit(hit: MemoryHit, *, weight_bias: float = 0.0) -> Dict[str, Any]:
    if not hit.anchors:
        return {}
    anchor = _clean_text(hit.anchors[0])
    if not anchor or anchor == hit.value:
        return {}
    return {
        "from": anchor,
        "to": hit.value,
        "relation": hit.relation,
        "weight": round(max(0.25, min(0.98, 0.42 + hit.score * 0.4 + weight_bias)), 6),
        "source_kind": hit.source_kind,
        "memory_id": hit.memory_id,
    }


@dataclass(slots=True)
class _KitModules:
    MemoryRecord: Any
    TMCRAMemory: Any
    JsonScopedMemoryStore: Any
    SQLiteScopedMemoryStore: Any


def _load_modules() -> _KitModules:
    module = load_tmcra_memory()
    return _KitModules(
        MemoryRecord=module.MemoryRecord,
        TMCRAMemory=module.TMCRAMemory,
        JsonScopedMemoryStore=module.JsonScopedMemoryStore,
        SQLiteScopedMemoryStore=module.SQLiteScopedMemoryStore,
    )


class _KitMemoryAdapterBase(MemoryAdapter):
    scope_id = "overlay_eval"

    def __init__(self, *, backend: str, auto_extract: bool = False, default_top_k: int = 8) -> None:
        modules = _load_modules()
        self._modules = modules
        self.default_top_k = max(1, int(default_top_k))
        self.auto_extract = bool(auto_extract)
        self.turn_index = 0
        self._last_retrieval_context_tokens = 0
        self._temp_dir = tempfile.TemporaryDirectory(prefix=f"tmcra_overlay_{backend}_")
        self._temp_root = Path(self._temp_dir.name)
        if backend == "json":
            self.store = modules.JsonScopedMemoryStore(self._temp_root / "store", default_top_k=self.default_top_k)
        elif backend == "sqlite":
            self.store = modules.SQLiteScopedMemoryStore(self._temp_root / "store.sqlite3", default_top_k=self.default_top_k)
        else:
            raise ValueError(f"Unsupported TMCRA memory kit backend: {backend}")

    def reset(self) -> None:
        self.turn_index = 0
        self._last_retrieval_context_tokens = 0
        empty_memory = self._modules.TMCRAMemory(default_top_k=self.default_top_k)
        self._save_memory(empty_memory)

    def ingest_turn(
        self,
        user_text: str,
        assistant_text: str = "",
        *,
        answer_payload: Dict[str, Any] | None = None,
        extraction_result: Dict[str, Any] | None = None,
    ) -> None:
        _ = assistant_text, extraction_result
        self.turn_index += 1
        memory = self.store.load_memory(self.scope_id)
        structured_records = self._structured_records(answer_payload)
        if structured_records:
            memory.add_records(structured_records)
            self._save_memory(memory)
            return
        if self.auto_extract or self._looks_like_write_turn(user_text, answer_payload=answer_payload):
            memory.ingest_text(user_text, turn_index=self.turn_index, source_kind="overlay_memory_kit")
            self._save_memory(memory)

    def retrieve(self, query: str, *, top_k: int = 6) -> MemoryRetrieval:
        result = self.store.retrieve(self.scope_id, query, top_k=max(top_k, self.default_top_k))
        current_hits = [self._to_memory_hit(item) for item in list(result.current_facts or [])]
        historical_hits = [self._to_memory_hit(item) for item in list(result.historical_facts or [])]
        hits = list(current_hits) + list(historical_hits)
        relations = []
        concepts = []
        for hit in hits:
            concepts.append({"concept": hit.value, "type": hit.category, "source_kind": hit.source_kind})
            for anchor in hit.anchors[:2]:
                concepts.append({"concept": anchor, "type": "context", "source_kind": hit.source_kind})
            relation = _relation_hit(hit, weight_bias=0.04)
            if relation:
                relations.append(relation)
        retrieval_context_tokens = int(result.metadata.get("estimated_context_tokens", _estimate_tokens(" ".join(hit.value for hit in hits))))
        self._last_retrieval_context_tokens = retrieval_context_tokens
        overwrite_hits = [hit for hit in historical_hits if any(chain.get("slot_key") == hit.slot_key for chain in list(result.overwrite_chains or []))]
        return MemoryRetrieval(
            concepts=concepts,
            relations=relations,
            hits=hits[:top_k],
            active_hits=current_hits[:top_k],
            history_hits=historical_hits[:top_k],
            stale_hits=historical_hits[:top_k],
            overwrite_hits=overwrite_hits[:top_k],
            retrieval_seconds=0.0,
            context_token_estimate=retrieval_context_tokens,
            retrieval_context_token_estimate=retrieval_context_tokens,
            metadata={
                **dict(result.metadata or {}),
                "overwrite_chains": list(result.overwrite_chains or []),
                "backend": self.name,
            },
        )

    def stats(self) -> Dict[str, Any]:
        memory = self.store.load_memory(self.scope_id)
        payload = memory.to_dict()
        records = list((payload.get("graph", {}) or {}).get("records", []) or [])
        active_records = [item for item in records if str(item.get("state", "active")) == "active"]
        total_state_tokens = _estimate_tokens(json.dumps(payload, ensure_ascii=False))
        return {
            "records": len(records),
            "active_records": len(active_records),
            "context_token_estimate": int(self._last_retrieval_context_tokens),
            "retrieval_context_token_estimate": int(self._last_retrieval_context_tokens),
            "total_state_token_estimate": int(total_state_tokens),
            "storage_bytes": int(self.storage_bytes()),
            "overlay_enabled": False,
            "kit_memory": True,
        }

    def storage_bytes(self) -> int:
        total = 0
        for path in self._temp_root.rglob("*"):
            if path.is_file():
                total += int(path.stat().st_size)
        return total

    def build_prompt_context(self, query: str, *, top_k: int = 8) -> Dict[str, Any]:
        context = self.store.assemble_context(self.scope_id, query, top_k=top_k)
        memory = self.store.load_memory(self.scope_id)
        return {
            "mode": self.name,
            "query": query,
            "retrieval": context.get("retrieval", {}),
            "stats": self.stats(),
            "state": memory.to_dict(),
            "system_prompt_addition": context.get("system_prompt_addition", ""),
            "estimated_tokens": int(context.get("estimated_tokens", 0) or 0),
        }

    def export_dialog_graph(self) -> Dict[str, Any]:
        return self.store.load_memory(self.scope_id).to_dict()

    def telemetry_snapshot(self) -> Dict[str, Any]:
        return self.stats()

    def _save_memory(self, memory: Any) -> None:
        last_error: Exception | None = None
        for _attempt in range(5):
            try:
                self.store.save_memory(self.scope_id, memory)
                return
            except PermissionError as exc:
                last_error = exc
                time.sleep(0.05)
        if last_error is not None:
            raise last_error

    def _structured_records(self, answer_payload: Dict[str, Any] | None) -> List[Any]:
        modules = self._modules
        results: List[Any] = []
        for index, raw in enumerate((answer_payload or {}).get("replacement_memory_records", []) or []):
            if not isinstance(raw, dict):
                continue
            category = _clean_text(raw.get("category", "memory")) or "memory"
            value = _clean_text(raw.get("value", ""))
            if not value:
                continue
            anchors = [_clean_text(anchor) for anchor in raw.get("anchors", []) or [] if _clean_text(anchor)]
            slot_key = _slot_key(category, value, slot_key=str(raw.get("slot", "")), anchors=anchors)
            record_id = f"{slot_key}:{self.turn_index}:{index}"
            results.append(
                modules.MemoryRecord(
                    record_id=record_id,
                    category=category,
                    slot_key=slot_key,
                    value=value,
                    relation=_clean_text(raw.get("relation", "")) or f"{category}_memory",
                    anchors=anchors[:8],
                    salience=float(raw.get("salience", 0.88 if category in {"goal", "constraint"} else 0.74) or 0.74),
                    confidence=float(raw.get("confidence", 0.82) or 0.82),
                    turn_index=self.turn_index,
                    source_kind=_clean_text(raw.get("source_kind", "")) or "replacement_memory",
                    state="active" if bool(raw.get("active", True)) else "historical",
                    metadata=dict(raw.get("metadata", {}) or {}),
                )
            )
        return results

    def _looks_like_write_turn(self, user_text: str, *, answer_payload: Dict[str, Any] | None) -> bool:
        text = _normalize(user_text)
        if not text or "?" in text:
            return False
        markers = (
            "goal",
            "constraint",
            "preference",
            "stage",
            "status",
            "phase",
            "term",
            "terminology",
            "目标",
            "约束",
            "偏好",
            "阶段",
            "状态",
            "术语",
        )
        if any(marker in text for marker in markers):
            return True
        metadata = dict((answer_payload or {}).get("metadata", {}) or {})
        return bool(metadata.get("memory_write"))

    def _to_memory_hit(self, item: Any) -> MemoryHit:
        return MemoryHit(
            memory_id=str(getattr(item, "record_id", "")),
            category=str(getattr(item, "category", "")),
            value=str(getattr(item, "value", "")),
            relation=str(getattr(item, "relation", "related_to")),
            anchors=[str(anchor) for anchor in list(getattr(item, "anchors", []) or []) if _clean_text(anchor)],
            score=float(getattr(item, "score", 0.0) or 0.0),
            source_kind=str(getattr(item, "source_kind", "memory")),
            slot_key=str(getattr(item, "slot_key", "")),
            state=str(getattr(item, "state", "active")),
            turn_index=int(getattr(item, "turn_index", 0) or 0),
            metadata=dict(getattr(item, "metadata", {}) or {}),
        )


class OverlayTMCRAMemoryKitJsonAdapter(_KitMemoryAdapterBase):
    name = "tmcra_memory_kit_json"

    def __init__(self, *, auto_extract: bool = False, default_top_k: int = 8) -> None:
        super().__init__(backend="json", auto_extract=auto_extract, default_top_k=default_top_k)


class OverlayTMCRAMemoryKitSQLiteAdapter(_KitMemoryAdapterBase):
    name = "tmcra_memory_kit_sqlite"

    def __init__(self, *, auto_extract: bool = False, default_top_k: int = 8) -> None:
        super().__init__(backend="sqlite", auto_extract=auto_extract, default_top_k=default_top_k)


def available_kit_memory_factories() -> Dict[str, Any]:
    if not tmcra_memory_available():
        return {}
    return {
        "tmcra_memory_kit_json": lambda: OverlayTMCRAMemoryKitJsonAdapter(),
        "tmcra_memory_kit_sqlite": lambda: OverlayTMCRAMemoryKitSQLiteAdapter(),
    }
