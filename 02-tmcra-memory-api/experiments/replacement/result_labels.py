from __future__ import annotations

from typing import Any, Dict, Iterable


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


_UNKNOWN_COMMENT = "未登记代号，当前先保留原值；如果这是新模块名，需要补充到 result_labels.py。"


REASONER_LABELS: Dict[str, Dict[str, str]] = {
    "generation_1_old_formal_baseline": {
        "label": "旧正式基线推理链",
        "comment": "旧正式结果对应的老推理链，用来做旧正式基线对比。",
    },
    "tmcra_isolated_trimaze_v2": {
        "label": "新算法主推理链",
        "comment": "TMCRA 当前主推理模块，负责路径搜索、证据约束和结构化回答生成。",
    },
    "tmcra_isolated_trimaze_tunneling_on": {
        "label": "新算法主推理链（隧穿开启）",
        "comment": "和新算法主推理链相同，但额外开启 tunneling 路径能力。",
    },
    "tmcra_isolated_trimaze_tunneling_off": {
        "label": "新算法主推理链（隧穿关闭）",
        "comment": "和新算法主推理链相同，但关闭 tunneling 路径能力。",
    },
    "direct_extraction_reasoner": {
        "label": "旧直接抽取推理",
        "comment": "较弱的直接抽取型推理基线，用来衡量没有强路径搜索时的表现。",
    },
    "openai_compat_qwen7b": {
        "label": "Qwen 7B 证据约束推理",
        "comment": "Qwen 7B 宿主接入 TMCRA 证据/记忆约束后的推理形态。",
    },
    "openai_compat_qwen7b_full_context": {
        "label": "Qwen 7B 原生全上下文",
        "comment": "不嵌入 TMCRA 模块的 Qwen 7B 原生全上下文回答模式。",
    },
    "openai_compat_deepseek7b": {
        "label": "DeepSeek 7B 证据约束推理",
        "comment": "DeepSeek 7B 宿主接入 TMCRA 证据/记忆约束后的推理形态。",
    },
    "openai_compat_deepseek7b_full_context": {
        "label": "DeepSeek 7B 原生全上下文",
        "comment": "不嵌入 TMCRA 模块的 DeepSeek 7B 原生全上下文回答模式。",
    },
    "openai_compat_gemma4e4b": {
        "label": "Gemma 4 E4B evidence-constrained",
        "comment": "Gemma host model routed through TMCRA grounded memory and evidence constraints.",
    },
    "openai_compat_gemma4e4b_cot": {
        "label": "Gemma 4 E4B evidence-constrained CoT",
        "comment": "Gemma host model using the constrained chain-of-thought wrapper over TMCRA evidence.",
    },
    "openai_compat_gemma4e4b_full_context": {
        "label": "Gemma 4 E4B native full-context",
        "comment": "Gemma host model answering from its native full-context prompt without TMCRA replacement logic.",
    },
}

MEMORY_LABELS: Dict[str, Dict[str, str]] = {
    "graph_session_memory": {
        "label": "图会话记忆旧版",
        "comment": "旧版图式会话记忆实现。",
    },
    "graph_session_memory_v2": {
        "label": "新记忆算法",
        "comment": "你当前主推的最新记忆模块，图式会话记忆 v2。",
    },
    "summary_window_memory": {
        "label": "摘要窗口记忆基线",
        "comment": "通过摘要窗口保留上下文的常规记忆基线。",
    },
    "vector_rag_memory": {
        "label": "向量检索记忆基线",
        "comment": "通过向量召回做历史检索的常规 RAG 记忆基线。",
    },
    "full_history_memory": {
        "label": "全历史拼接记忆基线",
        "comment": "把完整历史直接拼给系统的朴素基线。",
    },
    "null_memory": {
        "label": "空记忆",
        "comment": "关闭记忆能力，仅保留无记忆回答。",
    },
}

JUDGE_PROVIDER_LABELS: Dict[str, Dict[str, str]] = {
    "none": {
        "label": "纯代码判定",
        "comment": "不调用外部判定模型，仅依赖规则和代码逻辑裁决。",
    },
    "llm_assist": {
        "label": "旧辅助判定",
        "comment": "调用外部轻量大模型辅助裁决，具体模型看 profile 或 variant。",
    },
    "tmcra_judge": {
        "label": "新专用判定",
        "comment": "训练后的 TMCRA 专用判定栈，不直接自由回答，只做结构化判定。",
    },
}

