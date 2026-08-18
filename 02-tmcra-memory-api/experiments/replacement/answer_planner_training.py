from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ANSWER_PLANNER_SUPPLEMENT_VERSION = "answer_planner_supplement_v1"
ANSWER_PLAN_TARGET_KEY = "answer_plan_targets"
BLOCKED_TRAINING_SOURCE_MARKERS = (
    "longmemeval",
    "long_mem_eval",
    "lme_s500",
    "benchmark_gold",
    "gold_evidence",
)

PLANNER_QUERY_INTENTS = {
    "current_state",
    "history_compare",
    "temporal_order",
    "profile_recommendation",
    "multi_session_chain",
    "abstain",
}
EVIDENCE_ROLES = {
    "direct_support",
    "profile_basis",
    "temporal_anchor",
    "temporal_neighbor",
    "current_value",
    "historical_value",
    "contrast_support",
    "latent_context",
    "distractor",
}
PLAN_RELATIONS = {
    "same_profile_domain",
    "profile_support",
    "profile_tunnel",
    "before",
    "after",
    "supersedes",
    "refines",
    "same_problem_chain",
    "contrasts",
}


def clean_text(value: Any) -> str:
    return " ".join(str(value or "").replace("\r", " ").replace("\n", " ").replace("\t", " ").split()).strip()


def normalize_text(value: Any) -> str:
    return clean_text(value).lower()


