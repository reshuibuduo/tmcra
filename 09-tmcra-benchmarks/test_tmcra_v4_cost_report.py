from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from run_tmcra_v4_writer_cost_pilot import _inventory, _sample

from tmcra_v4_cost_report import (
    CostReportError,
    build_report,
    collect_calls,
    extract_physical_calls,
)


class CostReportTests(unittest.TestCase):
    def test_writer_cost_pilot_samples_every_token_quartile(self) -> None:
        rows = []
        for index in range(12):
            rows.append(
                {
                    "question_id": f"q{index}",
                    "haystack_sessions": [
                        [{"role": "user", "content": ("word " * (index + 1)).strip()}]
                    ],
                    "haystack_session_ids": [f"s{index}"],
                    "haystack_dates": ["2026-01-01"],
                }
            )
        inventory = _inventory(rows)
        selected = _sample(inventory, 2)
        self.assertEqual(len(selected), 8)
        self.assertEqual(
            {item["quartile"] for item in selected},
            {0, 1, 2, 3},
        )

    def test_extracts_physical_children_not_aggregate_parent(self) -> None:
        row = {
            "model": "deepseek-v4-flash",
            "usage": {"prompt_tokens": 30, "completion_tokens": 3},
            "requests": [
                {
                    "physical_call_id": "a",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "prompt_cache_hit_tokens": 4,
                        "prompt_cache_miss_tokens": 6,
                    },
                },
                {
                    "physical_call_id": "b",
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 2,
                        "prompt_cache_hit_tokens": 8,
                        "prompt_cache_miss_tokens": 12,
                    },
                },
            ],
        }
        calls = extract_physical_calls(row, source="memory")
        self.assertEqual([call["call_id"] for call in calls], ["physical_call_id:a", "physical_call_id:b"])
        self.assertTrue(all(call["model"] == "deepseek-v4-flash" for call in calls))

    def test_slow_tier_calls_replace_aggregate_usage_and_keep_tier_stage(self) -> None:
        row = {
            "model": "deepseek-v4-pro",
            "route": "pro",
            "physical_api_calls": 2,
            "usage": {
                "prompt_tokens": 30,
                "completion_tokens": 3,
                "prompt_cache_hit_tokens": 12,
                "prompt_cache_miss_tokens": 18,
            },
            "tier_calls": [
                {
                    "physical_call_id": "initial",
                    "tier_stage": "initial_pro",
                    "usage": {
                        "prompt_tokens": 10,
                        "completion_tokens": 1,
                        "prompt_cache_hit_tokens": 4,
                        "prompt_cache_miss_tokens": 6,
                    },
                },
                {
                    "physical_call_id": "correction",
                    "tier_stage": "semantic_correction",
                    "usage": {
                        "prompt_tokens": 20,
                        "completion_tokens": 2,
                        "prompt_cache_hit_tokens": 8,
                        "prompt_cache_miss_tokens": 12,
                    },
                },
            ],
        }
        calls = extract_physical_calls(row, source="slow.sqlite3")
        self.assertEqual(
            [call["call_id"] for call in calls],
            ["physical_call_id:initial", "physical_call_id:correction"],
        )
        self.assertEqual(
            [call["stage"] for call in calls],
            ["initial_pro", "semantic_correction"],
        )
        self.assertTrue(all(call["model"] == "deepseek-v4-pro" for call in calls))

    def test_exact_deepseek_price(self) -> None:
        report = build_report(
            [
                {
                    "call_id": "physical_call_id:a",
                    "source": "memory",
                    "path": "root",
                    "model": "deepseek-v4-pro",
                    "stage": "reconcile",
                    "status": "completed",
                    "usage": {
                        "prompt_tokens": 1_000_000,
                        "completion_tokens": 1_000_000,
                        "prompt_cache_hit_tokens": 250_000,
                        "prompt_cache_miss_tokens": 750_000,
                    },
                }
            ]
        )
        self.assertAlmostEqual(report["exact_cost_cny"], 8.25625)
        self.assertEqual(report["physical_call_count"], 1)

    def test_missing_cache_breakdown_reports_bounds(self) -> None:
        report = build_report(
            [
                {
                    "call_id": "x",
                    "source": "memory",
                    "path": "root",
                    "model": "deepseek-v4-flash",
                    "stage": "writer",
                    "status": "completed",
                    "usage": {"prompt_tokens": 1_000_000, "completion_tokens": 0},
                }
            ]
        )
        self.assertIsNone(report["exact_cost_cny"])
        self.assertEqual(report["min_cost_cny"], 0.02)
        self.assertEqual(report["max_cost_cny"], 1.0)

    def test_same_physical_call_in_log_and_database_is_deduplicated(self) -> None:
        base = {
            "call_id": "physical_call_id:same",
            "path": "root",
            "model": "deepseek-v4-flash",
            "stage": "writer",
            "status": "completed",
            "usage": {
                "prompt_tokens": 10,
                "completion_tokens": 2,
                "prompt_cache_hit_tokens": 4,
                "prompt_cache_miss_tokens": 6,
            },
        }
        report = build_report([
            {**base, "source": "calls.jsonl"},
            {**base, "source": "memory.sqlite3"},
        ])
        self.assertEqual(report["physical_call_count"], 1)
        self.assertEqual(report["duplicate_observation_count"], 1)

    def test_provider_neutral_cache_field_names_are_supported(self) -> None:
        report = build_report([
            {
                "call_id": "neutral",
                "source": "memory.sqlite3",
                "path": "slow_graph_attempts:1",
                "model": "deepseek-v4-flash",
                "stage": "slow_flash",
                "status": "completed",
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 2,
                    "cache_read_input_tokens": 4,
                    "cache_miss_input_tokens": 6,
                },
            }
        ])
        self.assertIsNotNone(report["exact_cost_cny"])
        self.assertEqual(report["calls"][0]["usage"]["cache_hit_tokens"], 4)

    def test_failed_physical_call_without_usage_is_counted_and_cost_is_incomplete(self) -> None:
        calls = extract_physical_calls(
            {
                "physical_call_id": "failed",
                "physical_api_call": True,
                "model": "deepseek-v4-pro",
                "route": "reconcile",
                "status": "http_error",
            },
            source="failure.jsonl",
        )
        report = build_report(calls)
        self.assertEqual(report["physical_call_count"], 1)
        self.assertIsNone(report["exact_cost_cny"])
        self.assertIsNone(report["min_cost_cny"])
        self.assertEqual(report["by_stage_model"][0]["calls_without_usage"], 1)

    def test_deterministic_nonphysical_attempt_with_zero_usage_is_not_a_call(self) -> None:
        calls = extract_physical_calls(
            {
                "route": "deterministic_create",
                "physical_api_call": False,
                "physical_api_calls": 0,
                "usage": {"prompt_tokens": 0, "completion_tokens": 0},
            },
            source="slow.sqlite3",
        )
        self.assertEqual(calls, [])

    def test_cache_breakdown_must_balance(self) -> None:
        with self.assertRaises(CostReportError):
            build_report(
                [
                    {
                        "call_id": "x",
                        "source": "memory",
                        "path": "root",
                        "model": "deepseek-v4-flash",
                        "stage": "writer",
                        "status": "completed",
                        "usage": {
                            "prompt_tokens": 10,
                            "completion_tokens": 0,
                            "prompt_cache_hit_tokens": 2,
                            "prompt_cache_miss_tokens": 2,
                        },
                    }
                ]
            )

    def test_collects_jsonl_and_sqlite_call_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            jsonl = root / "calls.jsonl"
            jsonl.write_text(
                json.dumps(
                    {
                        "physical_call_id": "json",
                        "model": "deepseek-v4-flash",
                        "usage": {
                            "prompt_tokens": 1,
                            "completion_tokens": 1,
                            "prompt_cache_hit_tokens": 0,
                            "prompt_cache_miss_tokens": 1,
                        },
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            database = root / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE slow_graph_attempts(call_metadata_json TEXT NOT NULL)"
                )
                connection.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?)",
                    (
                        json.dumps(
                            {
                                "physical_call_id": "sqlite",
                                "request": {"model": "deepseek-v4-pro"},
                                "usage": {
                                    "prompt_tokens": 2,
                                    "completion_tokens": 1,
                                    "prompt_cache_hit_tokens": 1,
                                    "prompt_cache_miss_tokens": 1,
                                },
                            }
                        ),
                    ),
                )
                connection.commit()
            calls = collect_calls([jsonl], [database])
            self.assertEqual({call["call_id"] for call in calls}, {
                "physical_call_id:json", "physical_call_id:sqlite"
            })

    def test_collects_writer_and_reconciliation_response_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE v4_batch_journal(response_metadata_json TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE v4_reconciliation_jobs(response_metadata_json TEXT NOT NULL)"
                )
                connection.execute(
                    "CREATE TABLE v4_subject_attribution_audits(call_metadata_json TEXT NOT NULL)"
                )
                for table, call_id, model in (
                    ("v4_batch_journal", "writer", "deepseek-v4-flash"),
                    ("v4_reconciliation_jobs", "reconcile", "deepseek-v4-pro"),
                    ("v4_subject_attribution_audits", "attribution", "deepseek-v4-pro"),
                ):
                    column = (
                        "call_metadata_json"
                        if table == "v4_subject_attribution_audits"
                        else "response_metadata_json"
                    )
                    connection.execute(
                        f"INSERT INTO {table}({column}) VALUES(?)",
                        (
                            json.dumps(
                                {
                                    "physical_call_id": call_id,
                                    "model": model,
                                    "stage": table,
                                    "usage": {
                                        "prompt_tokens": 2,
                                        "completion_tokens": 1,
                                        "prompt_cache_hit_tokens": 0,
                                        "prompt_cache_miss_tokens": 2,
                                    },
                                }
                            ),
                        ),
                    )
                connection.commit()
            calls = collect_calls([], [database])
            self.assertEqual(
                {call["call_id"] for call in calls},
                {
                    "physical_call_id:writer",
                    "physical_call_id:reconcile",
                    "physical_call_id:attribution",
                },
            )

    def test_expired_slow_attempt_is_reported_as_unknown_outcome(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            database = Path(directory) / "memory.sqlite3"
            with closing(sqlite3.connect(database)) as connection:
                connection.execute(
                    "CREATE TABLE slow_graph_attempts("
                    "attempt_id TEXT,status TEXT,call_metadata_json TEXT,error TEXT)"
                )
                connection.execute(
                    "INSERT INTO slow_graph_attempts VALUES(?,?,?,?)",
                    (
                        "sga_1",
                        "expired",
                        "{}",
                        "claim lease expired; external call outcome uncertain; "
                        "explicit resume required",
                    ),
                )
                connection.commit()
            report = build_report(collect_calls([], [database]))
            self.assertEqual(report["physical_call_count"], 1)
            self.assertEqual(report["definite_physical_call_count"], 0)
            self.assertEqual(report["unknown_outcome_call_count"], 1)
            self.assertIsNone(report["exact_cost_cny"])
            self.assertEqual(report["known_priced_exact_component_cny"], 0.0)
            self.assertEqual(
                report["by_stage_model"][0]["stage"], "slow_graph_interrupted"
            )


if __name__ == "__main__":
    unittest.main()