GENERATION_LABELS: Dict[str, Dict[str, str]] = {
    "g1": {"label": "第 1 代：旧正式基线", "comment": "旧正式非 overlay 基线。"},
    "g2": {"label": "第 2 代：overlay 初版", "comment": "新链早期 overlay 版本。"},
    "g3": {"label": "第 3 代：时间推理版", "comment": "新链加入 slot_state_resolve 和时间推理。"},
    "g4": {"label": "第 4 代：时间分片扩展版", "comment": "第 3 代基础上打开 temporal_shards。"},
    "g5": {"label": "第 5 代：旧辅助判定版", "comment": "使用外部 LLM assist 的旧辅助判定版本。"},
    "g6": {"label": "第 6 代：新专用判定版", "comment": "使用训练后 TMCRA judge 的新专用判定版本。"},
}

LLM_PROFILE_LABELS: Dict[str, Dict[str, str]] = {
    "qwen": {"label": "Qwen", "comment": "Qwen 系列模型。"},
    "qwen3b": {"label": "Qwen 3B", "comment": "内部轻量 judge 服务默认宿主。"},
    "qwen7b": {"label": "Qwen 7B", "comment": "公开 benchmark 默认宿主大模型。"},
    "deepseek": {"label": "DeepSeek", "comment": "DeepSeek 系列模型。"},
    "deepseek7b": {"label": "DeepSeek 7B", "comment": "补充复核用宿主大模型。"},
    "gemma": {"label": "Gemma", "comment": "Gemma host model family used for replacement evaluation."},
    "gemma4e4b": {"label": "Gemma 4 E4B", "comment": "Gemma 4 E4B host profile for TMCRA replacement benchmarks."},
}

VARIANT_LABELS: Dict[str, Dict[str, str]] = {
    "pure_code": {"label": "纯代码判定", "comment": "不启用外部判定模型。"},
    "qwen_3b_assist": {"label": "Qwen 3B 辅助判定", "comment": "用 Qwen 3B 做轻量 assist 判定。"},
    "deepseek_7b_assist": {"label": "DeepSeek 7B 辅助判定", "comment": "用 DeepSeek 7B 做 assist 判定。"},
    "tmcra_judge_stack": {"label": "新专用判定", "comment": "训练后的 TMCRA judge stack。"},
    "native_full_context": {"label": "宿主原生全上下文", "comment": "宿主大模型原生模式，不接 TMCRA 模块。"},
    "llm_plus_tmcra_memory": {"label": "宿主 + 新记忆算法", "comment": "只替换宿主记忆模块为 TMCRA 记忆。"},
    "llm_plus_tmcra_judge": {"label": "宿主 + 新专用判定", "comment": "只替换宿主判定模块为 TMCRA 判定。"},
    "llm_plus_tmcra_memory_judge": {"label": "宿主 + 新记忆算法 + 新专用判定", "comment": "同时替换宿主记忆和判定模块。"},
    "strict_memory_replace": {"label": "绝对替换：只换记忆", "comment": "彻底关掉宿主记忆，只用 TMCRA 记忆。"},
    "strict_reasoning_judge_replace": {"label": "绝对替换：换推理和判定", "comment": "彻底关掉宿主对应推理/判定链，由 TMCRA 接管。"},
    "strict_memory_reasoning_judge_replace": {"label": "绝对替换：换记忆、推理和判定", "comment": "宿主相关模块全部关闭，由 TMCRA 全接管。"},
    "hybrid_reasoning_assist": {"label": "结合模式：推理协同", "comment": "保留宿主主脑，TMCRA 只做推理协同。"},
    "hybrid_judge_assist": {"label": "结合模式：判定协同", "comment": "保留宿主主脑，TMCRA 只做判定协同。"},
    "hybrid_memory_reasoning_assist": {"label": "结合模式：记忆 + 推理协同", "comment": "TMCRA 提供记忆和推理协同，宿主仍保留主脑。"},
    "hybrid_memory_judge_assist": {"label": "结合模式：记忆 + 判定协同", "comment": "TMCRA 提供记忆和判定协同，宿主仍保留主脑。"},
}

