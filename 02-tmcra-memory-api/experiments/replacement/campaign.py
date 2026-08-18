from __future__ import annotations

import json
import re
from copy import deepcopy
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from .result_labels import annotate_result_payload, format_code_with_label


DEFAULT_OFFICIAL_FULL_BASELINE_DIR = Path(
    r"D:\ai tr\ai12\tmcra-integrated\outputs\replacement_eval\remote_sync_20260330\remote_full_eval_20260329_final"
)
DEFAULT_REMOTE_BASELINE_CANDIDATE = Path("/opt/tmcra-data/evaluations/outputs/replacement_eval/remote_full_eval_20260329_final")

DEFAULT_STAGE_SPECS: List[Dict[str, Any]] = [
    {"stage_id": "smoke", "title": "本地 Smoke", "weight": 0.05},
    {"stage_id": "static", "title": "660 静态正式回归", "weight": 0.10},
    {"stage_id": "full", "title": "训练机正式全量", "weight": 0.45},
    {"stage_id": "matrix", "title": "模块替换矩阵", "weight": 0.20},
    {"stage_id": "public", "title": "公开 Benchmark", "weight": 0.15},
    {"stage_id": "userflow", "title": "真实使用测试", "weight": 0.05},
]

DEFAULT_SMOKE_CASE_PREFIXES: Sequence[str] = (
    "history",
    "overwrite",
    "summary",
    "foundation",
    "terminology",
    "termkeep",
    "termredef",
    "delayed",
    "zhstate",
    "zhoverwrite",
    "zhterm",
    "path",
    "multipath",
    "crosslevel",
    "constraintpath",
    "counterfactual",
    "multibranch",
    "nonintuitive",
    "missingreason",
    "entity",
    "multisource",
    "alias",
    "scimathemo",
)

DEFAULT_PUBLIC_BENCHMARKS: List[Dict[str, Any]] = [
    {
        "benchmark_name": "LongMemEval",
        "category": "public_memory",
        "source_url": "https://github.com/xiaowu0162/LongMemEval",
        "rank_scope_default": "local matrix",
    },
    {
        "benchmark_name": "LoCoMo",
        "category": "public_memory",
        "source_url": "https://aclanthology.org/2024.acl-long.747/",
        "rank_scope_default": "local matrix",
    },
    {
        "benchmark_name": "Letta Leaderboard",
        "category": "public_memory",
        "source_url": "https://docs.letta.com/leaderboard",
        "rank_scope_default": "official",
    },
    {
        "benchmark_name": "RULER",
        "category": "public_long_context",
        "source_url": "https://github.com/NVIDIA/RULER",
        "rank_scope_default": "local matrix",
    },
    {
        "benchmark_name": "InfiniteBench",
        "category": "public_long_context",
        "source_url": "https://github.com/OpenBMB/InfiniteBench",
        "rank_scope_default": "local matrix",
    },
    {
        "benchmark_name": "MMLU-Pro",
        "category": "public_reasoning",
        "source_url": "https://github.com/TIGER-AI-Lab/MMLU-Pro",
        "rank_scope_default": "official",
    },
    {
        "benchmark_name": "GPQA",
        "category": "public_reasoning",
        "source_url": "https://arxiv.org/abs/2311.12022",
        "rank_scope_default": "official",
    },
    {
        "benchmark_name": "EQ-Bench",
        "category": "public_emotion",
        "source_url": "https://github.com/EQ-bench/EQ-Bench",
        "rank_scope_default": "official",
    },
]

DEFAULT_PUBLIC_EXECUTION_MODES: List[Dict[str, Any]] = [
    {
        "tmcra_mode": "native_full_context",
        "variant_name": "native_full_context",
        "matrix_kind": "baseline",
        "replacement_scope": "baseline",
        "reasoner_mode": "host_full_context",
        "host_memory_policy": "host_native",
        "host_reasoning_policy": "host_native",
        "host_judge_policy": "host_native",
        "judge_provider": "none",
        "judge_mode": "disabled",
        "base_assist_mode": "disabled",
        "fallback_policy": "disabled",
        "memory_variant": "full_history_memory",
        "tmcra_modules": [],
        "writeback_mode": "disabled",
        "writeback_provider": "none",
        "semantic_memory_writer_mode": "disabled",
        "semantic_memory_writer_use_host": False,
    },
    {
        "tmcra_mode": "llm_plus_tmcra_memory",
        "variant_name": "strict_memory_replace",
        "matrix_kind": "strict_replace_matrix",
        "replacement_scope": "absolute",
        "reasoner_mode": "host_full_context",
        "host_memory_policy": "disabled",
        "host_reasoning_policy": "host_native",
        "host_judge_policy": "host_native",
        "judge_provider": "none",
        "judge_mode": "disabled",
        "base_assist_mode": "disabled",
        "fallback_policy": "disabled",
        "memory_variant": "graph_session_memory_v2",
        "tmcra_modules": ["memory"],
        "writeback_mode": "enabled",
        "writeback_provider": "tmcra_writeback_judge",
        "semantic_memory_writer_mode": "required",
        "semantic_memory_writer_use_host": True,
    },
    {
        "tmcra_mode": "llm_plus_tmcra_judge",
        "variant_name": "strict_reasoning_judge_replace",
        "matrix_kind": "strict_replace_matrix",
        "replacement_scope": "absolute",
        "reasoner_mode": "tmcra_reasoning_strict",
        "host_memory_policy": "host_baseline",
        "host_reasoning_policy": "disabled",
        "host_judge_policy": "disabled",
        "judge_provider": "tmcra_judge",
        "judge_mode": "assist",
        "base_assist_mode": "disabled",
        "fallback_policy": "disabled",
        "memory_variant": "full_history_memory",
        "tmcra_modules": ["reasoning", "judge"],
        "writeback_mode": "enabled",
        "writeback_provider": "tmcra_writeback_judge",
        "semantic_memory_writer_mode": "disabled",
        "semantic_memory_writer_use_host": False,
    },
    {
        "tmcra_mode": "llm_plus_tmcra_memory_judge",
        "variant_name": "strict_memory_reasoning_judge_replace",
        "matrix_kind": "strict_replace_matrix",
        "replacement_scope": "absolute",
        "reasoner_mode": "tmcra_reasoning_strict",
        "host_memory_policy": "disabled",
        "host_reasoning_policy": "disabled",
        "host_judge_policy": "disabled",
        "judge_provider": "tmcra_judge",
        "judge_mode": "assist",
        "base_assist_mode": "disabled",
        "fallback_policy": "disabled",
        "memory_variant": "graph_session_memory_v2",
        "tmcra_modules": ["memory", "reasoning", "judge"],
        "writeback_mode": "enabled",
        "writeback_provider": "tmcra_writeback_judge",
        "semantic_memory_writer_mode": "required",
        "semantic_memory_writer_use_host": True,
    },
]

