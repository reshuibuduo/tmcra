from __future__ import annotations

import unittest

from tmcra_service.api_models import RecallRequest
from tmcra_service.routing import select_evidence_route


class EvidenceRoutingTests(unittest.TestCase):
    def test_recall_defaults_to_auto_routing(self) -> None:
        self.assertEqual(RecallRequest(query="remember this").evidence_mode, "auto")

    def test_raw_is_always_cheap(self) -> None:
        route = select_evidence_route(
            "raw", {"recall_plan": {"query_kind": "comparison"}}
        )
        self.assertEqual(route.selected, "raw")

    def test_auto_compiles_only_high_risk_shapes(self) -> None:
        simple = select_evidence_route(
            "auto", {"recall_plan": {"query_kind": "fact", "temporal_focus": "current"}}
        )
        comparison = select_evidence_route(
            "auto", {"recall_plan": {"query_kind": "comparison"}}
        )
        self.assertEqual(simple.selected, "raw")
        self.assertEqual(comparison.selected, "compiled")


if __name__ == "__main__":
    unittest.main()