MATRIX_KIND_LABELS: Dict[str, Dict[str, str]] = {
    "strict_replace_matrix": {
        "label": "绝对替换矩阵",
        "comment": "宿主对应模块被彻底屏蔽，只允许 TMCRA 替代模块生效。",
    },
    "hybrid_collab_matrix": {
        "label": "结合协作矩阵",
        "comment": "宿主与 TMCRA 同时存在，重点看混合协同收益。",
    },
    "baseline": {
        "label": "宿主基线",
        "comment": "不启用 TMCRA 替换时的宿主原始基线。",
    },
}

REPLACEMENT_SCOPE_LABELS: Dict[str, Dict[str, str]] = {
    "absolute": {"label": "绝对替换", "comment": "宿主对应模块必须关闭，不能偷偷参与。"},
    "hybrid": {"label": "结合协作", "comment": "宿主模块保留，和 TMCRA 共同工作。"},
}

POLICY_LABELS: Dict[str, Dict[str, str]] = {
    "disabled": {"label": "关闭", "comment": "该策略或模块处于关闭状态。"},
    "host_native": {"label": "宿主原生", "comment": "完全保留宿主原生实现。"},
    "host_baseline": {"label": "宿主基线", "comment": "使用宿主的基线能力，不由 TMCRA 接管。"},
    "assist_enabled": {"label": "协同开启", "comment": "宿主能力保留，同时允许 TMCRA 协同。"},
    "structured_prior": {"label": "结构先验协同", "comment": "TMCRA 只以结构先验方式参与，不直接接管最终自由回答。"},
    "fallback_only": {"label": "仅回退", "comment": "只在主链失败时作为回退路径使用。"},
    "shadow_compare": {"label": "影子对照", "comment": "只做对照，不直接影响正式输出。"},
}

REASONER_MODE_LABELS: Dict[str, Dict[str, str]] = {
    "host_full_context": {"label": "宿主全上下文模式", "comment": "由宿主大模型原生全上下文回答。"},
    "overlay_host_collab": {"label": "overlay 协同模式", "comment": "宿主和 TMCRA 共同参与，TMCRA 提供协同增强。"},
    "tmcra_reasoning_strict": {"label": "TMCRA 严格接管推理", "comment": "宿主对应推理链关闭，由 TMCRA 严格接管。"},
}

JUDGE_MODE_LABELS: Dict[str, Dict[str, str]] = {
    "disabled": {"label": "判定关闭", "comment": "不调用判定层。"},
    "shadow": {"label": "影子判定", "comment": "只出判定结果，不直接影响主决策。"},
    "assist": {"label": "辅助判定", "comment": "判定层会参与部分历史/路径/总结决策。"},
    "strict": {"label": "严格判定", "comment": "判定层直接作为正式决策入口。"},
}

TMCRA_MODE_LABELS: Dict[str, Dict[str, str]] = {
    "none": {"label": "无 TMCRA 模块", "comment": "不嵌入 TMCRA 模块。"},
    "native": {"label": "宿主原生", "comment": "宿主原生能力，不启用 TMCRA 替换。"},
    "memory": {"label": "只嵌入记忆", "comment": "只接入 TMCRA 记忆模块。"},
    "judge": {"label": "只嵌入判定", "comment": "只接入 TMCRA 判定模块。"},
    "memory_judge": {"label": "嵌入记忆 + 判定", "comment": "同时接入 TMCRA 记忆和判定模块。"},
    "llm_plus_tmcra_memory": {"label": "宿主 + TMCRA 记忆", "comment": "公开口径下仅把宿主记忆模块替换为 TMCRA 记忆。"},
    "llm_plus_tmcra_judge": {"label": "宿主 + TMCRA 判定", "comment": "公开口径下仅把宿主判定模块替换为 TMCRA 判定。"},
    "llm_plus_tmcra_memory_judge": {"label": "宿主 + TMCRA 记忆/推理/判定", "comment": "公开口径下把宿主记忆替换为 TMCRA，并由 TMCRA 接管推理与判定。"},
    "planned": {"label": "计划中", "comment": "该公开 benchmark 还没实际跑，只是计划项。"},
}