SPECIAL_SLICE_PREFIXES: Dict[str, Sequence[str]] = {
    "history_overwrite": ("history", "overwrite", "delayed", "termkeep", "termredef", "zhoverwrite", "zhterm"),
    "path_reasoning": (
        "path",
        "multipath",
        "constraintpath",
        "counterfactual",
        "crosslevel",
        "nonintuitive",
        "multibranch",
        "missingreason",
        "ambiguousreason",
        "causal4",
    ),
    "chinese": ("zhstate", "zhoverwrite", "zhterm"),
    "science_math_emotion": ("scimathemo",),
    "entity_multisource": ("entity", "multisource", "alias"),
}

DEFAULT_STATIC_FULL_GATE: Dict[str, Any] = {
    "reasoner": "tmcra_isolated_trimaze_v2",
    "memory": "graph_session_memory_v2",
    "min_answer_match": 0.85,
    "min_evidence_consistency_rate": 0.99,
    "max_unsupported_claim_rate": 0.0,
}


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _default_public_judge_manifest_path() -> str:
    outputs_root = Path(__file__).resolve().parents[2] / "outputs" / "replacement_eval"
    candidate_paths = [
        outputs_root / "judge_stageB_20260401c" / "tmcra_judge_stack" / "tmcra_judge_stack_manifest_v1.json",
        outputs_root / "judge_stageA_20260401b" / "tmcra_judge_stack" / "tmcra_judge_stack_manifest_v1.json",
        outputs_root / "judge_fix_20260401_coverage_v2" / "tmcra_judge_stack" / "tmcra_judge_stack_manifest_v1.json",
    ]
    for candidate in candidate_paths:
        if candidate.exists():
            return str(candidate)
    discovered = sorted(
        outputs_root.glob("**/tmcra_judge_stack_manifest_v1.json"),
        key=lambda path: path.stat().st_mtime,
        reverse=True,
    )
    if discovered:
        return str(discovered[0])
    return str(candidate_paths[0])


def _default_public_writeback_manifest_path() -> str:
    return str(
        Path(__file__).resolve().parents[2]
        / "outputs"
        / "replacement_eval"
        / "tmcra_writeback_judge"
        / "tmcra_writeback_judge_manifest_v1.json"
    )


def atomic_write_json(path: str | Path, payload: Any) -> Path:
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    temp_path = target.with_suffix(target.suffix + ".tmp")
    temp_path.write_text(json.dumps(annotate_result_payload(payload), ensure_ascii=False, indent=2), encoding="utf-8")
    temp_path.replace(target)
    return target


def load_json(path: str | Path, default: Any = None) -> Any:
    if path is None:
        return deepcopy(default)
    raw_path = str(path).strip()
    if not raw_path:
        return deepcopy(default)
    target = Path(path)
    if not target.exists():
        return deepcopy(default)
    try:
        return json.loads(target.read_text(encoding="utf-8"))
    except Exception:
        return deepcopy(default)


def resolve_baseline_dir(path: str | Path | None = None) -> Path:
    if path:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    repo_candidate = Path(__file__).resolve().parents[2] / "outputs" / "replacement_eval" / "remote_full_eval_20260329_final"
    for candidate in (repo_candidate, DEFAULT_OFFICIAL_FULL_BASELINE_DIR, DEFAULT_REMOTE_BASELINE_CANDIDATE):
        if Path(candidate).exists():
            return Path(candidate)
    return Path(path) if path else repo_candidate


def evaluate_static_full_gate(
    stage_dir: str | Path,
    *,
    reasoner: str = str(DEFAULT_STATIC_FULL_GATE["reasoner"]),
    memory: str = str(DEFAULT_STATIC_FULL_GATE["memory"]),
    min_answer_match: float = float(DEFAULT_STATIC_FULL_GATE["min_answer_match"]),
    min_evidence_consistency_rate: float = float(DEFAULT_STATIC_FULL_GATE["min_evidence_consistency_rate"]),
    max_unsupported_claim_rate: float = float(DEFAULT_STATIC_FULL_GATE["max_unsupported_claim_rate"]),
) -> Dict[str, Any]:
    stage_path = Path(stage_dir)
    leaderboard = load_json(stage_path / "leaderboard_v2.json", {"summary": []})
    summary_rows = list((leaderboard or {}).get("summary", []) or [])
    target_row = next(
        (
            dict(item)
            for item in summary_rows
            if str(item.get("reasoner", "")) == str(reasoner) and str(item.get("memory", "")) == str(memory)
        ),
        {},
    )
    metadata = dict(target_row.get("metadata", {}) or {})
    answer_match = float(metadata.get("answer_match", target_row.get("answer_match", 0.0)) or 0.0)
    evidence_consistency_rate = float(
        metadata.get("evidence_consistency_rate", target_row.get("evidence_consistency_rate", 0.0)) or 0.0
    )
    unsupported_claim_rate = float(
        metadata.get("unsupported_claim_rate", target_row.get("unsupported_claim_rate", 0.0)) or 0.0
    )
    available = bool(summary_rows)
    target_found = bool(target_row)
    checks = {
        "leaderboard_available": available,
        "target_found": target_found,
        "answer_match_ok": answer_match >= float(min_answer_match),
        "evidence_consistency_ok": evidence_consistency_rate >= float(min_evidence_consistency_rate),
        "unsupported_claim_ok": unsupported_claim_rate <= float(max_unsupported_claim_rate),
    }
    passed = all(bool(value) for value in checks.values())
    return {
        "stage_dir": str(stage_path),
        "passed": passed,
        "reasoner": str(reasoner),
        "memory": str(memory),
        "checks": checks,
        "thresholds": {
            "min_answer_match": float(min_answer_match),
            "min_evidence_consistency_rate": float(min_evidence_consistency_rate),
            "max_unsupported_claim_rate": float(max_unsupported_claim_rate),
        },
        "observed": {
            "answer_match": round(answer_match, 6),
            "evidence_consistency_rate": round(evidence_consistency_rate, 6),
            "unsupported_claim_rate": round(unsupported_claim_rate, 6),
        },
        "target_row": target_row,
    }


