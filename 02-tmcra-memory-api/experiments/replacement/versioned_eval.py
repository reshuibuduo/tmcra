from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Sequence

from .result_labels import annotate_result_payload, format_code_with_label


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


@dataclass(slots=True)
class VersionedBenchmarkRecord:
    generation_id: str
    generation_name: str
    reasoner: str
    memory: str
    total_score: float
    reasoning_quality_score: float
    memory_quality_score: float
    efficiency_score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "generation_id": self.generation_id,
            "generation_name": self.generation_name,
            "reasoner": self.reasoner,
            "memory": self.memory,
            "total_score": round(float(self.total_score), 6),
            "reasoning_quality_score": round(float(self.reasoning_quality_score), 6),
            "memory_quality_score": round(float(self.memory_quality_score), 6),
            "efficiency_score": round(float(self.efficiency_score), 6),
            "metadata": dict(self.metadata),
        }


@dataclass(slots=True)
class EmbeddedReplacementRecord:
    llm_profile: str
    variant: str
    reasoner: str
    memory: str
    judge_provider: str
    total_score: float
    reasoning_quality_score: float
    memory_quality_score: float
    efficiency_score: float
    metadata: Dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> Dict[str, Any]:
        return {
            "llm_profile": self.llm_profile,
            "variant": self.variant,
            "reasoner": self.reasoner,
            "memory": self.memory,
            "judge_provider": self.judge_provider,
            "total_score": round(float(self.total_score), 6),
            "reasoning_quality_score": round(float(self.reasoning_quality_score), 6),
            "memory_quality_score": round(float(self.memory_quality_score), 6),
            "efficiency_score": round(float(self.efficiency_score), 6),
            "metadata": dict(self.metadata),
        }


def leaderboard_to_generation_records(leaderboard: Dict[str, Any], *, generation_id: str, generation_name: str) -> List[VersionedBenchmarkRecord]:
    records: List[VersionedBenchmarkRecord] = []
    for item in leaderboard.get("summary", []) or []:
        records.append(
            VersionedBenchmarkRecord(
                generation_id=generation_id,
                generation_name=generation_name,
                reasoner=str(item.get("reasoner", "")),
                memory=str(item.get("memory", "")),
                total_score=float(item.get("total_score", 0.0) or 0.0),
                reasoning_quality_score=float(item.get("reasoning_quality_score", 0.0) or 0.0),
                memory_quality_score=float(item.get("memory_quality_score", 0.0) or 0.0),
                efficiency_score=float(item.get("efficiency_score", 0.0) or 0.0),
                metadata=dict(item.get("metadata", {}) or {}),
            )
        )
    return records


def leaderboard_to_embedded_records(
    leaderboard: Dict[str, Any],
    *,
    llm_profile: str,
    variant: str,
    judge_provider: str,
    metadata_extra: Dict[str, Any] | None = None,
) -> List[EmbeddedReplacementRecord]:
    extra = dict(metadata_extra or {})
    return [
        EmbeddedReplacementRecord(
            llm_profile=llm_profile,
            variant=variant,
            reasoner=str(item.get("reasoner", "")),
            memory=str(item.get("memory", "")),
            judge_provider=judge_provider,
            total_score=float(item.get("total_score", 0.0) or 0.0),
            reasoning_quality_score=float(item.get("reasoning_quality_score", 0.0) or 0.0),
            memory_quality_score=float(item.get("memory_quality_score", 0.0) or 0.0),
            efficiency_score=float(item.get("efficiency_score", 0.0) or 0.0),
            metadata={**dict(item.get("metadata", {}) or {}), **extra},
        )
        for item in leaderboard.get("summary", []) or []
    ]