def dedupe_texts(values: Iterable[Any], *, max_items: int | None = None) -> list[str]:
    results: list[str] = []
    seen: set[str] = set()
    for value in values:
        text = clean_text(value)
        if not text:
            continue
        key = normalize_text(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
        if max_items is not None and len(results) >= max_items:
            break
    return results


def assert_training_source_allowed(source_dataset: Any, metadata: Mapping[str, Any] | None = None) -> None:
    joined = normalize_text(
        " ".join(
            [
                clean_text(source_dataset),
                clean_text((metadata or {}).get("source_dataset", "")),
                clean_text((metadata or {}).get("source", "")),
                clean_text((metadata or {}).get("benchmark", "")),
            ]
        )
    )
    blocked = [marker for marker in BLOCKED_TRAINING_SOURCE_MARKERS if marker in joined]
    if blocked:
        raise ValueError(
            "answer planner supplement training must not use benchmark/gold sources; "
            f"blocked_markers={blocked}"
        )


def _coerce_role(value: Any, *, default: str = "latent_context") -> str:
    role = normalize_text(value).replace("-", "_")
    return role if role in EVIDENCE_ROLES else default


def _coerce_intent(value: Any) -> str:
    intent = normalize_text(value).replace("-", "_")
    return intent if intent in PLANNER_QUERY_INTENTS else "abstain"


def _coerce_relation(value: Any) -> str:
    relation = normalize_text(value).replace("-", "_")
    return relation if relation in PLAN_RELATIONS else "same_problem_chain"


def normalize_answer_plan_target(
    target: Mapping[str, Any],
    *,
    candidate_event_ids: Sequence[Any],
    source_dataset: str,
    metadata: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    assert_training_source_allowed(source_dataset, metadata)
    candidate_ids = dedupe_texts(candidate_event_ids)
    candidate_set = set(candidate_ids)
    selected = [item for item in dedupe_texts(target.get("selected_memory_ids", []) or []) if item in candidate_set]
    protected = [item for item in dedupe_texts(target.get("protected_memory_ids", []) or selected) if item in set(selected)]
    current = [item for item in dedupe_texts(target.get("current_memory_ids", []) or []) if item in candidate_set]
    historical = [item for item in dedupe_texts(target.get("historical_memory_ids", []) or []) if item in candidate_set]
    suppressed = [item for item in dedupe_texts(target.get("suppressed_memory_ids", []) or []) if item in candidate_set and item not in set(selected)]

    evidence_labels: list[dict[str, Any]] = []
    label_by_id: dict[str, dict[str, Any]] = {}
    for raw in list(target.get("evidence_labels", []) or []):
        if not isinstance(raw, Mapping):
            continue
        memory_id = clean_text(raw.get("memory_id", ""))
        if not memory_id or memory_id not in candidate_set:
            continue
        label = {
            "memory_id": memory_id,
            "utility": max(0.0, min(1.0, float(raw.get("utility", 0.0) or 0.0))),
            "role": _coerce_role(raw.get("role", "")),
            "currentness": normalize_text(raw.get("currentness", "")) or "unknown",
            "profile_domain": clean_text(raw.get("profile_domain", "")),
            "temporal_relation": normalize_text(raw.get("temporal_relation", "")),
            "protected": bool(raw.get("protected", memory_id in protected)),
            "chain_group": clean_text(raw.get("chain_group", "")),
        }
        evidence_labels.append(label)
        label_by_id[memory_id] = label
    for memory_id in selected:
        if memory_id in label_by_id:
            continue
        role = "direct_support"
        if memory_id in current:
            role = "current_value"
        elif memory_id in historical:
            role = "historical_value"
        evidence_labels.append(
            {
                "memory_id": memory_id,
                "utility": 1.0,
                "role": role,
                "currentness": "current" if memory_id in current else "historical" if memory_id in historical else "unknown",
                "profile_domain": "",
                "temporal_relation": "",
                "protected": memory_id in protected,
                "chain_group": "",
            }
        )

    relations: list[dict[str, Any]] = []
    for raw in list(target.get("support_chain", []) or []):
        if not isinstance(raw, Mapping):
            continue
        source = clean_text(raw.get("source", raw.get("from", "")))
        target_id = clean_text(raw.get("target", raw.get("to", "")))
        if source not in candidate_set or target_id not in candidate_set or source == target_id:
            continue
        relations.append(
            {
                "source": source,
                "target": target_id,
                "relation": _coerce_relation(raw.get("relation", "")),
                "score": max(0.0, min(1.0, float(raw.get("score", 1.0) or 1.0))),
                "protected": bool(raw.get("protected", source in protected and target_id in protected)),
            }
        )

    intent = _coerce_intent(target.get("query_intent", ""))
    return {
        "version": ANSWER_PLANNER_SUPPLEMENT_VERSION,
        "query_intent": intent,
        "answer_basis_type": clean_text(target.get("answer_basis_type", "")) or intent,
        "selected_memory_ids": selected,
        "protected_memory_ids": protected,
        "current_memory_ids": current,
        "historical_memory_ids": historical,
        "suppressed_memory_ids": suppressed,
        "evidence_labels": evidence_labels,
        "support_chain": relations,
        "temporal_order": [item for item in dedupe_texts(target.get("temporal_order", []) or []) if item in candidate_set],
        "profile_domains": dedupe_texts(target.get("profile_domains", []) or []),
        "conflict_policy": normalize_text(target.get("conflict_policy", "")) or "none",
        "metadata": {
            "source_dataset": source_dataset,
            "training_mode": "supplement",
            **dict(target.get("metadata", {}) or {}),
        },
    }


def attach_answer_plan_target(
    row: Mapping[str, Any],
    target: Mapping[str, Any],
    *,
    source_dataset: str,
) -> dict[str, Any]:
    payload = dict(row)
    candidate_event_ids = list(payload.get("candidate_event_ids", []) or [])
    normalized_target = normalize_answer_plan_target(
        target,
        candidate_event_ids=candidate_event_ids,
        source_dataset=source_dataset,
        metadata=dict(payload.get("metadata", {}) or {}),
    )
    payload[ANSWER_PLAN_TARGET_KEY] = normalized_target
    metadata = dict(payload.get("metadata", {}) or {})
    metadata.update(
        {
            "answer_planner_supplement": True,
            "answer_planner_version": ANSWER_PLANNER_SUPPLEMENT_VERSION,
            "training_mode": "supplement",
            "source_dataset": source_dataset,
        }
    )
    payload["metadata"] = metadata
    answer_targets = dict(payload.get("answer_targets", {}) or {})
    answer_targets.setdefault("answer_type", normalized_target["answer_basis_type"])
    answer_targets["answer_plan_supervision"] = True
    payload["answer_targets"] = answer_targets
    return payload


def validate_supplement_output_dir(
    output_dir: Path,
    *,
    base_checkpoints: Sequence[Path],
    allow_existing_empty: bool = False,
) -> Path:
    resolved_output = Path(output_dir).expanduser().resolve()
    base_paths = [Path(path).expanduser().resolve() for path in list(base_checkpoints or []) if str(path)]
    for base_path in base_paths:
        if resolved_output == base_path or resolved_output == base_path.parent:
            raise ValueError(f"output_dir must not be the base checkpoint path or parent: {resolved_output}")
        if base_path.exists() and resolved_output in base_path.parents:
            raise ValueError(f"output_dir must not be inside a base checkpoint path: {resolved_output}")
    if resolved_output.exists():
        if not resolved_output.is_dir():
            raise ValueError(f"output_dir exists and is not a directory: {resolved_output}")
        has_existing_files = any(resolved_output.iterdir())
        if has_existing_files:
            raise FileExistsError(
                f"output_dir already exists and is not empty: {resolved_output}. "
                "Use a new run directory for supplement training."
            )
        if not allow_existing_empty:
            raise FileExistsError(
                f"output_dir already exists: {resolved_output}. "
                "Use a new run directory, or explicitly allow an existing empty directory."
            )
    return resolved_output


def build_supplement_training_manifest(
    *,
    data_dir: Path,
    output_dir: Path,
    base_node_checkpoint: Path,
    base_path_checkpoint: Path | None = None,
    allow_existing_empty_output_dir: bool = False,
    trainable_stage: str = "answer_plan_only",
    extra_config: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    base_node = Path(base_node_checkpoint).expanduser().resolve()
    base_path = Path(base_path_checkpoint).expanduser().resolve() if base_path_checkpoint else None
    if not base_node.exists():
        raise FileNotFoundError(f"base_node_checkpoint not found: {base_node}")
    if base_path is not None and not base_path.exists():
        raise FileNotFoundError(f"base_path_checkpoint not found: {base_path}")
    resolved_output = validate_supplement_output_dir(
        output_dir,
        base_checkpoints=[base_node, *([base_path] if base_path else [])],
        allow_existing_empty=bool(allow_existing_empty_output_dir),
    )
    resolved_data = Path(data_dir).expanduser().resolve()
    resolved_trainable_stage = clean_text(trainable_stage) or "answer_plan_only"
    command = [
        "python",
        "scripts/train_locomo_node_memory.py",
        "--data-dir",
        str(resolved_data),
        "--output-dir",
        str(resolved_output),
        "--resume-checkpoint",
        str(base_node),
        "--resume-weights-only",
        "--trainable-stage",
        resolved_trainable_stage,
    ]
    return {
        "version": ANSWER_PLANNER_SUPPLEMENT_VERSION,
        "training_mode": "supplement",
        "base_model_policy": "read_only_resume_weights_only",
        "trainable_stage": resolved_trainable_stage,
        "base_node_checkpoint": str(base_node),
        "base_path_checkpoint": str(base_path) if base_path else "",
        "data_dir": str(resolved_data),
        "output_dir": str(resolved_output),
        "no_overwrite_base_model": True,
        "recommended_command": command,
        "extra_config": dict(extra_config or {}),
    }