class StageProgressTracker:
    def __init__(self, path: str | Path, *, stage_id: str, stage_name: str, segments: Sequence[Mapping[str, Any]]):
        self.path = Path(path)
        self.stage_id = str(stage_id)
        self.stage_name = str(stage_name)
        self.stage_status = "pending"
        self.stage_message = ""
        self.current_segment = ""
        self.segments: Dict[str, Dict[str, Any]] = {}
        for item in segments:
            segment_id = str(item.get("segment_id") or "").strip()
            if not segment_id:
                continue
            self.segments[segment_id] = {
                "segment_id": segment_id,
                "title": str(item.get("title") or segment_id),
                "weight": float(item.get("weight", 1.0) or 1.0),
                "status": "pending",
                "percent": 0.0,
                "benchmark": "",
                "completed": 0,
                "total": 0,
                "updated_at": "",
            }
        self._write()

    def _overall_percent(self) -> float:
        total_weight = sum(float(item.get("weight", 1.0) or 1.0) for item in self.segments.values())
        if total_weight <= 0:
            return 0.0
        weighted = sum(float(item.get("weight", 1.0) or 1.0) * float(item.get("percent", 0.0) or 0.0) for item in self.segments.values())
        return round(weighted / total_weight, 6)

    def _payload(self) -> Dict[str, Any]:
        return {
            "stage_id": self.stage_id,
            "stage_name": self.stage_name,
            "stage_status": self.stage_status,
            "stage_message": self.stage_message,
            "current_segment": self.current_segment,
            "overall_percent": self._overall_percent(),
            "updated_at": now_iso(),
            "segments": list(self.segments.values()),
        }

    def _write(self) -> None:
        atomic_write_json(self.path, self._payload())

    def start(self, message: str = "") -> None:
        self.stage_status = "running"
        self.stage_message = message
        self._write()

    def update_segment(self, segment_id: str, payload: Mapping[str, Any]) -> None:
        key = str(segment_id or "progress")
        segment = self.segments.setdefault(
            key,
            {
                "segment_id": key,
                "title": key,
                "weight": 1.0,
                "status": "pending",
                "percent": 0.0,
                "benchmark": "",
                "completed": 0,
                "total": 0,
                "updated_at": "",
            },
        )
        event = dict(payload or {})
        segment["status"] = str(event.get("status", "running") or "running")
        percent_value = float(event.get("percent", 0.0) or 0.0)
        completed_value = int(event.get("completed", 0) or 0)
        total_value = int(event.get("total", 0) or 0)
        suite_index_value = int(event.get("suite_index", 0) or 0)
        suite_total_value = int(event.get("suite_total", 0) or 0)
        if percent_value <= 0.0 and total_value > 0 and completed_value > 0:
            inferred_percent = float(completed_value) / float(max(1, total_value))
            if suite_total_value > 0 and suite_index_value > 0:
                inferred_percent = (float(suite_index_value - 1) + inferred_percent) / float(max(1, suite_total_value))
            percent_value = inferred_percent
        segment["percent"] = round(percent_value, 6)
        segment["benchmark"] = str(event.get("benchmark", "") or "")
        segment["completed"] = completed_value
        segment["total"] = total_value
        segment["updated_at"] = str(event.get("updated_at", "") or now_iso())
        for field, value in event.items():
            if field not in {"status", "percent", "benchmark", "completed", "total", "updated_at"}:
                segment[field] = value
        self.stage_status = "running"
        self.current_segment = key
        self._write()

    def callback(self, payload: Mapping[str, Any]) -> None:
        segment_id = str(dict(payload or {}).get("benchmark", "") or self.current_segment or "progress")
        self.update_segment(segment_id, payload)

    def mark_segment(self, segment_id: str, *, status: str, percent: float = 1.0, **payload: Any) -> None:
        event = {"status": status, "percent": percent, **payload}
        self.update_segment(segment_id, event)

    def mark_failed(self, message: str) -> None:
        self.stage_status = "failed"
        self.stage_message = str(message)
        self._write()

    def mark_completed(self, message: str = "") -> None:
        for segment in self.segments.values():
            if float(segment.get("percent", 0.0) or 0.0) < 1.0 and str(segment.get("status", "")) != "skipped":
                segment["percent"] = 1.0
                segment["status"] = "completed"
                segment["updated_at"] = now_iso()
        self.stage_status = "completed"
        self.stage_message = str(message)
        self._write()


def default_public_benchmark_catalog() -> List[Dict[str, Any]]:
    return deepcopy(DEFAULT_PUBLIC_BENCHMARKS)


def default_public_execution_modes() -> List[Dict[str, Any]]:
    return deepcopy(DEFAULT_PUBLIC_EXECUTION_MODES)


def load_public_benchmark_catalog(path: str | Path | None = None) -> List[Dict[str, Any]]:
    if not path:
        return default_public_benchmark_catalog()
    payload = load_json(path, default_public_benchmark_catalog())
    if isinstance(payload, list):
        return [dict(item) for item in payload if isinstance(item, Mapping)]
    if isinstance(payload, Mapping):
        return [dict(item) for item in list(payload.get("benchmarks", []) or []) if isinstance(item, Mapping)]
    return default_public_benchmark_catalog()


def load_public_benchmark_rows(paths: Sequence[str | Path]) -> List[Dict[str, Any]]:
    rows: List[Dict[str, Any]] = []
    for raw_path in paths:
        payload = load_json(raw_path, {})
        if isinstance(payload, list):
            rows.extend([dict(item) for item in payload if isinstance(item, Mapping)])
            continue
        if isinstance(payload, Mapping) and isinstance(payload.get("summary"), list):
            rows.extend([dict(item) for item in payload.get("summary", []) if isinstance(item, Mapping)])
            continue
        if isinstance(payload, Mapping):
            rows.append(dict(payload))
    return rows


def _benchmark_slug(name: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "_", str(name or "").strip().lower()).strip("_")
    return slug or "benchmark"


def _path_matches_any(root: Path, patterns: Sequence[str]) -> bool:
    if not root.exists():
        return False
    for pattern in patterns:
        if next(root.glob(pattern), None) is not None:
            return True
    return False


def _path_matches_all(root: Path, patterns: Sequence[str]) -> bool:
    if not patterns:
        return False
    return all(_path_matches_any(root, (pattern,)) for pattern in patterns)