def summarize_generation_records(records: Sequence[VersionedBenchmarkRecord]) -> Dict[str, Any]:
    grouped: Dict[str, List[VersionedBenchmarkRecord]] = {}
    for record in records:
        grouped.setdefault(record.generation_id, []).append(record)
    summary: List[Dict[str, Any]] = []
    ordered = sorted(grouped.items(), key=lambda item: item[0])
    previous_best = None
    for generation_id, generation_records in ordered:
        best = max(generation_records, key=lambda item: item.total_score)
        avg_total = sum(item.total_score for item in generation_records) / max(1, len(generation_records))
        avg_reasoning = sum(item.reasoning_quality_score for item in generation_records) / max(1, len(generation_records))
        avg_memory = sum(item.memory_quality_score for item in generation_records) / max(1, len(generation_records))
        avg_efficiency = sum(item.efficiency_score for item in generation_records) / max(1, len(generation_records))
        delta_from_previous = round(best.total_score - previous_best, 6) if previous_best is not None else None
        summary.append(
            {
                "generation_id": generation_id,
                "generation_name": best.generation_name,
                "best_reasoner": best.reasoner,
                "best_memory": best.memory,
                "best_total_score": round(best.total_score, 6),
                "avg_total_score": round(avg_total, 6),
                "avg_reasoning_quality_score": round(avg_reasoning, 6),
                "avg_memory_quality_score": round(avg_memory, 6),
                "avg_efficiency_score": round(avg_efficiency, 6),
                "delta_from_previous_best": delta_from_previous,
                "history_query_accuracy": round(float(best.metadata.get("history_query_accuracy", 0.0) or 0.0), 6),
                "compare_realization_accuracy": round(float(best.metadata.get("compare_realization_accuracy", 0.0) or 0.0), 6),
                "timeline_realization_accuracy": round(float(best.metadata.get("timeline_realization_accuracy", 0.0) or 0.0), 6),
                "path_composition_accuracy": round(float(best.metadata.get("path_composition_accuracy", 0.0) or 0.0), 6),
            }
        )
        previous_best = best.total_score
    return {"summary": summary, "records": [item.to_dict() for item in records]}


def summarize_embedded_records(records: Sequence[EmbeddedReplacementRecord]) -> Dict[str, Any]:
    grouped: Dict[tuple[str, str], List[EmbeddedReplacementRecord]] = {}
    for record in records:
        grouped.setdefault((record.llm_profile, record.variant), []).append(record)
    summary: List[Dict[str, Any]] = []
    for (llm_profile, variant), variant_records in sorted(grouped.items()):
        best = max(variant_records, key=lambda item: item.total_score)
        summary.append(
            {
                "llm_profile": llm_profile,
                "variant": variant,
                "judge_provider": best.judge_provider,
                "reasoner": best.reasoner,
                "memory": best.memory,
                "total_score": round(best.total_score, 6),
                "reasoning_quality_score": round(best.reasoning_quality_score, 6),
                "memory_quality_score": round(best.memory_quality_score, 6),
                "efficiency_score": round(best.efficiency_score, 6),
                "embedded_replacement_gain": round(
                    best.total_score
                    - max(
                        (
                            item.total_score
                            for item in variant_records
                            if _normalize(item.variant) == "native_full_context"
                        ),
                        default=best.total_score,
                    ),
                    6,
                )
                if _normalize(variant) != "native_full_context"
                else 0.0,
                "teacher_vs_tmcra_agreement": round(float(best.metadata.get("teacher_vs_tmcra_agreement", 0.0) or 0.0), 6),
                "matrix_kind": str(best.metadata.get("matrix_kind", "") or ""),
                "replacement_scope": str(best.metadata.get("replacement_scope", "") or ""),
                "host_memory_policy": str(best.metadata.get("host_memory_policy", "") or ""),
                "host_reasoning_policy": str(best.metadata.get("host_reasoning_policy", "") or ""),
                "host_judge_policy": str(best.metadata.get("host_judge_policy", "") or ""),
                "tmcra_modules": list(best.metadata.get("tmcra_modules", []) or []),
            }
        )
    return {"summary": summary, "records": [item.to_dict() for item in records]}


def build_versioned_verdict(*, generation_summary: Dict[str, Any], embedded_summary: Dict[str, Any]) -> Dict[str, Any]:
    generation_rows = list(generation_summary.get("summary", []) or [])
    embedded_rows = list(embedded_summary.get("summary", []) or [])
    judge_row = next((item for item in generation_rows if _normalize(item.get("generation_id", "")) in {"g6", "gen6", "generation6"}), None)
    qwen_assist = next((item for item in embedded_rows if _normalize(item.get("variant", "")) == "llm_plus_tmcra_judge" and _normalize(item.get("llm_profile", "")) == "qwen7b"), None)
    native_best = max((float(item.get("total_score", 0.0) or 0.0) for item in embedded_rows if _normalize(item.get("variant", "")) == "native_full_context"), default=0.0)
    judge_ready = bool(
        judge_row
        and float(judge_row.get("history_query_accuracy", 0.0) or 0.0) >= 0.15
        and float(judge_row.get("compare_realization_accuracy", 0.0) or 0.0) >= 0.15
        and float(judge_row.get("path_composition_accuracy", 0.0) or 0.0) >= 0.10
    )
    hybrid_ready = bool(qwen_assist and float(qwen_assist.get("total_score", 0.0) or 0.0) >= native_best - 0.02)
    if judge_ready and hybrid_ready:
        classification = "hybrid embedded replacement ready"
    elif judge_ready:
        classification = "judge replacement ready"
    else:
        classification = "full embedded replacement not ready"
    return {
        "classification": classification,
        "judge_replacement_ready": judge_ready,
        "hybrid_embedded_ready": hybrid_ready,
        "native_full_context_best": native_best,
    }


