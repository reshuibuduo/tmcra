from __future__ import annotations

from dataclasses import dataclass, field
import re
from typing import Iterable, List

from experiments.replacement.memory_profiles import TMCRAProfile


_ZH_PREVIOUS = "\u4e4b\u524d"
_ZH_BEFORE = "\u4ee5\u524d"
_ZH_ORIGINAL = "\u539f\u6765"
_ZH_COVERED_BEFORE = "\u8986\u76d6\u524d"
_ZH_CURRENT = "\u5f53\u524d"
_ZH_NOW = "\u73b0\u5728"
_ZH_STATUS = "\u73b0\u5728"
_ZH_TIMELINE = "\u53d8\u5316\u8fc7\u7a0b"
_ZH_EVOLUTION = "\u6f14\u53d8\u8fc7\u7a0b"
_ZH_CHANGED = "\u7ecf\u5386\u4e86\u54ea\u4e9b\u53d8\u5316"
_ZH_EVENT_CHAIN = "\u4e8b\u4ef6\u94fe"
_ZH_STATE_EVOLUTION = "\u72b6\u6001\u6f14\u5316"
_ZH_LATEST = "\u6700\u65b0"
_ZH_RECENT = "\u6700\u8fd1"
_ZH_EARLIEST = "\u6700\u65e9"
_ZH_INITIAL = "\u6700\u521d"
_ZH_AFTER = "\u4e4b\u540e"
_ZH_UPDATED_AFTER = "\u66f4\u65b0\u540e"
_ZH_SUMMARY = "\u603b\u7ed3"
_ZH_OVERVIEW = "\u6982\u62ec"
_ZH_EACH = "\u5206\u522b"
_ZH_AND = "\u548c"
_ZH_COMPARE = "\u5bf9\u6bd4"
_ZH_HISTORY = "\u5386\u53f2"
_ZH_ACTIVE = "\u6d3b\u8dc3"
_ZH_GOAL = "\u76ee\u6807"
_ZH_CONSTRAINT = "\u7ea6\u675f"
_ZH_PREFERENCE = "\u504f\u597d"
_ZH_STAGE = "\u9636\u6bb5"
_ZH_TERM = "\u672f\u8bed"
_ZH_FROM = "\u4ece"
_ZH_TO = "\u5230"
_ZH_THROUGH = "\u7ecf\u8fc7"
_ZH_VIA = "\u901a\u8fc7"
_ZH_MULTI_PATH = "\u591a\u6761\u8def\u5f84"
_ZH_OTHER_PATH = "\u53e6\u4e00\u6761\u8def\u5f84"
_ZH_DIFFERENT_PATH = "\u4e0d\u540c\u8def\u5f84"
_ZH_IF_NO = "\u5982\u679c\u6ca1\u6709"
_ZH_REMOVE = "\u79fb\u9664"
_ZH_REMOVE_ALT = "\u53bb\u6389"
_ZH_FOCUSED = "\u4e13\u6ce8"
_ZH_RESPONSIBLE = "\u8d1f\u8d23"
_ZH_THAT_ONE = "\u90a3\u4e2a"
_ZH_VERSION = "\u7248\u672c"