def _public_benchmark_dataset_status(benchmark_name: str, dataset_root: Path, *, dataset_file_count: int) -> tuple[str, str]:
    normalized = _benchmark_slug(benchmark_name)
    if dataset_file_count <= 0:
        return "missing", "no benchmark assets found"

    if normalized == "longmemeval":
        required = (
            "**/longmemeval_oracle.json",
            "**/longmemeval_s_cleaned.json",
            "**/longmemeval_m_cleaned.json",
        )
        if _path_matches_all(dataset_root, required):
            return "ready", "official LongMemEval json files present"
        return "partial", "repo present but official LongMemEval json files are still missing"

    if normalized == "locomo":
        if _path_matches_any(dataset_root, ("**/locomo10.json",)):
            return "ready", "LoCoMo conversation dataset files present"
        if _path_matches_any(dataset_root, ("**/*.json", "**/*.jsonl", "**/*.parquet")):
            return "partial", "LoCoMo assets found but core locomo10.json is still missing"
        return "missing", "LoCoMo dataset files are still missing"

    if normalized == "letta_leaderboard":
        if _path_matches_any(
            dataset_root,
            (
                "**/benchmarks/letta_bench/*.py",
                "**/benchmarks/memory_benchmarks/*.py",
                "**/examples/create_test_agent.py",
                "**/README.md",
            ),
        ):
            return "ready", "Letta leaderboard benchmark repo is present"
        if _path_matches_any(dataset_root, ("**/*.py", "**/*.md")):
            return "partial", "Letta benchmark assets found but benchmark entrypoints are incomplete"
        return "missing", "Letta leaderboard assets are missing"

    if normalized == "ruler":
        if _path_matches_any(dataset_root, ("**/scripts/run.sh", "**/scripts/data/prepare.sh")):
            return "ready", "RULER repo and generation scripts present"
        return "partial", "RULER root exists but generation scripts are incomplete"

    if normalized == "infinitebench":
        required = (
            "**/data/code_debug.jsonl",
            "**/data/code_run.jsonl",
            "**/data/kv_retrieval.jsonl",
            "**/data/longbook_choice_eng.jsonl",
            "**/data/longbook_qa_chn.jsonl",
            "**/data/longbook_qa_eng.jsonl",
            "**/data/longbook_sum_eng.jsonl",
            "**/data/longdialogue_qa_eng.jsonl",
            "**/data/math_calc.jsonl",
            "**/data/math_find.jsonl",
            "**/data/number_string.jsonl",
            "**/data/passkey.jsonl",
        )
        if _path_matches_all(dataset_root, required):
            return "ready", "InfiniteBench full task jsonl set present"
        if _path_matches_any(dataset_root, ("**/data/*.jsonl", "**/data/*.parquet")):
            return "partial", "InfiniteBench has partial task files but full task set is still incomplete"
        if _path_matches_any(dataset_root, ("**/data/collections.json", "**/scripts/download_dataset.sh")):
            return "partial", "InfiniteBench repo present but external dataset payload still missing"
        return "missing", "InfiniteBench assets are missing"

    if normalized == "mmlu_pro":
        required = (
            "**/data/test-00000-of-00001.parquet",
            "**/data/validation-00000-of-00001.parquet",
        )
        if _path_matches_all(dataset_root, required):
            return "ready", "MMLU-Pro parquet question payloads present"
        if _path_matches_any(dataset_root, ("**/*.parquet", "**/data/*.json", "**/data/*.jsonl")):
            return "partial", "MMLU-Pro has partial question payloads but parquet split is incomplete"
        if _path_matches_any(dataset_root, ("**/evaluate_from_api.py", "**/main.py")):
            return "partial", "MMLU-Pro repo present but question payloads are still missing"
        return "missing", "MMLU-Pro assets are missing"

    if normalized == "gpqa":
        if _path_matches_any(dataset_root, ("**/dataset.zip",)):
            return "ready", "GPQA dataset archive present"
        if _path_matches_any(dataset_root, ("**/README.md", "**/baselines/*.py")):
            return "partial", "GPQA repo present but dataset archive is still missing"
        return "missing", "GPQA assets are missing"

    if normalized == "eq_bench":
        if _path_matches_any(dataset_root, ("**/data/eq_bench_v2_questions_171.json", "**/data/eq_bench_v2_questions_171*.json")):
            return "ready", "EQ-Bench data files present"
        if _path_matches_any(dataset_root, ("**/eq-bench.py", "**/README.md")):
            return "partial", "EQ-Bench repo present but core data files are incomplete"
        return "missing", "EQ-Bench assets are missing"

    return "ready", "benchmark assets detected"


def _default_public_profile_payload(host_llm: str, mode_row: Mapping[str, Any]) -> Dict[str, Any]:
    judge_provider = str(mode_row.get("judge_provider", "") or "")
    judge_manifest_path = str(mode_row.get("judge_manifest_path", "") or "")
    if judge_provider == "tmcra_judge" and not judge_manifest_path:
        judge_manifest_path = _default_public_judge_manifest_path()

    writeback_provider = str(mode_row.get("writeback_provider", "") or "tmcra_writeback_judge")
    writeback_manifest_path = str(mode_row.get("writeback_manifest_path", "") or "")
    if writeback_provider == "tmcra_writeback_judge" and not writeback_manifest_path:
        writeback_manifest_path = _default_public_writeback_manifest_path()
    tmcra_modules = list(mode_row.get("tmcra_modules", []) or [])
    memory_enabled = "memory" in {str(item) for item in tmcra_modules}
    semantic_writer_mode = str(
        mode_row.get("semantic_memory_writer_mode", "")
        or ("required" if memory_enabled else "disabled")
    )

    return {
        "variant_name": str(mode_row.get("variant_name", "") or ""),
        "matrix_kind": str(mode_row.get("matrix_kind", "") or ""),
        "replacement_scope": str(mode_row.get("replacement_scope", "") or ""),
        "reasoner_mode": str(mode_row.get("reasoner_mode", "") or ""),
        "host_memory_policy": str(mode_row.get("host_memory_policy", "") or ""),
        "host_reasoning_policy": str(mode_row.get("host_reasoning_policy", "") or ""),
        "host_judge_policy": str(mode_row.get("host_judge_policy", "") or ""),
        "judge_provider": judge_provider,
        "judge_mode": str(mode_row.get("judge_mode", "") or ""),
        "base_assist_mode": str(mode_row.get("base_assist_mode", "") or ""),
        "fallback_policy": str(mode_row.get("fallback_policy", "") or "disabled"),
        "judge_manifest_path": judge_manifest_path,
        "judge_history_model_path": str(mode_row.get("judge_history_model_path", "") or ""),
        "judge_slot_model_path": str(mode_row.get("judge_slot_model_path", "") or ""),
        "judge_path_model_path": str(mode_row.get("judge_path_model_path", "") or ""),
        "memory_variant": str(mode_row.get("memory_variant", "") or ""),
        "tmcra_modules": tmcra_modules,
        "writeback_mode": str(mode_row.get("writeback_mode", "") or "enabled"),
        "writeback_provider": writeback_provider,
        "writeback_manifest_path": writeback_manifest_path,
        "semantic_memory_writer_mode": semantic_writer_mode,
        "semantic_memory_writer_use_host": bool(
            mode_row.get("semantic_memory_writer_use_host", True if memory_enabled else False)
        ),
        "semantic_memory_writer_model": str(mode_row.get("semantic_memory_writer_model", "") or ""),
        "semantic_memory_writer_base_url": str(mode_row.get("semantic_memory_writer_base_url", "") or ""),
        "semantic_memory_writer_api_key": str(mode_row.get("semantic_memory_writer_api_key", "") or ""),
        "semantic_memory_writer_timeout_seconds": float(mode_row.get("semantic_memory_writer_timeout_seconds", 120.0) or 120.0),
        "semantic_memory_writer_max_tokens": int(mode_row.get("semantic_memory_writer_max_tokens", 512) or 512),
        "semantic_memory_writer_max_proposals": int(mode_row.get("semantic_memory_writer_max_proposals", 4) or 4),
        "semantic_memory_writer_min_grounding_score": float(mode_row.get("semantic_memory_writer_min_grounding_score", 0.5) or 0.5),
        "host_llm": str(host_llm or "qwen7b"),
    }