def split_embedded_summary(embedded_summary: Dict[str, Any]) -> Dict[str, Dict[str, Any]]:
    rows = list(embedded_summary.get("summary", []) or [])
    records = list(embedded_summary.get("records", []) or [])
    baseline_rows = [dict(item) for item in rows if _normalize(item.get("variant", "")) == "native_full_context"]
    baseline_records = [dict(item) for item in records if _normalize(item.get("variant", "")) == "native_full_context"]
    strict_rows = [dict(item) for item in rows if _normalize(item.get("matrix_kind", "")) == "strict_replace_matrix"]
    strict_records = [dict(item) for item in records if _normalize(item.get("metadata", {}).get("matrix_kind", "")) == "strict_replace_matrix"]
    hybrid_rows = [dict(item) for item in rows if _normalize(item.get("matrix_kind", "")) == "hybrid_collab_matrix"]
    hybrid_records = [dict(item) for item in records if _normalize(item.get("metadata", {}).get("matrix_kind", "")) == "hybrid_collab_matrix"]
    return {
        "strict_replace_matrix": {"baseline_rows": baseline_rows, "summary": strict_rows, "records": strict_records},
        "hybrid_collab_matrix": {"baseline_rows": baseline_rows, "summary": hybrid_rows, "records": hybrid_records},
        "baseline": {"summary": baseline_rows, "records": baseline_records},
    }


def write_versioned_reports(
    *,
    output_dir: str | Path,
    generation_summary: Dict[str, Any],
    embedded_summary: Dict[str, Any],
    verdict: Dict[str, Any],
) -> Dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: Dict[str, Path] = {}

    generation_summary = annotate_result_payload(generation_summary)
    embedded_summary = annotate_result_payload(embedded_summary)
    verdict = annotate_result_payload(verdict)

    generation_path = out_dir / "generation_leaderboard.json"
    generation_path.write_text(json.dumps(generation_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["generation_json"] = generation_path

    embedded_path = out_dir / "embedded_replacement_matrix.json"
    embedded_path.write_text(json.dumps(embedded_summary, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["embedded_json"] = embedded_path

    split_payload = split_embedded_summary(embedded_summary)
    strict_path = out_dir / "strict_replace_matrix.json"
    strict_path.write_text(json.dumps(split_payload["strict_replace_matrix"], ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["strict_replace_matrix_json"] = strict_path
    hybrid_path = out_dir / "hybrid_collab_matrix.json"
    hybrid_path.write_text(json.dumps(split_payload["hybrid_collab_matrix"], ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["hybrid_collab_matrix_json"] = hybrid_path

    verdict_path = out_dir / "judge_stack_verdict.json"
    verdict_path.write_text(json.dumps(verdict, ensure_ascii=False, indent=2), encoding="utf-8")
    artifacts["verdict_json"] = verdict_path

    lines = [
        "# TMCRA Judge Stack Full Validation",
        "",
        f"- Generated: {datetime.now().isoformat(timespec='seconds')}",
        f"- Classification: `{verdict.get('classification', 'unknown')}`",
        "",
        "## Six Generations",
        "",
    ]
    for item in generation_summary.get("summary", []) or []:
        lines.append(
            f"- {format_code_with_label('generation_id', item['generation_id'])}: best={item['best_total_score']}, history={item['history_query_accuracy']}, compare={item['compare_realization_accuracy']}, timeline={item['timeline_realization_accuracy']}, path={item['path_composition_accuracy']}"
        )
    lines.extend(["", "## Embedded Replacement", ""])
    for item in embedded_summary.get("summary", []) or []:
        lines.append(
            f"- {format_code_with_label('llm_profile', item['llm_profile'])} / {format_code_with_label('variant', item['variant'])}: total={item['total_score']}, reasoning={item['reasoning_quality_score']}, memory={item['memory_quality_score']}, efficiency={item['efficiency_score']}, gain={item['embedded_replacement_gain']}, matrix={item.get('matrix_kind', '')}, scope={item.get('replacement_scope', '')}, host_memory={item.get('host_memory_policy', '')}, host_reasoning={item.get('host_reasoning_policy', '')}, tmcra={item.get('tmcra_modules', [])}"
        )
    report_path = out_dir / "replacement_decision_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    artifacts["report_md"] = report_path
    return artifacts
