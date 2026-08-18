from __future__ import annotations

import re
from typing import Dict, Iterable, List

from experiments.replacement.adapters.base import MemoryHit


def _clean_text(value: object) -> str:
    return str(value or "").strip()


def _normalize(value: object) -> str:
    return _clean_text(value).lower()


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


class EntityDisambiguator:
    def derive_entity_key(self, hit: MemoryHit) -> str:
        metadata = dict(hit.metadata or {})
        for key in ("entity_key", "entity_name", "entity_id"):
            value = _clean_text(metadata.get(key, ""))
            if value:
                return _normalize(value)

        components: List[str] = []
        slot_key = _normalize(hit.slot_key)
        if slot_key:
            components.append(slot_key)
        if hit.anchors:
            components.append(_normalize(hit.anchors[0]))
        components.extend(self._role_tokens(hit))
        components.extend(self._version_tokens(hit))
        components = _dedupe(components)[:4]
        if components:
            return "|".join(components)

        value_tokens = [token for token in _tokenize(hit.value) if len(token) > 1]
        if value_tokens:
            return "|".join(value_tokens[:2])
        return _normalize(hit.memory_id)

    def discriminator_tokens(self, hit: MemoryHit) -> List[str]:
        metadata = dict(hit.metadata or {})
        values: List[str] = []
        values.extend(hit.anchors)
        values.append(hit.slot_key)
        values.append(hit.value)
        for key in ("entity_name", "entity_role", "entity_version"):
            values.append(_clean_text(metadata.get(key, "")))
        for key in ("entity_aliases", "entity_discriminator_tokens"):
            raw = metadata.get(key, [])
            if isinstance(raw, list):
                values.extend(_clean_text(item) for item in raw)
            else:
                values.append(_clean_text(raw))
        return _dedupe(_tokenize(" ".join(values)))

    def match_details(
        self,
        hit: MemoryHit,
        query_hints: List[str],
        *,
        temporal_hints: List[str] | None = None,
        latest_turn: int = 0,
    ) -> Dict[str, object]:
        if not query_hints:
            temporal_bias, temporal_match_type = self.temporal_bias(
                hit,
                temporal_hints or [],
                latest_turn=latest_turn,
            )
            return {
                "score": temporal_bias,
                "match_type": "none",
                "exact_matches": [],
                "partial_matches": [],
                "conflict": False,
                "temporal_bias": temporal_bias,
                "temporal_match_type": temporal_match_type,
            }

        key = self.derive_entity_key(hit)
        tokens = set(self.discriminator_tokens(hit))
        exact_matches: List[str] = []
        partial_matches: List[str] = []
        for hint in [_normalize(item) for item in query_hints if _clean_text(item)]:
            hint_tokens = set(_tokenize(hint))
            if hint == key or hint in tokens:
                exact_matches.append(hint)
            elif hint_tokens and hint_tokens & tokens:
                partial_matches.append(hint)
            elif hint and hint in key:
                partial_matches.append(hint)

        temporal_bias, temporal_match_type = self.temporal_bias(
            hit,
            temporal_hints or [],
            latest_turn=latest_turn,
        )

        if exact_matches:
            score = min(0.5, 0.30 + 0.06 * max(0, len(exact_matches) - 1) + temporal_bias)
            return {
                "score": score,
                "match_type": "exact",
                "exact_matches": exact_matches,
                "partial_matches": partial_matches,
                "conflict": False,
                "temporal_bias": temporal_bias,
                "temporal_match_type": temporal_match_type,
            }
        if partial_matches:
            score = min(0.32, 0.12 + 0.04 * max(0, len(partial_matches) - 1) + temporal_bias)
            return {
                "score": score,
                "match_type": "partial",
                "exact_matches": exact_matches,
                "partial_matches": partial_matches,
                "conflict": False,
                "temporal_bias": temporal_bias,
                "temporal_match_type": temporal_match_type,
            }
        return {
            "score": -0.25 + temporal_bias,
            "match_type": "conflict",
            "exact_matches": exact_matches,
            "partial_matches": partial_matches,
            "conflict": True,
            "temporal_bias": temporal_bias,
            "temporal_match_type": temporal_match_type,
        }

    def score(
        self,
        hit: MemoryHit,
        query_hints: List[str],
        *,
        temporal_hints: List[str] | None = None,
        latest_turn: int = 0,
    ) -> float:
        return float(
            self.match_details(
                hit,
                query_hints,
                temporal_hints=temporal_hints,
                latest_turn=latest_turn,
            ).get("score", 0.0)
        )

    def temporal_bias(self, hit: MemoryHit, temporal_hints: List[str], *, latest_turn: int = 0) -> tuple[float, str]:
        if not temporal_hints:
            return 0.0, "none"
        normalized = {_normalize(item) for item in temporal_hints if _clean_text(item)}
        age = max(0, int(latest_turn or 0) - int(hit.turn_index or 0))
        bias = 0.0
        match_type = "neutral"

        if "current" in normalized or "latest" in normalized or "after" in normalized:
            if hit.state == "active":
                bias += 0.08
                match_type = "current"
            else:
                bias -= 0.06
        if "previous" in normalized:
            if hit.state != "active":
                bias += 0.08
                match_type = "previous"
            else:
                bias -= 0.06
        if "earliest" in normalized:
            if age >= 2:
                bias += 0.05
                match_type = "earliest"
            elif hit.state == "active":
                bias -= 0.04
        if "timeline" in normalized and hit.state != "active":
            bias += min(0.06, 0.02 + (age * 0.01))
            if match_type == "neutral":
                match_type = "timeline"
        return bias, match_type

    def _role_tokens(self, hit: MemoryHit) -> List[str]:
        metadata = dict(hit.metadata or {})
        values = [
            _clean_text(metadata.get("entity_role", "")),
            _clean_text(hit.slot_key),
            *[_clean_text(anchor) for anchor in hit.anchors],
        ]
        tokens = [token for token in _tokenize(" ".join(values)) if token in {"owner", "responsible", "focused", "alpha", "beta", "gamma", "delta"}]
        return _dedupe(tokens)

    def _version_tokens(self, hit: MemoryHit) -> List[str]:
        metadata = dict(hit.metadata or {})
        values = " ".join(
            [
                _clean_text(metadata.get("entity_version", "")),
                _clean_text(hit.slot_key),
                _clean_text(hit.value),
                " ".join(_clean_text(anchor) for anchor in hit.anchors),
            ]
        )
        return _dedupe(re.findall(r"v\d+(?:\.\d+)*", values, flags=re.IGNORECASE))