def build_public_benchmark_prep(
    *,
    output_dir: str | Path,
    catalog: Sequence[Mapping[str, Any]],
    host_llm: str = "qwen7b",
    frozen_embed_root: str | Path | None = None,
    benchmark_data_root: str | Path | None = None,
    execution_modes: Sequence[Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    prep_root = Path(output_dir)
    data_root = (
        Path(benchmark_data_root)
        if benchmark_data_root
        else Path(__file__).resolve().parents[2] / "data" / "public_benchmarks"
    )
    frozen_root = Path(frozen_embed_root) if frozen_embed_root else None
    frozen_manifest_path = (frozen_root / "frozen_embed_manifest.json") if frozen_root else None
    frozen_manifest = load_json(frozen_manifest_path, {}) if frozen_manifest_path and frozen_manifest_path.exists() else {}

    frozen_variant_rows: Dict[str, Dict[str, Any]] = {}
    frozen_variant_paths: Dict[str, str] = {}
    if isinstance(frozen_manifest, Mapping):
        for group_name in ("strict_replace_matrix", "hybrid_collab_matrix"):
            for item in list(frozen_manifest.get(group_name, []) or []):
                if not isinstance(item, Mapping):
                    continue
                variant_name = str(item.get("variant_name", "") or "").strip()
                if not variant_name:
                    continue
                frozen_variant_rows[variant_name] = dict(item)
                candidate = frozen_root / group_name / variant_name / "execution_profile.json" if frozen_root else None
                frozen_variant_paths[variant_name] = str(candidate) if candidate and candidate.exists() else ""

    resolved_modes: List[Dict[str, Any]] = []
    for mode in list(execution_modes or default_public_execution_modes()):
        raw_mode = dict(mode)
        variant_name = str(raw_mode.get("variant_name", "") or "").strip()
        source_row = dict(frozen_variant_rows.get(variant_name, {}))
        profile_payload = _default_public_profile_payload(host_llm, {**source_row, **raw_mode})
        source_profile_path = frozen_variant_paths.get(variant_name, "")
        prepared_profile_path = prep_root / "profiles" / str(raw_mode.get("tmcra_mode", "mode")) / "execution_profile.json"
        profile_status = "ready"
        if variant_name != "native_full_context" and not source_profile_path:
            profile_status = "derived_without_frozen_source"
        resolved_modes.append(
            {
                "tmcra_mode": str(raw_mode.get("tmcra_mode", "") or ""),
                "variant_name": variant_name,
                "matrix_kind": str(profile_payload.get("matrix_kind", "") or ""),
                "replacement_scope": str(profile_payload.get("replacement_scope", "") or ""),
                "host_memory_policy": str(profile_payload.get("host_memory_policy", "") or ""),
                "host_reasoning_policy": str(profile_payload.get("host_reasoning_policy", "") or ""),
                "host_judge_policy": str(profile_payload.get("host_judge_policy", "") or ""),
                "judge_provider": str(profile_payload.get("judge_provider", "") or ""),
                "judge_mode": str(profile_payload.get("judge_mode", "") or ""),
                "memory_variant": str(profile_payload.get("memory_variant", "") or ""),
                "tmcra_modules": list(profile_payload.get("tmcra_modules", []) or []),
                "writeback_mode": str(profile_payload.get("writeback_mode", "") or "enabled"),
                "writeback_provider": str(profile_payload.get("writeback_provider", "") or "tmcra_writeback_judge"),
                "writeback_manifest_path": str(profile_payload.get("writeback_manifest_path", "") or ""),
                "semantic_memory_writer_mode": str(profile_payload.get("semantic_memory_writer_mode", "") or ""),
                "semantic_memory_writer_use_host": bool(profile_payload.get("semantic_memory_writer_use_host", False)),
                "source_profile_path": str(source_profile_path or ""),
                "prepared_profile_path": str(prepared_profile_path),
                "profile_status": profile_status,
                "profile_payload": profile_payload,
            }
        )

    for mode in resolved_modes:
        prepared_profile_path = str(mode.get("prepared_profile_path", "") or "")
        if prepared_profile_path:
            atomic_write_json(prepared_profile_path, dict(mode.get("profile_payload", {}) or {}))

    planned_rows: List[Dict[str, Any]] = []
    benchmark_rows: List[Dict[str, Any]] = []
    for item in list(catalog or []):
        catalog_item = dict(item)
        benchmark_name = str(catalog_item.get("benchmark_name", "") or "").strip()
        if not benchmark_name:
            continue
        benchmark_slug = _benchmark_slug(benchmark_name)
        dataset_root = data_root / benchmark_slug
        dataset_exists = dataset_root.exists()
        dataset_file_count = sum(1 for child in dataset_root.rglob("*") if child.is_file()) if dataset_exists else 0
        dataset_status, dataset_detail = _public_benchmark_dataset_status(
            benchmark_name,
            dataset_root,
            dataset_file_count=dataset_file_count,
        )
        benchmark_dir = prep_root / "benchmarks" / benchmark_slug
        planned_runs: List[Dict[str, Any]] = []
        for mode in resolved_modes:
            tmcra_mode = str(mode.get("tmcra_mode", "") or "")
            run_dir = benchmark_dir / tmcra_mode
            row = {
                "benchmark_name": benchmark_name,
                "benchmark_slug": benchmark_slug,
                "category": str(catalog_item.get("category", "") or ""),
                "host_llm": str(host_llm or "qwen7b"),
                "tmcra_mode": tmcra_mode,
                "variant_name": str(mode.get("variant_name", "") or ""),
                "matrix_kind": str(mode.get("matrix_kind", "") or ""),
                "replacement_scope": str(mode.get("replacement_scope", "") or ""),
                "system_name": f"{host_llm or 'qwen7b'}::{tmcra_mode}",
                "score": None,
                "official_public_rank": None,
                "official_total_models": None,
                "local_eval_rank": None,
                "local_eval_pool": 0,
                "rank_scope": str(catalog_item.get("rank_scope_default", "official") or "official"),
                "leaderboard_date": None,
                "source_url": str(catalog_item.get("source_url", "") or ""),
                "dataset_root": str(dataset_root),
                "dataset_status": dataset_status,
                "dataset_detail": dataset_detail,
                "dataset_file_count": dataset_file_count,
                "profile_status": str(mode.get("profile_status", "") or ""),
                "prepared_profile_path": str(mode.get("prepared_profile_path", "") or ""),
                "source_profile_path": str(mode.get("source_profile_path", "") or ""),
                "writeback_mode": str(mode.get("writeback_mode", "") or "enabled"),
                "writeback_provider": str(mode.get("writeback_provider", "") or "tmcra_writeback_judge"),
                "writeback_manifest_path": str(mode.get("writeback_manifest_path", "") or ""),
                "semantic_memory_writer_mode": str(mode.get("semantic_memory_writer_mode", "") or ""),
                "semantic_memory_writer_use_host": bool(mode.get("semantic_memory_writer_use_host", False)),
                "result_path": str(run_dir / "result.json"),
                "log_path": str(run_dir / "run.log"),
                "progress_path": str(run_dir / "progress.json"),
                "upload_bundle_path": str(run_dir / "upload_bundle.json"),
                "llm_prompt_tokens": 0,
                "llm_completion_tokens": 0,
                "llm_total_tokens": 0,
                "judge_prompt_tokens": 0,
                "judge_completion_tokens": 0,
                "judge_total_tokens": 0,
                "combined_prompt_tokens": 0,
                "combined_completion_tokens": 0,
                "combined_total_tokens": 0,
                "avg_llm_total_tokens": 0.0,
                "avg_judge_total_tokens": 0.0,
                "avg_combined_total_tokens": 0.0,
            }
            planned_rows.append(dict(row))
            planned_runs.append(dict(row))
        benchmark_rows.append(
            {
                "benchmark_name": benchmark_name,
                "benchmark_slug": benchmark_slug,
                "category": str(catalog_item.get("category", "") or ""),
                "source_url": str(catalog_item.get("source_url", "") or ""),
                "rank_scope_default": str(catalog_item.get("rank_scope_default", "official") or "official"),
                "dataset_root": str(dataset_root),
                "dataset_status": dataset_status,
                "dataset_detail": dataset_detail,
                "dataset_file_count": dataset_file_count,
                "planned_runs": planned_runs,
            }
        )

    return {
        "generated_at": now_iso(),
        "output_dir": str(prep_root),
        "host_llm": str(host_llm or "qwen7b"),
        "frozen_embed_root": str(frozen_root) if frozen_root else "",
        "frozen_manifest_path": str(frozen_manifest_path) if frozen_manifest_path and frozen_manifest_path.exists() else "",
        "benchmark_data_root": str(data_root),
        "execution_modes": resolved_modes,
        "benchmarks": benchmark_rows,
        "summary": build_public_benchmark_summary(catalog=catalog, rows=planned_rows),
    }


def build_public_benchmark_summary(*, catalog: Sequence[Mapping[str, Any]], rows: Sequence[Mapping[str, Any]]) -> Dict[str, Any]:
    catalog_map = {str(item.get("benchmark_name", "")).strip(): dict(item) for item in catalog}
    merged_rows: List[Dict[str, Any]] = []

    for row in rows:
        benchmark_name = str(row.get("benchmark_name", "")).strip()
        catalog_item = dict(catalog_map.get(benchmark_name, {}))
        merged_rows.append(
            {
                "benchmark_name": benchmark_name,
                "host_llm": str(row.get("host_llm", "qwen7b") or "qwen7b"),
                "tmcra_mode": str(row.get("tmcra_mode", "none") or "none"),
                "system_name": str(row.get("system_name", "") or f"{row.get('host_llm', 'qwen7b')}::{row.get('tmcra_mode', 'none')}"),
                "score": row.get("score"),
                "official_public_rank": row.get("official_public_rank"),
                "official_total_models": row.get("official_total_models"),
                "local_eval_rank": None,
                "local_eval_pool": 0,
                "rank_scope": str(row.get("rank_scope", catalog_item.get("rank_scope_default", "local matrix")) or "local matrix"),
                "leaderboard_date": row.get("leaderboard_date"),
                "source_url": str(row.get("source_url", catalog_item.get("source_url", "")) or ""),
                "llm_prompt_tokens": int(row.get("llm_prompt_tokens", 0) or 0),
                "llm_completion_tokens": int(row.get("llm_completion_tokens", 0) or 0),
                "llm_total_tokens": int(row.get("llm_total_tokens", 0) or 0),
                "judge_prompt_tokens": int(row.get("judge_prompt_tokens", 0) or 0),
                "judge_completion_tokens": int(row.get("judge_completion_tokens", 0) or 0),
                "judge_total_tokens": int(row.get("judge_total_tokens", 0) or 0),
                "combined_prompt_tokens": int(row.get("combined_prompt_tokens", 0) or 0),
                "combined_completion_tokens": int(row.get("combined_completion_tokens", 0) or 0),
                "combined_total_tokens": int(row.get("combined_total_tokens", 0) or 0),
                "avg_llm_total_tokens": float(row.get("avg_llm_total_tokens", 0.0) or 0.0),
                "avg_judge_total_tokens": float(row.get("avg_judge_total_tokens", 0.0) or 0.0),
                "avg_combined_total_tokens": float(row.get("avg_combined_total_tokens", 0.0) or 0.0),
                "variant_name": str(row.get("variant_name", "") or ""),
                "matrix_kind": str(row.get("matrix_kind", "") or ""),
                "replacement_scope": str(row.get("replacement_scope", "") or ""),
                "profile_status": str(row.get("profile_status", "") or ""),
                "dataset_status": str(row.get("dataset_status", "") or ""),
                "dataset_detail": str(row.get("dataset_detail", "") or ""),
                "dataset_root": str(row.get("dataset_root", "") or ""),
                "prepared_profile_path": str(row.get("prepared_profile_path", "") or ""),
                "source_profile_path": str(row.get("source_profile_path", "") or ""),
                "writeback_mode": str(row.get("writeback_mode", "") or ""),
                "writeback_provider": str(row.get("writeback_provider", "") or ""),
                "writeback_manifest_path": str(row.get("writeback_manifest_path", "") or ""),
                "semantic_memory_writer_mode": str(row.get("semantic_memory_writer_mode", "") or ""),
                "semantic_memory_writer_use_host": bool(row.get("semantic_memory_writer_use_host", False)),
                "result_path": str(row.get("result_path", "") or ""),
                "log_path": str(row.get("log_path", "") or ""),
                "progress_path": str(row.get("progress_path", "") or ""),
                "upload_bundle_path": str(row.get("upload_bundle_path", "") or ""),
                "upload_ready": bool(row.get("upload_ready", False)),
                "official_submission_supported": bool(row.get("official_submission_supported", False)),
            }
        )

    for benchmark_name, catalog_item in catalog_map.items():
        if any(str(item.get("benchmark_name", "")).strip() == benchmark_name for item in merged_rows):
            continue
        merged_rows.append(
            {
                "benchmark_name": benchmark_name,
                "host_llm": "qwen7b",
                "tmcra_mode": "planned",
                "system_name": "qwen7b::planned",
                "score": None,
                "official_public_rank": None,
                "official_total_models": None,
                "local_eval_rank": None,
                "local_eval_pool": 0,
                "rank_scope": str(catalog_item.get("rank_scope_default", "official") or "official"),
                "leaderboard_date": None,
                "source_url": str(catalog_item.get("source_url", "") or ""),
                "llm_prompt_tokens": 0,
                "llm_completion_tokens": 0,
                "llm_total_tokens": 0,
                "judge_prompt_tokens": 0,
                "judge_completion_tokens": 0,
                "judge_total_tokens": 0,
                "combined_prompt_tokens": 0,
                "combined_completion_tokens": 0,
                "combined_total_tokens": 0,
                "avg_llm_total_tokens": 0.0,
                "avg_judge_total_tokens": 0.0,
                "avg_combined_total_tokens": 0.0,
                "variant_name": "",
                "matrix_kind": "",
                "replacement_scope": "",
                "profile_status": "",
                "dataset_status": "",
                "dataset_detail": "",
                "dataset_root": "",
                "prepared_profile_path": "",
                "source_profile_path": "",
                "writeback_mode": "",
                "writeback_provider": "",
                "writeback_manifest_path": "",
                "result_path": "",
                "log_path": "",
                "progress_path": "",
                "upload_bundle_path": "",
                "upload_ready": False,
                "official_submission_supported": bool(str(catalog_item.get("rank_scope_default", "official") or "official") == "official"),
            }
        )

    grouped: Dict[str, List[Dict[str, Any]]] = {}
    for row in merged_rows:
        grouped.setdefault(str(row.get("benchmark_name", "")), []).append(row)
    for group in grouped.values():
        ranked = [item for item in group if item.get("score") is not None]
        ranked.sort(key=lambda item: float(item.get("score", 0.0) or 0.0), reverse=True)
        pool_size = len(ranked)
        for index, item in enumerate(ranked, start=1):
            item["local_eval_rank"] = index
            item["local_eval_pool"] = pool_size
        for item in group:
            if item.get("local_eval_rank") is None:
                item["local_eval_pool"] = pool_size

    merged_rows.sort(
        key=lambda item: (
            str(item.get("benchmark_name", "")),
            int(item.get("local_eval_rank") or 9999),
            str(item.get("system_name", "")),
        )
    )
    return {"summary": merged_rows}


def _avg(records: Sequence[Mapping[str, Any]], key: str) -> float:
    if not records:
        return 0.0
    return round(sum(float(item.get(key, 0.0) or 0.0) for item in records) / max(1, len(records)), 6)


def build_special_slices(
    *,
    static_results: Mapping[str, Any] | None = None,
    long_dialog_results: Mapping[str, Any] | None = None,
    scaling_results: Mapping[str, Any] | None = None,
    tunneling_results: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    static_cases = list((static_results or {}).get("cases", []) or [])
    slice_rows: List[Dict[str, Any]] = []
    for slice_name, prefixes in SPECIAL_SLICE_PREFIXES.items():
        grouped: Dict[tuple[str, str], List[Dict[str, Any]]] = {}
        for record in static_cases:
            case_id = str(record.get("case_id", "") or "")
            if not any(case_id.startswith(prefix) for prefix in prefixes):
                continue
            grouped.setdefault((str(record.get("reasoner", "")), str(record.get("memory", ""))), []).append(dict(record))
        for (reasoner, memory), records in grouped.items():
            slice_rows.append(
                {
                    "slice": slice_name,
                    "reasoner": reasoner,
                    "memory": memory,
                    "cases": len(records),
                    "avg_reasoning_quality_score": _avg(records, "reasoning_quality_score"),
                    "avg_answer_match": _avg(records, "answer_match"),
                    "avg_memory_correctness": _avg(records, "memory_correctness"),
                    "avg_overwrite_resolution": _avg(records, "overwrite_resolution"),
                    "avg_path_composition_accuracy": _avg(records, "path_composition_accuracy"),
                    "avg_entity_isolation_accuracy": _avg(records, "entity_isolation_accuracy"),
                    "avg_temporal_consistency_score": _avg(records, "temporal_consistency_score"),
                    "avg_unsupported_claim_rate": _avg(records, "unsupported_claim_rate"),
                }
            )

    return {
        "summary": slice_rows,
        "resources": {
            "long_dialog": list((long_dialog_results or {}).get("summary", []) or []),
            "scaling": list((scaling_results or {}).get("summary", []) or []),
            "tunneling": list((tunneling_results or {}).get("summary", []) or []),
            "static_subsets": list((static_results or {}).get("subsets", []) or []),
        },
    }


def build_official_full_summary(stage_dir: str | Path, *, baseline_dir: str | Path | None = None) -> Dict[str, Any]:
    stage_path = Path(stage_dir)
    leaderboard = load_json(stage_path / "leaderboard_v2.json", {"summary": []})
    verdict = load_json(stage_path / "TMCRA_replacement_verdict.json", {})
    failure_slices = load_json(stage_path / "failure_slices.json", {})
    token_usage_summary = load_json(stage_path / "token_usage_summary.json", {})
    baseline = Path(baseline_dir) if baseline_dir else None
    baseline_leaderboard = load_json(baseline / "leaderboard_v2.json", {"summary": []}) if baseline else {"summary": []}
    return {
        "output_dir": str(stage_path),
        "available": bool(list(leaderboard.get("summary", []) or [])),
        "top_entry": dict((leaderboard.get("summary", []) or [{}])[0] or {}),
        "leaderboard": leaderboard,
        "verdict": verdict,
        "failure_slices": failure_slices,
        "token_usage_summary": token_usage_summary,
        "baseline_output_dir": str(baseline) if baseline else "",
        "baseline_leaderboard": baseline_leaderboard,
    }


def build_replacement_matrix_summary(stage_dir: str | Path) -> Dict[str, Any]:
    stage_path = Path(stage_dir)
    generation = load_json(stage_path / "generation_leaderboard.json", {"summary": []})
    embedded = load_json(stage_path / "embedded_replacement_matrix.json", {"summary": []})
    strict_replace = load_json(stage_path / "strict_replace_matrix.json", {"summary": [], "baseline_rows": []})
    hybrid_collab = load_json(stage_path / "hybrid_collab_matrix.json", {"summary": [], "baseline_rows": []})
    verdict = load_json(stage_path / "judge_stack_verdict.json", {})
    token_usage_summary = load_json(stage_path / "token_usage_summary.json", {})
    return {
        "output_dir": str(stage_path),
        "available": bool(
            list(generation.get("summary", []) or [])
            or list(embedded.get("summary", []) or [])
            or list(strict_replace.get("summary", []) or [])
            or list(hybrid_collab.get("summary", []) or [])
        ),
        "generation_leaderboard": generation,
        "embedded_replacement_matrix": embedded,
        "strict_replace_matrix": strict_replace,
        "hybrid_collab_matrix": hybrid_collab,
        "judge_stack_verdict": verdict,
        "token_usage_summary": token_usage_summary,
    }


def build_campaign_manifest(
    *,
    output_dir: str | Path,
    baseline_full_dir: str | Path | None = None,
    stage_specs: Sequence[Mapping[str, Any]] | None = None,
    public_host_llm: str = "qwen7b",
    frozen_embed_root: str | Path | None = None,
) -> Dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "output_dir": str(Path(output_dir)),
        "baseline_full_dir": str(resolve_baseline_dir(baseline_full_dir)),
        "public_host_llm": str(public_host_llm or "qwen7b"),
        "frozen_embed_root": str(Path(frozen_embed_root)) if frozen_embed_root else "",
        "stages": [
            {
                "stage_id": str(item.get("stage_id", "")),
                "title": str(item.get("title", item.get("stage_id", ""))),
                "weight": float(item.get("weight", 0.0) or 0.0),
                "status": "pending",
                "percent": 0.0,
                "output_dir": "",
                "progress_json": "",
                "log_path": "",
                "command": [],
                "started_at": "",
                "finished_at": "",
            }
            for item in list(stage_specs or DEFAULT_STAGE_SPECS)
        ],
    }


def load_stage_progress(path: str | Path) -> Dict[str, Any]:
    payload = load_json(path, {})
    if not isinstance(payload, Mapping):
        return {}
    return dict(payload)


def build_campaign_progress(
    *,
    manifest: Mapping[str, Any],
    stage_progress_map: Mapping[str, Mapping[str, Any]] | None = None,
) -> Dict[str, Any]:
    progress_map = {str(key): dict(value) for key, value in dict(stage_progress_map or {}).items()}
    stages: List[Dict[str, Any]] = []
    total_weight = 0.0
    weighted_percent = 0.0

    for stage in list(manifest.get("stages", []) or []):
        stage_row = dict(stage)
        stage_id = str(stage_row.get("stage_id", ""))
        payload = dict(progress_map.get(stage_id, {}))
        status = str(payload.get("stage_status", stage_row.get("status", "pending")) or "pending")
        percent = round(float(payload.get("overall_percent", stage_row.get("percent", 0.0)) or 0.0), 6)
        weight = float(stage_row.get("weight", 0.0) or 0.0)
        total_weight += weight
        weighted_percent += weight * percent
        stage_row.update(
            {
                "status": status,
                "percent": percent,
                "stage_message": str(payload.get("stage_message", stage_row.get("stage_message", "")) or ""),
                "current_segment": str(payload.get("current_segment", stage_row.get("current_segment", "")) or ""),
                "updated_at": str(payload.get("updated_at", stage_row.get("updated_at", "")) or now_iso()),
                "segments": list(payload.get("segments", []) or stage_row.get("segments", []) or []),
            }
        )
        stages.append(stage_row)

    overall_percent = round(weighted_percent / total_weight, 6) if total_weight > 0 else 0.0
    overall_status = "pending"
    if any(str(stage.get("status", "")) == "failed" for stage in stages):
        overall_status = "failed"
    elif stages and all(str(stage.get("status", "")) in {"completed", "skipped"} for stage in stages):
        overall_status = "completed"
    elif any(str(stage.get("status", "")) == "running" for stage in stages):
        overall_status = "running"

    current_stage = next((stage for stage in stages if str(stage.get("status", "")) == "running"), None)
    if current_stage is None:
        current_stage = next((stage for stage in stages if str(stage.get("status", "")) not in {"completed", "skipped"}), None)

    return {
        "generated_at": str(manifest.get("generated_at", "") or now_iso()),
        "updated_at": now_iso(),
        "output_dir": str(manifest.get("output_dir", "")),
        "baseline_full_dir": str(manifest.get("baseline_full_dir", "")),
        "public_host_llm": str(manifest.get("public_host_llm", "qwen7b") or "qwen7b"),
        "frozen_embed_root": str(manifest.get("frozen_embed_root", "") or ""),
        "overall_status": overall_status,
        "overall_percent": overall_percent,
        "current_stage": str((current_stage or {}).get("stage_id", "") or ""),
        "current_stage_title": str((current_stage or {}).get("title", "") or ""),
        "stages": stages,
    }


def build_overall_leaderboard(
    *,
    official_full_summary: Mapping[str, Any] | None = None,
    replacement_matrix: Mapping[str, Any] | None = None,
    special_slices: Mapping[str, Any] | None = None,
    public_summary: Mapping[str, Any] | None = None,
) -> Dict[str, Any]:
    return {
        "generated_at": now_iso(),
        "official_full": dict(official_full_summary or {}),
        "replacement_matrix": dict(replacement_matrix or {}),
        "special_slices": dict(special_slices or {}),
        "public_benchmarks": dict(public_summary or {}),
    }


def write_campaign_outputs(
    *,
    output_dir: str | Path,
    manifest: Mapping[str, Any],
    overall_leaderboard: Mapping[str, Any],
    official_full_summary: Mapping[str, Any],
    replacement_matrix: Mapping[str, Any],
    special_slices: Mapping[str, Any],
    public_benchmark_summary: Mapping[str, Any],
) -> Dict[str, Path]:
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    artifacts: Dict[str, Path] = {}
    artifacts["campaign_manifest_json"] = atomic_write_json(out_dir / "campaign_manifest.json", manifest)
    artifacts["overall_leaderboard_json"] = atomic_write_json(out_dir / "overall_leaderboard.json", overall_leaderboard)
    artifacts["official_full_summary_json"] = atomic_write_json(out_dir / "official_full_summary.json", official_full_summary)
    artifacts["replacement_matrix_json"] = atomic_write_json(out_dir / "replacement_matrix.json", replacement_matrix)
    artifacts["special_slices_json"] = atomic_write_json(out_dir / "special_slices.json", special_slices)
    artifacts["public_benchmark_summary_json"] = atomic_write_json(out_dir / "public_benchmark_summary.json", public_benchmark_summary)

    lines = [
        "# TMCRA Overall Formal Test Campaign",
        "",
        f"- Generated: {now_iso()}",
        f"- Baseline full dir: `{manifest.get('baseline_full_dir', '')}`",
        f"- Public host LLM: {format_code_with_label('public_host_llm', manifest.get('public_host_llm', 'qwen7b'))}",
        "",
        "## Stage Status",
        "",
    ]
    for stage in list(manifest.get("stages", []) or []):
        lines.append(
            f"- `{stage.get('stage_id')}` `{stage.get('title')}`: status={stage.get('status', 'pending')}, output_dir={stage.get('output_dir', '')}"
        )

    leaderboard_rows = list((official_full_summary.get("leaderboard", {}) or {}).get("summary", []) or [])
    top_entry = dict((leaderboard_rows[0] if leaderboard_rows else {}) or {})
    if top_entry:
        lines.extend(
            [
                "",
                "## Official Full",
                "",
                f"- top reasoner={format_code_with_label('reasoner', top_entry.get('reasoner', ''))} memory={format_code_with_label('memory', top_entry.get('memory', ''))} total={top_entry.get('total_score', '')}",
                f"- classification=`{official_full_summary.get('verdict', {}).get('classification', 'unknown')}`",
            ]
        )

    generation_rows = list((replacement_matrix.get("generation_leaderboard", {}) or {}).get("summary", []) or [])
    embedded_rows = list((replacement_matrix.get("embedded_replacement_matrix", {}) or {}).get("summary", []) or [])
    if generation_rows or embedded_rows:
        lines.extend(["", "## Replacement Matrix", ""])
        for item in generation_rows[:6]:
            lines.append(
                f"- generation {format_code_with_label('generation_id', item.get('generation_id', ''))}: best={item.get('best_total_score', '')}, history={item.get('history_query_accuracy', '')}, path={item.get('path_composition_accuracy', '')}"
            )
        for item in embedded_rows[:8]:
            lines.append(
                f"- embedded {format_code_with_label('llm_profile', item.get('llm_profile', ''))} / {format_code_with_label('variant', item.get('variant', ''))}: total={item.get('total_score', '')}, gain={item.get('embedded_replacement_gain', '')}"
            )

    lines.extend(["", "## Public Benchmark Rankings", ""])
    for item in list(public_benchmark_summary.get("summary", []) or []):
        lines.append(
            f"- `{item.get('benchmark_name', '')}` / `{item.get('system_name', '')}`: score={item.get('score', 'N/A')}, official_rank={item.get('official_public_rank', 'N/A')}, local_rank={item.get('local_eval_rank', 'N/A')}, scope={item.get('rank_scope', '')}, combined_tokens={item.get('combined_total_tokens', 0)}, avg_combined_tokens={item.get('avg_combined_total_tokens', 0)}, source={item.get('source_url', '')}"
        )

    report_path = out_dir / "overall_decision_report.md"
    report_path.write_text("\n".join(lines), encoding="utf-8")
    artifacts["overall_decision_report_md"] = report_path
    return artifacts
