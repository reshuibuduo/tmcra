import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_tmcra_v4_retrieve as retrieval
from tmcra_v4_route_policy import RETRIEVAL_CONTRACT_SCHEMA


class V4RetrievalManifestTests(unittest.TestCase):
    def row(self, qid="q1", index="index.pt"):
        return {
            "question_id": qid,
            "scope_id": "tmcra_v4:" + qid,
            "db_path": "database.sqlite3",
            "index_path": index,
        }

    def test_scope_and_query_manifests_require_exact_index_identity(self):
        retrieval._validate_manifest_pair([self.row()], [self.row()])
        with self.assertRaisesRegex(
            retrieval.RetrievalError, "same frozen indexes"
        ):
            retrieval._validate_manifest_pair(
                [self.row()], [self.row(index="different.pt")]
            )

    def test_manifest_rejects_duplicate_qid(self):
        with self.assertRaisesRegex(
            retrieval.RetrievalError, "missing or duplicate"
        ):
            retrieval._validate_manifest_pair(
                [self.row(), self.row()], [self.row()]
            )

    def test_qid_list_selects_both_manifests_in_requested_order(self):
        with tempfile.TemporaryDirectory() as directory:
            qids = Path(directory) / "qids.txt"
            qids.write_text("q2\nq1\n", encoding="utf-8")
            scope, query = retrieval._select_manifest_rows(
                [self.row("q1", "one.pt"), self.row("q2", "two.pt")],
                [self.row("q1", "one.pt"), self.row("q2", "two.pt")],
                qids,
            )
            self.assertEqual([row["question_id"] for row in scope], ["q2", "q1"])
            self.assertEqual([row["question_id"] for row in query], ["q2", "q1"])

    def test_qid_list_rejects_unknown_or_duplicate_questions(self):
        with tempfile.TemporaryDirectory() as directory:
            qids = Path(directory) / "qids.txt"
            qids.write_text("q1\nq1\n", encoding="utf-8")
            with self.assertRaisesRegex(retrieval.RetrievalError, "duplicated"):
                retrieval._select_manifest_rows([self.row()], [self.row()], qids)
            qids.write_text("q2\n", encoding="utf-8")
            with self.assertRaisesRegex(retrieval.RetrievalError, "unknown"):
                retrieval._select_manifest_rows([self.row()], [self.row()], qids)

    def test_resume_requires_original_failed_marker_and_graph_boundary(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            fingerprints = {"q1": "graph-1"}
            with self.assertRaisesRegex(retrieval.RetrievalError, "FAILED"):
                retrieval._prepare_graph_boundary(
                    run_dir=run_dir,
                    tag="retrieval_4",
                    fingerprints=fingerprints,
                    resume=True,
                )
            (run_dir / "FAILED.retrieval_4").write_text("{}\n", encoding="utf-8")
            (run_dir / "retrieval_4.graph_before.json").write_text(
                json.dumps(fingerprints), encoding="utf-8"
            )
            retrieval._prepare_graph_boundary(
                run_dir=run_dir,
                tag="retrieval_4",
                fingerprints=fingerprints,
                resume=True,
            )
            with self.assertRaisesRegex(retrieval.RetrievalError, "graph changed"):
                retrieval._prepare_graph_boundary(
                    run_dir=run_dir,
                    tag="retrieval_4",
                    fingerprints={"q1": "graph-2"},
                    resume=True,
                )

    def test_fresh_retrieval_refuses_stale_staging(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)
            (run_dir / ".retrieval_4.staging").mkdir()
            with self.assertRaisesRegex(retrieval.RetrievalError, "--resume"):
                retrieval._prepare_graph_boundary(
                    run_dir=run_dir,
                    tag="retrieval_4",
                    fingerprints={"q1": "graph-1"},
                    resume=False,
                )

    def test_production_retrieval_rejects_diagnostic_composition_before_work(self):
        args = type(
            "Args",
            (),
            {
                "run_dir": Path("missing-run"),
                "tag": "diagnostic",
                "composition_mode": "source-only-diagnostic",
                "diagnostic": False,
            },
        )()
        with self.assertRaisesRegex(retrieval.RetrievalError, "requires layered"):
            retrieval.retrieve(args)

    def test_production_retrieval_rejects_adaptive_or_non_top8_before_work(self):
        base = {
            "run_dir": Path("missing-run"),
            "tag": "retrieval",
            "composition_mode": "layered",
            "diagnostic": False,
            "packing_budget_mode": "adaptive",
            "top_k": 8,
        }
        with self.assertRaisesRegex(retrieval.RetrievalError, "fixed Top8"):
            retrieval.retrieve(type("Args", (), base)())
        base["packing_budget_mode"] = "fixed"
        base["top_k"] = 16
        with self.assertRaisesRegex(retrieval.RetrievalError, "fixed Top8"):
            retrieval.retrieve(type("Args", (), base)())

    def test_committed_diagnostic_output_cannot_resume_as_production(self):
        with tempfile.TemporaryDirectory() as directory:
            out_dir = Path(directory)
            contribution = {
                "layer": "source",
                "role": "primary",
                "weight": 1.0,
            }
            evidence = {
                "question_id": "q1",
                "retrieval_contract": {
                    "schema_version": RETRIEVAL_CONTRACT_SCHEMA,
                    "execution_lane": "diagnostic",
                    "composition_mode": "source-only-diagnostic",
                    "inventory_counts": {
                        "source": 1,
                        "fast": 0,
                        "fast_semantic": 0,
                        "slow": 0,
                        "slow_capsule_heads": 0,
                        "slow_summaries": 0,
                        "slow_claims": 0,
                        "slow_ranked_claims": 0,
                    },
                    "candidate_paths_executed": {
                        "source": True,
                        "fast": False,
                        "slow": False,
                    },
                    "required_selected_layers": ["source"],
                    "selected_layer_window_counts": {
                        "source": 1,
                        "fast": 0,
                        "slow": 0,
                    },
                    "packing_budget_mode": "fixed",
                    "packing_budget": 8,
                    "source_coverage_trace_k": 24,
                    "final_window_count": 1,
                },
                "evidence_windows": [
                    {
                        "session_id": "s1",
                        "text": "fact",
                        "retrieval_metadata": {
                            "layer_contributions": [contribution]
                        },
                    }
                ],
            }
            (out_dir / "evidence_windows.jsonl").write_text(
                json.dumps(evidence) + "\n", encoding="utf-8"
            )
            (out_dir / "retrieval_debug.jsonl").write_text(
                json.dumps({"question_id": "q1"}) + "\n", encoding="utf-8"
            )
            (out_dir / "report.json").write_text(
                json.dumps({"query_count": 1}), encoding="utf-8"
            )
            with self.assertRaisesRegex(
                retrieval.RetrievalError, "does not match the requested lane"
            ):
                retrieval._validate_committed_retrieval_output(
                    out_dir,
                    expected_qids=["q1"],
                    execution_lane="production",
                )

    def test_production_build_preflight_runs_strict_zero_api_audit(self):
        with tempfile.TemporaryDirectory() as directory:
            run_dir = Path(directory)

            def write_report(command, log, environment):
                output = Path(command[command.index("--output") + 1])
                output.write_text(
                    json.dumps({"passed": True, "slow_promotion_coverage": {"complete": True}}),
                    encoding="utf-8",
                )

            with mock.patch.object(retrieval, "_run", side_effect=write_report) as run:
                report = retrieval._run_production_build_preflight(
                    run_dir, "retrieval_layered", resume=False
                )
            command = run.call_args.args[0]
            self.assertIn("--build-only", command)
            self.assertNotIn("--retrieval-dir", command)
            self.assertTrue(report["slow_promotion_coverage"]["complete"])


if __name__ == "__main__":
    unittest.main()