TMCRA_MODULE_LABELS: Dict[str, Dict[str, str]] = {
    "memory": {"label": "记忆模块", "comment": "TMCRA 记忆子系统。"},
    "reasoning": {"label": "推理模块", "comment": "TMCRA 推理子系统。"},
    "judge": {"label": "判定模块", "comment": "TMCRA 专用判定子系统。"},
}


FIELD_SPECS = {
    "reasoner": REASONER_LABELS,
    "reasoner_name": REASONER_LABELS,
    "memory": MEMORY_LABELS,
    "memory_name": MEMORY_LABELS,
    "memory_variant": MEMORY_LABELS,
    "judge_provider": JUDGE_PROVIDER_LABELS,
    "generation_id": GENERATION_LABELS,
    "llm_profile": LLM_PROFILE_LABELS,
    "host_llm": LLM_PROFILE_LABELS,
    "public_host_llm": LLM_PROFILE_LABELS,
    "variant": VARIANT_LABELS,
    "variant_name": VARIANT_LABELS,
    "matrix_kind": MATRIX_KIND_LABELS,
    "replacement_scope": REPLACEMENT_SCOPE_LABELS,
    "host_memory_policy": POLICY_LABELS,
    "host_reasoning_policy": POLICY_LABELS,
    "host_judge_policy": POLICY_LABELS,
    "base_assist_mode": POLICY_LABELS,
    "fallback_policy": POLICY_LABELS,
    "reasoner_mode": REASONER_MODE_LABELS,
    "judge_mode": JUDGE_MODE_LABELS,
    "tmcra_mode": TMCRA_MODE_LABELS,
}


def describe_code(field_name: str, code: object) -> Dict[str, str] | None:
    text = _clean_text(code)
    if not text:
        return None
    spec = FIELD_SPECS.get(field_name)
    if spec is None:
        return None
    entry = dict(spec.get(_normalize(text), {}))
    return {
        "code": text,
        "label": str(entry.get("label", text)),
        "comment": str(entry.get("comment", _UNKNOWN_COMMENT)),
    }


def _describe_modules(values: Iterable[object]) -> list[Dict[str, str]]:
    items: list[Dict[str, str]] = []
    for value in values:
        text = _clean_text(value)
        if not text:
            continue
        entry = dict(TMCRA_MODULE_LABELS.get(_normalize(text), {}))
        items.append(
            {
                "code": text,
                "label": str(entry.get("label", text)),
                "comment": str(entry.get("comment", _UNKNOWN_COMMENT)),
            }
        )
    return items


def annotate_result_payload(payload: Any) -> Any:
    if isinstance(payload, list):
        return [annotate_result_payload(item) for item in payload]
    if not isinstance(payload, dict):
        return payload

    # Rebuild annotations from the raw payload each time instead of mutating an
    # already-annotated structure. This keeps repeated progress/report writes
    # stable and prevents nested `code_annotations` growth.
    annotated: Dict[str, Any] = {}
    for key, value in payload.items():
        if key == "code_annotations":
            continue
        annotated[key] = annotate_result_payload(value)

    annotations: Dict[str, Any] = {}
    for field_name in FIELD_SPECS:
        if field_name not in annotated:
            continue
        description = describe_code(field_name, annotated.get(field_name))
        if description is None:
            continue
        annotated[f"{field_name}_label"] = description["label"]
        annotated[f"{field_name}_comment"] = description["comment"]
        if field_name == "generation_id" and not _clean_text(annotated.get("generation_name", "")):
            annotated["generation_name"] = description["label"]
        annotations[field_name] = description

    modules = annotated.get("tmcra_modules")
    if isinstance(modules, list):
        described_modules = _describe_modules(modules)
        if described_modules:
            annotated["tmcra_modules_labels"] = [item["label"] for item in described_modules]
            annotated["tmcra_modules_comments"] = [item["comment"] for item in described_modules]
            annotations["tmcra_modules"] = described_modules

    if annotations:
        annotated["code_annotations"] = annotations
    return annotated


def format_code_with_label(field_name: str, code: object) -> str:
    text = _clean_text(code)
    if not text:
        return "N/A"
    description = describe_code(field_name, text)
    if description is None:
        return f"`{text}`"
    label = _clean_text(description.get("label", ""))
    return f"`{text}` ({label})" if label and label != text else f"`{text}`"