_SUMMARY_MARKERS = (
    "summary",
    "summarize",
    "overall",
    "what do we know",
    "sum up",
    "combine",
    "combined",
    "together",
    "final answer",
    "one final answer",
    _ZH_SUMMARY,
    _ZH_OVERVIEW,
)
_PREVIOUS_MARKERS = (
    "previous",
    "earlier",
    "before",
    "old",
    "prior",
    "used to",
    _ZH_PREVIOUS,
    _ZH_BEFORE,
    _ZH_ORIGINAL,
    _ZH_COVERED_BEFORE,
)
_COMPARE_MARKERS = (
    "current and previous",
    "previous and current",
    "before and now",
    "compare",
    "current vs previous",
    "active and historical",
    "historical and active",
    "active versus historical",
    f"{_ZH_PREVIOUS}{_ZH_AND}{_ZH_NOW}",
    f"{_ZH_CURRENT}{_ZH_AND}{_ZH_PREVIOUS}",
    _ZH_COMPARE,
)
_TIMELINE_MARKERS = (
    "timeline",
    "how changed",
    "how it changed",
    "change over time",
    "over time",
    "evolved",
    "change history",
    _ZH_TIMELINE,
    _ZH_EVOLUTION,
    _ZH_CHANGED,
)
_CURRENT_MARKERS = ("current", "active", "now", "currently", "present", _ZH_CURRENT, _ZH_NOW, _ZH_STATUS)
_LATEST_MARKERS = ("latest", "newest", "most recent", "recent", _ZH_LATEST, _ZH_RECENT)
_EARLIEST_MARKERS = ("earliest", "first", "initial", _ZH_EARLIEST, _ZH_INITIAL)
_AFTER_MARKERS = ("after", "later", "updated after", _ZH_AFTER, _ZH_UPDATED_AFTER)
_INACTIVE_HISTORY_MARKERS = (
    "noise",
    "just noise",
    "noise note",
    "inactive",
    "stay inactive",
    "should stay inactive",
    "keep inactive",
    "remain inactive",
    "should be ignored",
    "ignore this",
    "ignore that",
    "\u566a\u58f0",
    "\u4e0d\u6fc0\u6d3b",
    "\u4fdd\u6301\u4e0d\u6fc0\u6d3b",
    "\u4e0d\u5e94\u751f\u6548",
    "\u5ffd\u7565",
)
_MULTI_PATH_MARKERS = (
    "multiple paths",
    "different paths",
    "another path",
    "all paths",
    "two paths",
    "multiple branches",
    "valid branches",
    "multiple valid branches",
    "different branches",
    _ZH_MULTI_PATH,
    _ZH_OTHER_PATH,
    _ZH_DIFFERENT_PATH,
)
_COUNTERFACTUAL_MARKERS = ("without", "removed", "remove", "were removed", "if not", _ZH_IF_NO, _ZH_REMOVE, _ZH_REMOVE_ALT)
_PATH_MARKERS = ("path", "paths", "route", "routes", "chain", "chains", "connect", "pathway", "branch", "branches", "\u8def\u5f84", "\u94fe\u8def", "\u8fde\u63a5", "\u8def\u7ebf")
_TEMPORAL_PATH_MARKERS = (
    "event chain",
    "state evolution",
    "timeline path",
    "temporal path",
    "change chain",
    _ZH_EVENT_CHAIN,
    _ZH_STATE_EVOLUTION,
    _ZH_TIMELINE,
    _ZH_EVOLUTION,
    _ZH_CHANGED,
)
_CONSTRAINT_PATTERNS = (
    r"\bvia\s+([a-z0-9_.-]+)",
    r"\bthrough\s+([a-z0-9_.-]+)",
    r"\bmust include\s+([a-z0-9_.-]+)",
    rf"{_ZH_THROUGH}\s*([a-z0-9_.-]+)",
    rf"{_ZH_VIA}\s*([a-z0-9_.-]+)",
)
_PATH_PATTERNS = (
    r"\bwhat path\b",
    r"\bwhich path\b",
    r"\bwhat is missing\b.*\bpath\b",
    r"\bgive\b.*\bbranches?\b",
    r"\bfind\b.*\bnon[- ]obvious path\b",
    r"\bshow (?:the )?(?:path|paths|route|routes|chain|chains)\b",
    r"\btrace (?:the )?(?:path|paths|route|routes|chain|chains)\b",
    r"\b(?:causal|reasoning)\s+chain\b.*\bfrom\b.*\bto\b",
    r"\bpropagat(?:e|es|ion)\b.*\bto\b",
    r"\bacross abstraction levels\b",
    r"\b(?:path|paths|route|routes|chain|chains|branch|branches)\b.*\b(?:from|to|between|through|via)\b",
    rf"{_ZH_FROM}\s*([a-z0-9_.-]+)\s*{_ZH_TO}\s*([a-z0-9_.-]+)",
)
_ENTITY_PATTERNS = (
    r"\b(alpha|beta|gamma|delta)\b",
    r"\b(v\d+(?:\.\d+)*)\b",
    r"\b(build[-_ ]?[a-z0-9.]+)\b",
    r'"([^"]+)"',
    r"'([^']+)'",
    r"`([^`]+)`",
    rf"(?:{_ZH_RESPONSIBLE}|{_ZH_FOCUSED}|{_ZH_THAT_ONE})\s*([a-z0-9_.-]+)",
    r"(?:focused on|owns|owner of|responsible for)\s+([a-z0-9_.-]+)",
)


def _clean_text(value: object) -> str:
    return re.sub(r"\s+", " ", str(value or "").strip())


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


def _contains_cjk(value: object) -> bool:
    return bool(re.search(r"[\u4e00-\u9fff]", _clean_text(value)))


