from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import run_tmcra_v4_build as build


class V4BuildResumeTests(unittest.TestCase):
    def test_worker_environment_uses_16384_for_writer_and_slow_graph(self):
        environment = build._worker_environment({}, ["key-a", "key-b"], 0)
        self.assertEqual(environment["TMCRA_WRITER_MAX_TOKENS"], "16384")
        self.assertEqual(
            environment["TMCRA_DEEPSEEK_FLASH_MAX_TOKENS"], "16384"
        )
        self.assertEqual(
            environment["TMCRA_DEEPSEEK_PRO_MAX_TOKENS"], "16384"
        )

        overridden = build._worker_environment(
            {"TMCRA_WRITER_MAX_TOKENS": "24576"}, ["key-a"], 0
        )
        self.assertEqual(overridden["TMCRA_WRITER_MAX_TOKENS"], "24576")
        self.assertEqual(
            overridden["TMCRA_DEEPSEEK_FLASH_MAX_TOKENS"], "24576"
        )

    def test_writer_quality_report_exposes_tolerated_semantic_loss(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            rows = [
                {
                    "message_key": "m1",
                    "semantic_proposals": 2,
                    "semantic_committed": 1,
                    "validation_warnings": [
                        {
                            "code": "invalid_assertion_quarantined",
                            "dropped_count": 1,
                        },
                        {
                            "code": "temporal_durability_defaulted_uncertain",
                            "dropped_count": 0,
                        },
                    ],
                },
                {
                    "message_key": "m2",
                    "semantic_proposals": 1,
                    "semantic_committed": 1,
                    "validation_warnings": [],
                },
            ]
            (worker_dir / "product_write_messages.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows),
                encoding="utf-8",
            )

            report = build._writer_quality_report(
                [{"worker_dir": str(worker_dir)}]
            )

            self.assertTrue(report["requires_review"])
            self.assertEqual(report["message_count"], 2)
            self.assertEqual(report["messages_with_warnings"], 1)
            self.assertEqual(report["warning_count"], 2)
            self.assertEqual(report["dropped_count"], 1)
            self.assertEqual(report["semantic_proposals"], 3)
            self.assertEqual(report["semantic_committed"], 2)

    def test_subject_attribution_stage_is_mandatory_pro_apply(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            report_path = root / "subject_attribution_report.json"

            def fake_run(command, log_path, environment):
                self.assertIn("audit_tmcra_v4_subject_attribution.py", " ".join(command))
                self.assertIn("--apply", command)
                report_path.write_text(
                    json.dumps(
                        {
                            "status": "complete",
                            "mode": "apply",
                            "prompt_version": build.SUBJECT_ATTRIBUTION_PROMPT_VERSION,
                            "model": "deepseek-v4-pro",
                            "routed_message_count": 1,
                            "quarantined_count": 2,
                            "physical_api_calls": 1,
                        }
                    ),
                    encoding="utf-8",
                )

            with mock.patch.object(build, "_run", side_effect=fake_run) as run:
                report = build._subject_attribution_stage(root, {})
            self.assertEqual(run.call_count, 1)
            self.assertEqual(report["quarantined_count"], 2)

    def test_load_resume_manifest_requires_frozen_prepared_workers(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            manifest = {
                "status": "prepared",
                "row_count": 1,
                "qids": ["q1"],
                "workers": [{"question_id": "q1"}],
            }
            (root / "input_manifest.json").write_text(json.dumps(manifest))
            self.assertEqual(build._load_resume_manifest(root)["qids"], ["q1"])
            manifest["row_count"] = 2
            (root / "input_manifest.json").write_text(json.dumps(manifest))
            with self.assertRaises(build.BuildError):
                build._load_resume_manifest(root)

    def test_verify_resume_writer_requires_every_batch_committed(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            database = worker_dir / "native_memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute("CREATE TABLE v4_batch_journal(status TEXT)")
                con.executemany(
                    "INSERT INTO v4_batch_journal VALUES(?)",
                    [("committed",), ("committed",)],
                )
                con.commit()
            (worker_dir / "product_writer_report.json").write_text(
                json.dumps({"completed": True, "batches": 2})
            )
            (worker_dir / "writer_chain_audit.json").write_text(
                json.dumps({"passed": True})
            )
            build._verify_resume_writer({"worker_dir": str(worker_dir)})
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "UPDATE v4_batch_journal SET status='failed' WHERE rowid=1"
                )
                con.commit()
            with self.assertRaises(build.BuildError):
                build._verify_resume_writer({"worker_dir": str(worker_dir)})

    def test_slow_resume_refuses_unreviewed_failed_jobs(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            database = worker_dir / "native_memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_jobs(job_id TEXT,region_key TEXT,"
                    "last_error TEXT,status TEXT,created_at INTEGER)"
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?)",
                    ("job-1", "profile", "invalid GraphPatch", "failed", 1),
                )
                con.commit()
            worker = {
                "worker_dir": str(worker_dir),
                "scope_id": "scope",
            }
            with mock.patch.object(build, "_run") as run:
                with self.assertRaisesRegex(
                    build.BuildError, "explicit revalidation"
                ):
                    build._slow_worker_resume(
                        worker, repo=worker_dir, environment={}
                    )
            self.assertEqual(run.call_count, 1)

    def test_fresh_slow_worker_requires_complete_promotion_coverage(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            worker = {
                "worker_dir": str(worker_dir),
                "scope_id": "scope",
            }
            with mock.patch.object(build, "_run") as run:
                build._slow_worker(worker, repo=worker_dir, environment={})
            audit_command = run.call_args_list[2].args[0]
            self.assertIn("--require-promotion-coverage", audit_command)

    def test_writer_resume_is_explicit_and_reaudits(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            worker = {
                "worker_dir": str(worker_dir),
                "input": str(worker_dir / "input.json"),
            }
            with mock.patch.object(build, "_run") as run:
                build._writer_worker_resume(
                    worker,
                    repo=worker_dir,
                    environment={},
                    recover_interrupted_api_calls=True,
                )
            self.assertEqual(run.call_count, 2)
            writer_command = run.call_args_list[0].args[0]
            self.assertIn("--revalidate-failed-raw-response", writer_command)
            self.assertIn("--recover-interrupted-api-calls", writer_command)
            audit_command = run.call_args_list[1].args[0]
            self.assertIn("writer_chain_audit.json", " ".join(audit_command))

    def test_interrupted_writer_calls_are_discovered_before_resume(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            database = worker_dir / "native_memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE v4_batch_journal(batch_id TEXT,status TEXT,batch_index INTEGER)"
                )
                con.execute(
                    "INSERT INTO v4_batch_journal VALUES('batch-1','api_started',0)"
                )
                con.execute(
                    "CREATE TABLE v4_reconciliation_jobs(job_id TEXT,status TEXT,created_at INTEGER)"
                )
                con.execute(
                    "INSERT INTO v4_reconciliation_jobs VALUES('job-1','pro_started',0)"
                )
                con.commit()
            calls = build._interrupted_writer_calls(
                {"worker_dir": str(worker_dir), "question_id": "q1"}
            )
            self.assertEqual(
                [item["call_key"] for item in calls],
                ["flash:batch-1", "pro:job-1"],
            )

    def test_interrupted_slow_call_requires_explicit_recovery_before_enqueue(self):
        with tempfile.TemporaryDirectory() as temp:
            worker_dir = Path(temp)
            database = worker_dir / "native_memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_jobs("
                    "job_id TEXT,scope_id TEXT,region_key TEXT,status TEXT,"
                    "attempts INTEGER,last_error TEXT,claim_token TEXT,"
                    "claim_owner TEXT,lease_expires_at INTEGER,created_at INTEGER)"
                )
                con.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,job_id TEXT,status TEXT,created_at INTEGER,"
                    "claim_token TEXT,claim_owner TEXT,error TEXT)"
                )
                con.execute(
                    "INSERT INTO slow_graph_jobs VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        "job-1",
                        "scope",
                        "profile",
                        "pending",
                        0,
                        "",
                        "claim-1",
                        "pid:999999:dead",
                        1,
                        1,
                    ),
                )
                con.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?,?,?,?)",
                    (
                        "attempt-1",
                        "job-1",
                        "started",
                        1,
                        "claim-1",
                        "pid:999999:dead",
                        "",
                    ),
                )
                con.commit()
            worker = {
                "worker_dir": str(worker_dir),
                "scope_id": "scope",
                "question_id": "q1",
            }
            calls = build._interrupted_slow_calls(worker)
            self.assertEqual([item["attempt_id"] for item in calls], ["attempt-1"])
            with mock.patch.object(build, "_run") as run:
                with self.assertRaisesRegex(
                    build.BuildError, "explicit process-loss recovery"
                ):
                    build._slow_worker_resume(
                        worker, repo=worker_dir, environment={}
                    )
            self.assertEqual(run.call_count, 0)

    def test_process_loss_cost_uncertainty_is_not_reported_as_exact_cost(self):
        with tempfile.TemporaryDirectory() as temp:
            database = Path(temp) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as con:
                con.execute(
                    "CREATE TABLE slow_graph_process_loss_recoveries("
                    "potential_duplicate_physical_calls_min INTEGER,"
                    "potential_duplicate_physical_calls_max INTEGER)"
                )
                con.executemany(
                    "INSERT INTO slow_graph_process_loss_recoveries VALUES(?,?)",
                    [(0, 3), (0, 3)],
                )
                con.commit()
            self.assertEqual(
                build._slow_process_loss_cost_uncertainty([database]),
                {
                    "unknown_external_call_outcomes": 2,
                    "potential_duplicate_physical_calls_min": 0,
                    "potential_duplicate_physical_calls_max": 6,
                },
            )

    def test_record_failure_preserves_superseded_marker(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root / "FAILED").write_text(
                json.dumps({"at": "old", "error": "old failure"}) + "\n"
            )
            build._record_failure(root, RuntimeError("new failure"))
            current = json.loads((root / "FAILED").read_text())
            history = [
                json.loads(line)
                for line in (root / "build_failure_history.jsonl")
                .read_text()
                .splitlines()
            ]
            self.assertIn("new failure", current["error"])
            self.assertEqual(history[0]["failure"]["error"], "old failure")


if __name__ == "__main__":
    unittest.main()