def _contains_marker(text: object, marker: object) -> bool:
    source = _clean_text(text)
    needle = _clean_text(marker)
    if not source or not needle:
        return False
    if _contains_cjk(needle):
        return needle in source
    pattern = re.escape(_normalize(needle)).replace(r"\ ", r"\s+")
    return bool(re.search(rf"(?<![a-z0-9_]){pattern}(?![a-z0-9_])", _normalize(source), flags=re.IGNORECASE))


def _contains_any_marker(text: object, markers: Iterable[object]) -> bool:
    return any(_contains_marker(text, marker) for marker in markers)


def _tokenize(value: object) -> List[str]:
    text = _normalize(value)
    if not text:
        return []
    english = re.findall(r"[a-z0-9_.-]+", text)
    cjk = [char for char in text if "\u4e00" <= char <= "\u9fff"]
    return _dedupe([*english, *cjk])


def _dedupe(items: Iterable[object]) -> List[str]:
    results: List[str] = []
    seen = set()
    for item in items:
        text = _clean_text(item)
        if not text:
            continue
        key = _normalize(text)
        if key in seen:
            continue
        seen.add(key)
        results.append(text)
    return results


@dataclass(slots=True)
class QueryIntent:
    kind: str
    history_kind: str = "none"
    category_hints: List[str] = field(default_factory=list)
    entity_hints: List[str] = field(default_factory=list)
    path_mode: str = "none"
    temporal_hints: List[str] = field(default_factory=list)
    slot_mode: str = "current"
    requires_temporal_resolution: bool = False
    requires_state_resolution: bool = True
    requires_path_reasoning: bool = False
    summary_mode: str = "default"

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "history_kind": self.history_kind,
            "category_hints": list(self.category_hints),
            "entity_hints": list(self.entity_hints),
            "path_mode": self.path_mode,
            "temporal_hints": list(self.temporal_hints),
            "slot_mode": self.slot_mode,
            "requires_temporal_resolution": bool(self.requires_temporal_resolution),
            "requires_state_resolution": bool(self.requires_state_resolution),
            "requires_path_reasoning": bool(self.requires_path_reasoning),
            "summary_mode": self.summary_mode,
        }


class QueryIntentParser:
    def __init__(self, *, profile: TMCRAProfile | None = None) -> None:
        self.profile = profile or TMCRAProfile()

    def parse(self, query: str, *, answer_mode: str = "transparent") -> QueryIntent:
        _ = answer_mode
        text = _clean_text(query)
        lowered = _normalize(text)
        category_hints = [hint for hint in self.profile.infer_category_hints(text) if hint != "path"]
        entity_hints = self._extract_entity_hints(text)
        path_mode = self._infer_path_mode(text, lowered)
        history_kind = self._infer_history_kind(text, lowered)
        temporal_hints = self._infer_temporal_hints(text, lowered, history_kind=history_kind)
        asks_summary = _contains_any_marker(text, _SUMMARY_MARKERS)
        if _contains_any_marker(text, _INACTIVE_HISTORY_MARKERS):
            category_hints = []

        if path_mode != "none":
            kind = "path"
        elif asks_summary or (len(category_hints) > 1 and (_ZH_EACH in text or history_kind == "current")):
            kind = "summary"
        elif history_kind in {"compare", "previous", "timeline"}:
            kind = "history"
        else:
            kind = "slot"

        if kind != "path":
            path_mode = "none"
        if kind == "summary" and history_kind == "none" and _contains_any_marker(text, [*_CURRENT_MARKERS, *_LATEST_MARKERS]):
            history_kind = "current"
        if kind == "slot" and history_kind == "none" and _contains_any_marker(text, [*_CURRENT_MARKERS, *_LATEST_MARKERS]):
            history_kind = "current"
        if kind == "summary" and not category_hints and _contains_cjk(text):
            category_hints = ["goal", "constraint", "preference", "stage_state"]
        slot_mode = self._slot_mode(kind=kind, history_kind=history_kind, temporal_hints=temporal_hints)
        return QueryIntent(
            kind=kind,
            history_kind=history_kind,
            category_hints=_dedupe(category_hints),
            entity_hints=entity_hints,
            path_mode=path_mode,
            temporal_hints=temporal_hints,
            slot_mode=slot_mode,
            requires_temporal_resolution=bool(history_kind != "none" or temporal_hints or path_mode in {"temporal_path", "state_evolution_path"}),
            requires_state_resolution=kind in {"slot", "history", "summary"},
            requires_path_reasoning=kind == "path",
            summary_mode="timeline" if history_kind == "timeline" else ("summary" if kind == "summary" else "default"),
        )

    def _infer_history_kind(self, text: str, lowered: str) -> str:
        _ = lowered
        if (
            _contains_any_marker(text, _COMPARE_MARKERS)
            or (
                _contains_any_marker(text, [*_CURRENT_MARKERS, *_LATEST_MARKERS])
                and (_contains_any_marker(text, _PREVIOUS_MARKERS) or _contains_marker(text, "historical") or _contains_marker(text, _ZH_HISTORY))
            )
            or (_ZH_EACH in text and _contains_any_marker(text, _PREVIOUS_MARKERS) and _contains_any_marker(text, _CURRENT_MARKERS))
            or (_contains_marker(text, "historical") and _contains_any_marker(text, ("active", "current", _ZH_CURRENT, _ZH_ACTIVE)))
            or (_ZH_HISTORY in text and (_ZH_CURRENT in text or _ZH_ACTIVE in text))
        ):
            return "compare"
        if _contains_any_marker(text, _TIMELINE_MARKERS):
            return "timeline"
        if _contains_any_marker(text, _INACTIVE_HISTORY_MARKERS):
            return "previous"
        if _contains_any_marker(text, [*_CURRENT_MARKERS, *_LATEST_MARKERS]):
            return "current"
        if _contains_any_marker(text, _PREVIOUS_MARKERS) or _contains_marker(text, "historical") or _contains_marker(text, _ZH_HISTORY):
            return "previous"
        return "none"

    def _infer_path_mode(self, text: str, lowered: str) -> str:
        explicit_path = any(re.search(pattern, lowered, flags=re.IGNORECASE) for pattern in _PATH_PATTERNS)
        structural_path = _contains_any_marker(text, _PATH_MARKERS) and any(
            token in lowered for token in (" from ", " to ", " between ", " via ", " through ", " connect ", _ZH_FROM, _ZH_TO, _ZH_THROUGH, _ZH_VIA)
        )
        if not (explicit_path or structural_path):
            return "none"
        if _contains_any_marker(text, _TEMPORAL_PATH_MARKERS):
            return "state_evolution_path" if _contains_any_marker(text, ("state evolution", _ZH_STATE_EVOLUTION)) else "temporal_path"
        if _contains_any_marker(text, _COUNTERFACTUAL_MARKERS):
            return "counterfactual"
        if _contains_any_marker(text, _MULTI_PATH_MARKERS):
            return "multi"
        if any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in _CONSTRAINT_PATTERNS):
            return "constrained"
        return "single"

    def _slot_mode(self, *, kind: str, history_kind: str, temporal_hints: List[str]) -> str:
        if "inactive" in set(temporal_hints):
            return "previous"
        if history_kind in {"current", "previous", "compare", "timeline"}:
            return history_kind
        if kind == "summary":
            return "summary"
        return "current"

    def _extract_entity_hints(self, text: str) -> List[str]:
        lowered = _normalize(text)
        hints: List[str] = []
        for pattern in _ENTITY_PATTERNS:
            for match in re.findall(pattern, text, flags=re.IGNORECASE):
                if isinstance(match, tuple):
                    hints.extend(item for item in match if _clean_text(item))
                else:
                    hints.append(str(match))
        hints.extend(token for token in _tokenize(text) if token in {"alpha", "beta", "gamma", "delta"})
        if _ZH_VERSION in text:
            hints.extend(re.findall(r"([a-z0-9_.-]*v\d+(?:\.\d+)*)", lowered))
        return _dedupe(hints)


    def _infer_temporal_hints(self, text: str, lowered: str, *, history_kind: str) -> List[str]:
        hints: List[str] = []
        if history_kind == "compare":
            hints.extend(["previous", "current"])
        elif history_kind == "previous":
            hints.append("previous")
        elif history_kind == "current":
            hints.append("current")
        elif history_kind == "timeline":
            hints.extend(["timeline", "earliest", "latest"])

        if _contains_any_marker(text, _LATEST_MARKERS):
            hints.extend(["current", "latest"])
        if _contains_any_marker(text, _EARLIEST_MARKERS):
            hints.extend(["previous", "earliest"])
        if _contains_any_marker(text, _AFTER_MARKERS):
            hints.append("after")
        if _contains_any_marker(text, _TIMELINE_MARKERS):
            hints.append("timeline")
        if _contains_any_marker(text, _INACTIVE_HISTORY_MARKERS):
            hints.extend(["inactive", "noise"])
        if _ZH_AFTER in text or _ZH_UPDATED_AFTER in text:
            hints.append("after")
        return _dedupe(hints)
