#!/usr/bin/env python3
from __future__ import annotations

import copy
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import textwrap
import time
import unittest
from unittest import mock
from pathlib import Path

import torch

from build_v3_runtime_dataset import covered_windows
from tmcra_v3_online_runtime import load_persisted_parent_chunks, ordered_graph_parents, parent_subchunks
from tmcra_v3_product_writer import (
    DeepSeekProductWriter,
    ProductWriterError,
    ProductWriterResponseError,
    call_with_source_claim_heartbeat,
    claim_source_journal,
    exact_json_object,
    exact_source_tokens,
    finish_source_journal,
    ingest_product_message,
    journal_source_message,
    mark_source_api_call_started,
    load_jsonl_for_resume,
    merge_segment_outputs,
    exact_evidence_spans,
    pending_interaction_candidates,
    repair_warning_journal_payloads,
    reconstruct_persisted_message,
    reopen_failed_source_journal,
    source_journal_row,
    split_capacity_range,
    stage_source_enrichment,
    stage_source_persisted,
    validate_writer_output,
)
from tmcra_v3_reranker import ChannelAwareMemoryReranker, listwise_loss
from tmcra_v3_schema import CHANNEL_NAMES, SCHEMA_VERSION, validate_candidate, validate_split_isolation


def candidate() -> dict:
    return {
        "candidate_id": "c1",
        "text": "memory text",
        "channels": {name: 0.0 for name in CHANNEL_NAMES},
        "labels": {"relevance": True, "hard_negative": False, "evidence_role": "positive"},
    }


class SchemaTests(unittest.TestCase):
    def test_valid_candidate(self) -> None:
        validate_candidate(candidate(), context="test")

    def test_serialized_channel_key_order_is_irrelevant(self) -> None:
        value = candidate()
        value["channels"] = {name: 0.0 for name in sorted(CHANNEL_NAMES)}
        validate_candidate(value, context="test")

    def test_positive_hard_negative_collision_rejected(self) -> None:
        value = candidate()
        value["labels"]["hard_negative"] = True
        with self.assertRaisesRegex(RuntimeError, "cannot be a hard negative"):
            validate_candidate(value, context="test")

    def test_label_named_channel_rejected(self) -> None:
        value = candidate()
        value["channels"] = dict(value["channels"])
        value["channels"].pop(CHANNEL_NAMES[-1])
        value["channels"]["hard_negative"] = 1.0
        with self.assertRaises(RuntimeError):
            validate_candidate(value, context="test")

    def test_split_overlap_rejected(self) -> None:
        train = [{"question_id": "q1", "query_text": "same"}]
        holdout = [{"question_id": "q2", "query_text": "same"}]
        with self.assertRaisesRegex(RuntimeError, "query overlap"):
            validate_split_isolation(train, holdout)


class ModelTests(unittest.TestCase):
    def test_subchunk_windows_cover_without_gaps(self) -> None:
        text = " ".join(f"token-{index}" for index in range(500))
        spans = covered_windows(text, max_chars=320, overlap=40)
        self.assertEqual(spans[0][0], 0)
        self.assertEqual(spans[-1][1], len(text))
        self.assertTrue(all(right_start <= left_end for (_, left_end), (right_start, _) in zip(spans, spans[1:])))
        self.assertTrue(all(end - start <= 320 for start, end in spans))

    def test_candidate_permutation_equivariance(self) -> None:
        torch.manual_seed(3)
        model = ChannelAwareMemoryReranker(32, len(CHANNEL_NAMES), hidden_dim=64, layers=1)
        model.eval()
        representations = torch.randn(1, 7, 32)
        semantic = torch.randn(1, 7)
        channels = torch.randn(1, 7, len(CHANNEL_NAMES))
        mask = torch.ones(1, 7, dtype=torch.bool)
        permutation = torch.tensor([4, 1, 6, 0, 3, 5, 2])
        inverse = torch.argsort(permutation)
        with torch.no_grad():
            base = model(representations, semantic, channels, mask)
            shuffled = model(
                representations[:, permutation],
                semantic[:, permutation],
                channels[:, permutation],
                mask[:, permutation],
            )
        self.assertTrue(torch.allclose(base, shuffled[:, inverse], atol=1e-5, rtol=1e-5))

    def test_listwise_loss_has_gradients(self) -> None:
        scores = torch.tensor([[0.2, -0.1, 0.4]], requires_grad=True)
        labels = torch.tensor([[0.0, 1.0, 0.0]])
        mask = torch.ones_like(labels, dtype=torch.bool)
        loss = listwise_loss(scores, labels, mask)
        loss.backward()
        self.assertIsNotNone(scores.grad)
        self.assertGreater(float(scores.grad.abs().sum()), 0.0)

    def test_listwise_loss_uses_positive_bag_mass(self) -> None:
        scores = torch.tensor([[2.0, -1.0, 0.0]])
        labels = torch.tensor([[1.0, 1.0, 0.0]])
        mask = torch.ones_like(labels, dtype=torch.bool)
        expected = -torch.log((torch.exp(scores[0, 0]) + torch.exp(scores[0, 1])) / torch.exp(scores[0]).sum())
        self.assertTrue(torch.allclose(listwise_loss(scores, labels, mask), expected))

    def test_listwise_loss_applies_sample_weights(self) -> None:
        scores = torch.tensor([[2.0, 0.0], [0.0, 2.0]])
        labels = torch.tensor([[1.0, 0.0], [1.0, 0.0]])
        mask = torch.ones_like(labels, dtype=torch.bool)
        weights = torch.tensor([1.0, 0.25])
        first = -torch.log_softmax(scores[0], dim=0)[0]
        second = -torch.log_softmax(scores[1], dim=0)[0]
        expected = (first + 0.25 * second) / 1.25
        self.assertTrue(torch.allclose(listwise_loss(scores, labels, mask, weights), expected))


class FixedAnswerRunnerTests(unittest.TestCase):
    def test_single_logical_gpt_call_and_repair_hooks_disabled(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            tmp = Path(raw_tmp)
            harness = tmp / "fake_harness.py"
            harness.write_text(
                textwrap.dedent(
                    """
                    import json
                    import os

                    def answer_llm_config():
                        return (
                            os.environ["TMCRA_ANSWER_BASE_URL"],
                            os.environ["TMCRA_ANSWER_MODEL"],
                            os.environ["TMCRA_ANSWER_API_KEY"],
                        )

                    def chat_completion(*args, **kwargs):
                        return json.dumps({"answer": "ok"})

                    def complete_profile_answer_from_evidence(question, answer, windows):
                        return "mutated-by-profile-hook"

                    def answer_question(question, memory_hits, evidence_windows=None):
                        answer = json.loads(chat_completion())["answer"]
                        answer = complete_profile_answer_from_evidence(question, answer, evidence_windows or [])
                        if os.getenv("TMCRA_ANSWER_REASONING_ORGANIZER", "1") != "off":
                            chat_completion()
                        return answer
                    """
                ),
                encoding="utf-8",
            )
            evidence = tmp / "evidence.jsonl"
            evidence.write_text(
                json.dumps(
                    {
                        "question_id": "q1",
                        "question_type": "knowledge-update",
                        "question": "What is remembered?",
                        "question_date": "2026-01-01",
                        "gold_answer": "ok",
                        "selected_session_ids": ["s1"],
                        "answer_session_ids": ["s1"],
                        "evidence_windows": [{"memory_id": "m1", "score": 1.0, "text": "ok"}],
                    }
                )
                + "\n",
                encoding="utf-8",
            )
            out_dir = tmp / "out"
            env = dict(os.environ)
            env.update(
                {
                    "TMCRA_ANSWER_BASE_URL": "https://example.invalid/v1",
                    "TMCRA_ANSWER_MODEL": "gpt-5.4",
                    "TMCRA_ANSWER_API_KEY": "test-key",
                }
            )
            result = subprocess.run(
                [
                    sys.executable,
                    str(Path(__file__).with_name("run_v3_gpt54_answers.py")),
                    "--evidence",
                    str(evidence),
                    "--harness",
                    str(harness),
                    "--out-dir",
                    str(out_dir),
                    "--workers",
                    "1",
                    "--attempts",
                    "1",
                ],
                env=env,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, msg=result.stdout + result.stderr)
            report = json.loads((out_dir / "report.json").read_text(encoding="utf-8"))
            answer = json.loads((out_dir / "answers.jsonl").read_text(encoding="utf-8"))
            self.assertEqual(report["fixed_answer_layer"]["TMCRA_ANSWER_REASONING_ORGANIZER"], "off")
            self.assertEqual(report["disabled_harness_hooks"], ["complete_profile_answer_from_evidence"])
            self.assertEqual(answer["logical_answer_calls"], 1)
            self.assertEqual(answer["hypothesis"], "ok")


class OnlineRuntimeTests(unittest.TestCase):
    def test_writer_source_role_overrides_incorrect_model_echo(self) -> None:
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [
                {
                    "interaction_type": "question",
                    "status": "open",
                    "evidence_span_id": "e0",
                    "intent": "seek_information",
                    "about": [],
                }
            ],
            "resolutions": [],
        }
        validated = validate_writer_output(
            payload,
            "Can you tell me more?",
            message_role="user",
        )
        self.assertEqual(validated["message_role"], "user")
        self.assertEqual(
            validated["validation_warnings"][0]["code"],
            "model_message_role_overridden",
        )

    def test_exact_json_object_accepts_safe_transport_wrappers(self) -> None:
        self.assertEqual(exact_json_object(' \n {"ok":true}\t'), {"ok": True})
        warnings = []
        self.assertEqual(exact_json_object('```json\n{"ok":true}\n```', warnings=warnings), {"ok": True})
        self.assertEqual(warnings[0]["code"], "json_fence_removed")
        with self.assertRaises(ProductWriterError):
            exact_json_object('{"ok":true,}')

    def test_exact_evidence_spans_include_full_message_and_sentences(self) -> None:
        content = "I wake at 7:00 AM. On weekends, I wake at 7:30 AM."
        spans = exact_evidence_spans(content)
        self.assertEqual(
            spans[0],
            {"span_id": "e0", "text": content, "char_start": 0, "char_end": len(content)},
        )
        self.assertEqual(
            [span["text"] for span in spans[1:]],
            ["I wake at 7:00 AM.", "On weekends, I wake at 7:30 AM."],
        )

    def test_exact_source_tokens_are_stable_and_ordered(self) -> None:
        self.assertEqual(
            exact_source_tokens("wake at 7:30 AM"),
            [
                {"token_id": 0, "text": "wake"},
                {"token_id": 1, "text": "at"},
                {"token_id": 2, "text": "7:30"},
                {"token_id": 3, "text": "AM"},
            ],
        )
        self.assertEqual(
            [item["text"] for item in exact_source_tokens("\u7532\u4e59\u3002\u4e19\u4e01\u3002")],
            ["\u7532", "\u4e59", "\u3002", "\u4e19", "\u4e01", "\u3002"],
        )

    def test_evidence_spans_preserve_repeated_text_positions(self) -> None:
        spans = exact_evidence_spans("Same. Same.")
        self.assertEqual([span["text"] for span in spans[1:]], ["Same.", "Same."])
        self.assertEqual(
            [(span["char_start"], span["char_end"]) for span in spans[1:]],
            [(0, 5), (6, 11)],
        )

    def test_graph_preserves_repeated_evidence_positions_and_polarity(self) -> None:
        repo = Path(
            "/opt/tmcra-data/migration/legacy/"
            "tmcra_api_service/private/tmcra-integrated"
        )
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from experiments.replacement.adapters.memory_adapters import GraphSessionMemoryAdapter
        from experiments.replacement.memory_graph import SessionMemoryEdgeV2, SessionMemoryRecordV2

        content = "Same. Same."
        base_assertion = {
            "memory_type": "fact",
            "entity_key": "echo.test",
            "attribute_key": "statement",
            "operation": "replace",
            "relation": "states_same",
            "temporal_status": "current",
            "facets": [],
        }
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [
                {**base_assertion, "evidence_span_id": "e1", "polarity": "positive"},
                {**base_assertion, "evidence_span_id": "e2", "polarity": "negative"},
            ],
            "interactions": [],
            "resolutions": [],
        }
        extraction = validate_writer_output(payload, content, message_role="user")
        adapter = GraphSessionMemoryAdapter(auto_extract=False, storage_backend="memory", scope_id="test:repeat")
        persisted = ingest_product_message(
            adapter,
            SessionMemoryRecordV2,
            SessionMemoryEdgeV2,
            scope_id="test:repeat",
            session_id="session-a",
            session_index=0,
            message_id="s000_m000",
            message_index=0,
            date="2025/01/02 (Thu) 03:04",
            timestamp="2025-01-02T03:04:00+00:00",
            role="user",
            content=content,
            extraction=extraction,
        )
        semantic = [
            record
            for record in adapter.graph.records_by_id.values()
            if record.metadata.get("content_variant") == "product_semantic_memory"
        ]
        self.assertEqual(len(semantic), 2)
        self.assertEqual(
            {(record.metadata["evidence_char_start"], record.metadata["polarity"]) for record in semantic},
            {(0, "positive"), (6, "negative")},
        )
        grounded_sources = {
            edge.source_memory_id
            for edge in adapter.graph.memory_edges.values()
            if edge.edge_type == "grounded_in"
        }
        self.assertEqual(grounded_sources, {record.memory_id for record in semantic})
        self.assertEqual(persisted["semantic"], 2)

    def test_product_graph_marks_fast_layer_and_disables_legacy_profile_aggregate(self) -> None:
        repo = Path(
            "/opt/tmcra-data/migration/legacy/"
            "tmcra_api_service/private/tmcra-integrated"
        )
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from experiments.replacement.adapters.memory_adapters import GraphSessionMemoryAdapter
        from experiments.replacement.memory_graph import SessionMemoryEdgeV2, SessionMemoryRecordV2

        extraction = validate_writer_output(
            {
                "schema_version": "tmcra.memory-write.v3.4",
                "message_role": "user",
                "assertions": [
                    {
                        "memory_type": "preference",
                        "entity_key": "drink.tea",
                        "attribute_key": "preference",
                        "operation": "replace",
                        "evidence_span_id": "e0",
                        "relation": "prefers",
                        "temporal_status": "current",
                        "polarity": "positive",
                        "facets": [],
                    }
                ],
                "interactions": [],
                "resolutions": [],
            },
            "I prefer tea.",
            message_role="user",
        )
        with mock.patch.dict(os.environ, {"TMCRA_LEGACY_PROFILE_LAYER_ENABLED": "0"}):
            adapter = GraphSessionMemoryAdapter(
                auto_extract=False,
                storage_backend="memory",
                scope_id="test:fast-layer",
            )
            ingest_product_message(
                adapter,
                SessionMemoryRecordV2,
                SessionMemoryEdgeV2,
                scope_id="test:fast-layer",
                session_id="session-a",
                session_index=0,
                message_id="s000_m000",
                message_index=0,
                date="2025/01/02 (Thu) 03:04",
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
                extraction=extraction,
            )
        self.assertTrue(adapter.graph.records_by_id)
        self.assertTrue(all(record.metadata.get("memory_layer") == "fast" for record in adapter.graph.records_by_id.values()))
        self.assertFalse(
            any(
                record.metadata.get("profile_aggregate_node") or record.metadata.get("profile_cluster_node")
                for record in adapter.graph.records_by_id.values()
            )
        )

    def test_capacity_split_refuses_to_cut_an_unpunctuated_fact(self) -> None:
        content = "one long fact whose subject and value must remain together"
        self.assertIsNone(split_capacity_range(content, 0, len(content)))

    def test_resume_repairs_only_an_incomplete_final_jsonl_row(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "events.jsonl"
            path.write_bytes(b'{"ok":1}\n{"partial":')
            rows, repaired = load_jsonl_for_resume(path)
            self.assertTrue(repaired)
            self.assertEqual(rows, [{"ok": 1}])
            self.assertEqual(path.read_text(encoding="utf-8"), '{"ok":1}\n')
            path.write_bytes(b'{"broken":}\n{"ok":2}\n')
            with self.assertRaisesRegex(ProductWriterError, "malformed non-tail"):
                load_jsonl_for_resume(path)

    def test_product_source_journal_persists_before_enrichment(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            journal_source_message(
                path,
                scope_id="test:journal",
                session_id="session-a",
                session_index=0,
                message_id="s000_m000",
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="How many stars do I need?",
            )
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    "SELECT enrichment_status, content FROM product_source_journal"
                ).fetchone()
            self.assertEqual(row, ("pending", "How many stars do I need?"))
            claim_owner = "test:first"
            claim_token = claim_source_journal(
                path,
                scope_id="test:journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
            )
            self.assertTrue(claim_token)
            finish_source_journal(
                path,
                scope_id="test:journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
                claim_token=str(claim_token),
                status="failed",
                error="writer unavailable",
            )
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    "SELECT enrichment_status, error FROM product_source_journal"
                ).fetchone()
            self.assertEqual(row, ("failed", "writer unavailable"))
            reopen_failed_source_journal(path, scope_id="test:journal", message_id="s000_m000")
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    "SELECT enrichment_status, error FROM product_source_journal"
                ).fetchone()
            self.assertEqual(row, ("pending", ""))

    def test_product_source_journal_persists_explicit_warning_status(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            journal_source_message(
                path,
                scope_id="test:warning-journal",
                session_id="session-a",
                session_index=0,
                message_id="s000_m000",
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
            )
            warnings = json.dumps([{"code": "invalid_facet_quarantined"}])
            claim_owner = "test:warning"
            claim_token = claim_source_journal(
                path,
                scope_id="test:warning-journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
            )
            self.assertTrue(claim_token)
            finish_source_journal(
                path,
                scope_id="test:warning-journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
                claim_token=str(claim_token),
                status="enriched_with_warnings",
                source_record_id="source.s000.m000:1",
                error=warnings,
            )
            with sqlite3.connect(path) as connection:
                row = connection.execute(
                    "SELECT enrichment_status, source_record_id, error FROM product_source_journal"
                ).fetchone()
            self.assertEqual(row, ("enriched_with_warnings", "source.s000.m000:1", warnings))

    def test_warning_journal_preserves_and_repairs_large_structured_payload(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            scope_id = "test:large-warning-journal"
            message_id = "s000_m000"
            journal_source_message(
                path,
                scope_id=scope_id,
                session_id="session-a",
                session_index=0,
                message_id=message_id,
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="assistant",
                content="A long answer.",
            )
            warning_rows = [
                {
                    "path": f"root.interactions[{index}].about[0]",
                    "code": "mismatched_redundant_facet_quote_ignored",
                    "error": "token coordinates remain authoritative",
                    "dropped_count": 0,
                }
                for index in range(40)
            ]
            extraction = {
                "schema_version": "tmcra.memory-write.v3.4",
                "message_role": "assistant",
                "assertions": [],
                "interactions": [],
                "resolutions": [],
                "validation_warnings": warning_rows,
                "quarantined_item_count": 0,
            }
            claim_owner = "test:large-warning"
            claim_token = claim_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=claim_owner,
            )
            self.assertTrue(claim_token)
            stage_source_enrichment(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=claim_owner,
                claim_token=str(claim_token),
                extraction=extraction,
                call_metadata={"model": "deepseek-v4-pro"},
            )
            serialized = json.dumps(
                warning_rows, ensure_ascii=False, sort_keys=True
            )
            self.assertGreater(len(serialized), 1000)
            finish_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=claim_owner,
                claim_token=str(claim_token),
                status="enriched_with_warnings",
                source_record_id="source.s000.m000:1",
                error=serialized,
            )
            with sqlite3.connect(path) as connection:
                stored = connection.execute(
                    "SELECT error FROM product_source_journal"
                ).fetchone()[0]
                connection.execute(
                    "UPDATE product_source_journal SET error=?",
                    (stored[:1000],),
                )
            self.assertEqual(json.loads(stored), warning_rows)
            self.assertEqual(
                repair_warning_journal_payloads(path, scope_id=scope_id), 1
            )
            with sqlite3.connect(path) as connection:
                repaired = connection.execute(
                    "SELECT error FROM product_source_journal"
                ).fetchone()[0]
            self.assertEqual(json.loads(repaired), warning_rows)

    def test_source_journal_stages_writer_output_and_graph_commit(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            journal_source_message(
                path,
                scope_id="test:staged-journal",
                session_id="session-a",
                session_index=0,
                message_id="s000_m000",
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
            )
            extraction = {
                "schema_version": "tmcra.memory-write.v3.4",
                "message_role": "user",
                "assertions": [],
                "interactions": [],
                "resolutions": [],
                "validation_warnings": [],
                "quarantined_item_count": 0,
            }
            metadata = {"model": "deepseek-v4-pro", "api_call_count": 1}
            persisted = {"source_record_id": "source.s000.m000:1", "source": 1}
            claim_owner = "test:staged"
            claim_token = claim_source_journal(
                path,
                scope_id="test:staged-journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
            )
            self.assertTrue(claim_token)
            stage_source_enrichment(
                path,
                scope_id="test:staged-journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
                claim_token=str(claim_token),
                extraction=extraction,
                call_metadata=metadata,
            )
            stage_source_persisted(
                path,
                scope_id="test:staged-journal",
                message_id="s000_m000",
                claim_owner=claim_owner,
                claim_token=str(claim_token),
                persisted=persisted,
            )
            row = source_journal_row(path, scope_id="test:staged-journal", message_id="s000_m000")
            self.assertIsNotNone(row)
            self.assertEqual(json.loads(row["extraction_json"]), extraction)
            self.assertEqual(json.loads(row["call_metadata_json"]), metadata)
            self.assertEqual(json.loads(row["persisted_json"]), persisted)

    def test_source_journal_competing_process_claims_allow_one_writer_to_proceed(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            scope_id = "test:competing-claims"
            message_id = "s000_m000"
            journal_source_message(
                path,
                scope_id=scope_id,
                session_id="session-a",
                session_index=0,
                message_id=message_id,
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
            )
            claim_script = textwrap.dedent(
                """
                import sys
                import time
                from pathlib import Path
                from tmcra_v3_product_writer import claim_source_journal

                while not Path(sys.argv[5]).exists():
                    time.sleep(0.01)
                token = claim_source_journal(
                    sys.argv[1],
                    scope_id=sys.argv[2],
                    message_id=sys.argv[3],
                    claim_owner=sys.argv[4],
                    lease_seconds=60,
                )
                print(token or "")
                """
            )
            start_gate = path.with_suffix(".start")
            commands = [
                [
                    sys.executable,
                    "-c",
                    claim_script,
                    str(path),
                    scope_id,
                    message_id,
                    f"writer:{index}",
                    str(start_gate),
                ]
                for index in range(2)
            ]
            processes = [subprocess.Popen(command, text=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE) for command in commands]
            start_gate.touch()
            results = [process.communicate(timeout=30) for process in processes]
            self.assertTrue(all(process.returncode == 0 for process in processes), msg=str(results))
            tokens = [stdout.strip() for stdout, _ in results]
            self.assertEqual(sum(bool(token) for token in tokens), 1)
            row = source_journal_row(path, scope_id=scope_id, message_id=message_id)
            self.assertIsNotNone(row)
            self.assertIn(row["claim_owner"], {"writer:0", "writer:1"})
            self.assertIn(row["claim_token"], tokens)

    def test_source_journal_expired_claim_recovers_staged_transaction_without_rewrite(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            scope_id = "test:staged-recovery"
            message_id = "s000_m000"
            journal_source_message(
                path,
                scope_id=scope_id,
                session_id="session-a",
                session_index=0,
                message_id=message_id,
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
            )
            extraction = {
                "schema_version": "tmcra.memory-write.v3.4",
                "message_role": "user",
                "assertions": [],
                "interactions": [],
                "resolutions": [],
                "validation_warnings": [],
                "quarantined_item_count": 0,
            }
            first_owner = "writer:interrupted"
            first_token = claim_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=first_owner,
                lease_seconds=60,
            )
            self.assertTrue(first_token)
            stage_source_enrichment(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=first_owner,
                claim_token=str(first_token),
                extraction=extraction,
                call_metadata={"model": "deepseek-v4-pro", "api_call_count": 1},
            )
            with self.assertRaises(ProductWriterError):
                stage_source_persisted(
                    path,
                    scope_id=scope_id,
                    message_id=message_id,
                    claim_owner="writer:other",
                    claim_token="wrong-token",
                    persisted={"source_record_id": "source.s000.m000:1", "source": 1},
                )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE product_source_journal SET claim_expires_at=? WHERE scope_id=? AND message_id=?",
                    ("2000-01-01T00:00:00+00:00", scope_id, message_id),
                )
            connection.close()
            recovery_owner = "writer:recovery"
            recovery_token = claim_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=recovery_owner,
                lease_seconds=60,
            )
            self.assertTrue(recovery_token)
            staged = source_journal_row(path, scope_id=scope_id, message_id=message_id)
            self.assertIsNotNone(staged)
            self.assertEqual(json.loads(staged["extraction_json"]), extraction)
            persisted = {"source_record_id": "source.s000.m000:1", "source": 1}
            stage_source_persisted(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=recovery_owner,
                claim_token=str(recovery_token),
                persisted=persisted,
            )
            finish_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=recovery_owner,
                claim_token=str(recovery_token),
                status="enriched",
                source_record_id="source.s000.m000:1",
            )
            completed = source_journal_row(path, scope_id=scope_id, message_id=message_id)
            self.assertIsNotNone(completed)
            self.assertEqual(completed["enrichment_status"], "enriched")
            self.assertEqual(json.loads(completed["extraction_json"]), extraction)
            self.assertEqual(completed["claim_owner"], "")
            self.assertEqual(completed["claim_token"], "")

    def test_expired_unstaged_writer_call_requires_explicit_resume(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            scope_id = "test:uncertain-call"
            message_id = "s000_m000"
            journal_source_message(
                path,
                scope_id=scope_id,
                session_id="session-a",
                session_index=0,
                message_id=message_id,
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
            )
            owner = "writer:lost"
            token = claim_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=owner,
                lease_seconds=60,
            )
            self.assertTrue(token)
            mark_source_api_call_started(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=owner,
                claim_token=str(token),
            )
            with sqlite3.connect(path) as connection:
                connection.execute(
                    "UPDATE product_source_journal SET claim_expires_at=? "
                    "WHERE scope_id=? AND message_id=?",
                    ("2000-01-01T00:00:00+00:00", scope_id, message_id),
                )
            with self.assertRaisesRegex(ProductWriterError, "uncertain external-call outcome"):
                claim_source_journal(
                    path,
                    scope_id=scope_id,
                    message_id=message_id,
                    claim_owner="writer:replacement",
                )
            row = source_journal_row(path, scope_id=scope_id, message_id=message_id)
            self.assertEqual(row["enrichment_status"], "failed")
            self.assertIn("explicit resume required", row["error"])
            reopen_failed_source_journal(path, scope_id=scope_id, message_id=message_id)
            self.assertTrue(
                claim_source_journal(
                    path,
                    scope_id=scope_id,
                    message_id=message_id,
                    claim_owner="writer:explicit-resume",
                )
            )

    def test_writer_claim_heartbeat_covers_long_external_call(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            path = Path(raw_tmp) / "memory.sqlite3"
            scope_id = "test:writer-heartbeat"
            message_id = "s000_m000"
            journal_source_message(
                path,
                scope_id=scope_id,
                session_id="session-a",
                session_index=0,
                message_id=message_id,
                message_index=0,
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content="I prefer tea.",
            )
            owner = "writer:heartbeat"
            token = claim_source_journal(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=owner,
                lease_seconds=1,
            )
            self.assertTrue(token)
            mark_source_api_call_started(
                path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=owner,
                claim_token=str(token),
            )
            result = call_with_source_claim_heartbeat(
                lambda: (time.sleep(2.0), "done")[1],
                path=path,
                scope_id=scope_id,
                message_id=message_id,
                claim_owner=owner,
                claim_token=str(token),
                lease_seconds=1,
            )
            self.assertEqual(result, "done")
            row = source_journal_row(path, scope_id=scope_id, message_id=message_id)
            self.assertEqual(row["claim_token"], token)
            self.assertFalse(
                claim_source_journal(
                    path,
                    scope_id=scope_id,
                    message_id=message_id,
                    claim_owner="writer:competitor",
                )
            )

    def test_product_writer_requires_flash_and_pro_models(self) -> None:
        common = {"base_url": "https://example.invalid/v1", "api_keys": ["test"], "timeout": 1, "max_tokens": 256}
        with self.assertRaises(ProductWriterError):
            DeepSeekProductWriter(model="deepseek-chat", reviewer_model="deepseek-v4-pro", **common)
        with self.assertRaises(ProductWriterError):
            DeepSeekProductWriter(model="deepseek-v4-flash", reviewer_model="deepseek-chat", **common)
        writer = DeepSeekProductWriter(
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            **common,
        )
        self.assertEqual(writer.model, "deepseek-v4-flash")
        self.assertEqual(writer.reviewer_model, "deepseek-v4-pro")

    def test_product_writer_reports_length_finish_as_fatal_with_raw_response(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        response_payload = {
            "choices": [
                {
                    "finish_reason": "length",
                    "message": {"content": '{"schema_version":"tmcra.memory-write.v3.4"'},
                }
            ],
            "usage": {"prompt_tokens": 10, "completion_tokens": 256, "total_tokens": 266},
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch("tmcra_v3_product_writer.urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(ProductWriterResponseError, "finish_reason='length'") as caught:
                writer._request(
                    model="deepseek-v4-pro",
                    system_prompt="test",
                    user_payload={"test": True},
                    stage="writer_primary_pro",
                )
        self.assertIn("schema_version", caught.exception.response_content)
        self.assertEqual(caught.exception.request_metadata["finish_reason"], "length")

    def test_product_writer_counts_http_200_invalid_choices_as_a_physical_attempt(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        response_payload = {
            "choices": [],
            "usage": {"prompt_tokens": 17, "completion_tokens": 0, "total_tokens": 17},
        }

        class FakeResponse:
            def __enter__(self):
                return self

            def __exit__(self, *_args):
                return False

            def read(self):
                return json.dumps(response_payload).encode("utf-8")

        with mock.patch("tmcra_v3_product_writer.urllib.request.urlopen", return_value=FakeResponse()):
            with self.assertRaisesRegex(ProductWriterResponseError, "returned 0 choices") as caught:
                writer._request(
                    model="deepseek-v4-pro",
                    system_prompt="test",
                    user_payload={"test": True},
                    stage="writer_primary_pro",
                )
        self.assertEqual(caught.exception.request_metadata["finish_reason"], "invalid_response")
        self.assertEqual(caught.exception.request_metadata["prompt_tokens"], 17)
        self.assertEqual(caught.exception.request_metadata["model"], "deepseek-v4-pro")

    def test_product_writer_has_no_semantic_item_or_facet_count_cap(self) -> None:
        content = " ".join(f"token{index}" for index in range(80))
        assertions = []
        for assertion_index in range(17):
            assertions.append(
                {
                    "memory_type": "fact",
                    "entity_key": f"profile.item{assertion_index}",
                    "attribute_key": f"value{assertion_index}",
                    "operation": "replace",
                    "evidence_span_id": "e0",
                    "relation": "has_value",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "facets": (
                        [
                            {
                                "type": "entity",
                                "role": f"facet_{facet_index}",
                                "token_start": 0,
                                "token_end": 64 if facet_index == 0 else facet_index,
                            }
                            for facet_index in range(5)
                        ]
                        if assertion_index == 0
                        else []
                    ),
                }
            )
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": assertions,
            "interactions": [],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="user")
        self.assertEqual(len(validated["assertions"]), 17)
        self.assertEqual(len(validated["assertions"][0]["facets"]), 5)
        self.assertEqual(validated["assertions"][0]["facets"][0]["token_end"], 64)
        self.assertEqual(validated["validation_warnings"], [])

    def test_validator_preserves_semantic_variants_and_merges_only_exact_duplicates(self) -> None:
        content = "I prefer tea in the morning."
        assertion = {
            "memory_type": "preference",
            "entity_key": "drink.tea",
            "attribute_key": "timing",
            "operation": "replace",
            "evidence_span_id": "e0",
            "relation": "prefers",
            "temporal_status": "current",
            "polarity": "positive",
            "facets": [{"type": "time", "role": "time", "token_start": 4, "token_end": 5}],
        }
        duplicate_with_extra_facet = copy.deepcopy(assertion)
        duplicate_with_extra_facet["facets"].append(
            {"type": "entity", "role": "drink", "token_start": 2, "token_end": 2}
        )
        different_relation = copy.deepcopy(assertion)
        different_relation["relation"] = "schedules"
        interaction = {
            "interaction_type": "request",
            "status": "open",
            "evidence_span_id": "e0",
            "intent": "remember_preference",
            "about": [{"type": "entity", "role": "drink", "token_start": 2, "token_end": 2}],
        }
        different_status = copy.deepcopy(interaction)
        different_status["status"] = "informational"
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [assertion, duplicate_with_extra_facet, different_relation],
            "interactions": [interaction, different_status],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="user")
        self.assertEqual(len(validated["assertions"]), 2)
        self.assertEqual(len(validated["assertions"][0]["facets"]), 2)
        self.assertEqual(len(validated["interactions"]), 2)
        self.assertEqual(validated["quarantined_item_count"], 0)

    def test_product_writer_segments_only_after_explicit_length_and_merges_all_items(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        content = "One fact. Two facts. Three facts. Four facts. Five facts."
        calls: list[dict] = []

        def metadata(finish_reason: str) -> dict:
            return {
                "model": "deepseek-v4-pro",
                "api_key_index": 0,
                "latency_seconds": 0.1,
                "response_sha256": finish_reason,
                "finish_reason": finish_reason,
                "max_output_tokens": 256,
                "prompt_tokens": 10,
                "completion_tokens": 20,
                "total_tokens": 30,
            }

        def request(**kwargs):
            calls.append(kwargs)
            if len(calls) == 1:
                raise ProductWriterResponseError(
                    "length",
                    response_content='{"schema_version":"tmcra.memory-write.v3.4"',
                    request_metadata=metadata("length"),
                )
            capacity_range = kwargs["user_payload"]["capacity_segment"]
            if capacity_range["char_start"] == 0:
                entity_key, attribute_key, relation = "segment.left", "fact", "contains_fact"
            else:
                entity_key, attribute_key, relation = "segment.right", "fact", "contains_fact"
            output = {
                "schema_version": "tmcra.memory-write.v3.4",
                "message_role": "user",
                "assertions": [
                    {
                        "memory_type": "fact",
                        "entity_key": entity_key,
                        "attribute_key": attribute_key,
                        "operation": "replace",
                        "evidence_span_id": "e0",
                        "relation": relation,
                        "temporal_status": "current",
                        "polarity": "positive",
                        "facets": [],
                    }
                ],
                "interactions": [],
                "resolutions": [],
            }
            return json.dumps(output), metadata("stop")

        writer._request = request  # type: ignore[method-assign]
        output, call_metadata = writer.write(
            current_message={"role": "user", "timestamp": "2025-01-02T00:00:00+00:00", "content": content},
            previous_message=None,
        )
        self.assertEqual(len(output["assertions"]), 2)
        self.assertEqual({item["evidence_span_id"] for item in output["assertions"]}, {"c0.e0", "c1.e0"})
        self.assertEqual(call_metadata["writer_mode"], "capacity_segmented_on_length")
        self.assertEqual(call_metadata["api_call_count"], 3)
        self.assertEqual(call_metadata["capacity_segment_count"], 2)
        self.assertTrue(all(call["model"] == "deepseek-v4-pro" for call in calls))
        self.assertEqual(calls[1]["user_payload"]["full_current_message_context"]["content"], content)

    def test_capacity_merge_rejects_conflicting_resolution_states(self) -> None:
        base = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [],
            "validation_warnings": [],
            "quarantined_item_count": 0,
        }
        first = {
            **base,
            "resolutions": [
                {
                    "interaction_id": "interaction.1",
                    "resolution": "partial",
                    "evidence_span_id": "c0.e0",
                    "evidence_quote": "Part one.",
                    "evidence_char_start": 0,
                    "evidence_char_end": 9,
                }
            ],
        }
        second = copy.deepcopy(first)
        second["resolutions"][0].update(
            {
                "resolution": "resolved",
                "evidence_span_id": "c1.e0",
                "evidence_quote": "Final answer.",
                "evidence_char_start": 10,
                "evidence_char_end": 23,
            }
        )
        with self.assertRaisesRegex(ProductWriterError, "conflicting resolutions"):
            merge_segment_outputs([first, second], message_role="assistant")

    def test_single_response_rejects_conflicting_resolution_states(self) -> None:
        interaction_id = "interaction.s000_m000.0:1"
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [],
            "resolutions": [
                {
                    "interaction_id": interaction_id,
                    "resolution": "partial",
                    "evidence_span_id": "e0",
                },
                {
                    "interaction_id": interaction_id,
                    "resolution": "resolved",
                    "evidence_span_id": "e0",
                },
            ],
        }
        with self.assertRaisesRegex(ProductWriterError, "conflicting states for the same interaction"):
            validate_writer_output(
                payload,
                "I can answer part now and finish it now.",
                message_role="assistant",
                pending_interaction_ids=[interaction_id],
            )

    def test_product_writer_routes_user_directly_to_one_pro_call(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        content = "I value careful work more than fast work."
        base_assertion = {
            "memory_type": "preference",
            "entity_key": "work.style",
            "attribute_key": "priority",
            "operation": "replace",
            "evidence_span_id": "e0",
            "relation": "prefers",
            "temporal_status": "current",
            "polarity": "positive",
            "facets": [{"type": "state", "role": "priority", "token_start": 2, "token_end": 3}],
        }
        pro_output = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [base_assertion],
            "interactions": [],
            "resolutions": [],
        }
        metadata = {
            "model": "deepseek-v4-pro",
            "api_key_index": 0,
            "latency_seconds": 0.1,
            "response_sha256": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }
        requests = []

        def request(**kwargs):
            requests.append(kwargs)
            return json.dumps(pro_output), metadata

        writer._request = request  # type: ignore[method-assign]
        output, call_metadata = writer.write(
            current_message={"role": "user", "timestamp": "2025-01-02T00:00:00+00:00", "content": content},
            previous_message=None,
        )
        self.assertEqual(output["assertions"][0]["facets"][0]["quote"], "careful work")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["model"], "deepseek-v4-pro")
        self.assertEqual(requests[0]["user_payload"]["evidence_spans"][0]["source"], "full_current_message")
        self.assertNotIn("text", requests[0]["user_payload"]["evidence_spans"][0])
        self.assertEqual(requests[0]["user_payload"]["source_tokens"][2], "careful")
        self.assertEqual(call_metadata["writer_mode"], "single_pass_routed")
        self.assertEqual(call_metadata["routing_reason"], "authoritative_user_memory")

    def test_product_writer_routes_assistant_by_pending_interaction(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        content = "You need 120 stars."
        output = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [],
            "resolutions": [],
        }
        metadata = {
            "model": "test",
            "api_key_index": 0,
            "latency_seconds": 0.1,
            "response_sha256": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }
        requested_models = []

        def request(**kwargs):
            requested_models.append(kwargs["model"])
            return json.dumps(output), metadata

        writer._request = request  # type: ignore[method-assign]
        writer.write(
            current_message={"role": "assistant", "timestamp": "2025-01-02T00:00:00+00:00", "content": content},
            previous_message=None,
        )
        self.assertEqual(requested_models, ["deepseek-v4-flash"])
        writer.write(
            current_message={"role": "assistant", "timestamp": "2025-01-02T00:00:00+00:00", "content": content},
            previous_message=None,
            pending_interactions=[
                {
                    "interaction_id": "interaction.s000_m000.0:1",
                    "interaction_type": "question",
                    "status": "open",
                    "speaker": "user",
                    "intent": "ask_required_stars",
                    "evidence_quote": "How many stars?",
                }
            ],
        )
        self.assertEqual(requested_models, ["deepseek-v4-flash", "deepseek-v4-pro"])

    def test_product_writer_quarantines_invalid_assertion_without_second_call(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        content = "I agree, especially now during the pandemic, reliable sources matter."
        invalid = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [
                {
                    "memory_type": "preference",
                    "entity_key": "news.sources",
                    "attribute_key": "reliability",
                    "operation": "replace",
                    "evidence_span_id": "e999",
                    "relation": "values",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "facets": [],
                }
            ],
            "interactions": [],
            "resolutions": [],
        }
        metadata = {
            "model": "deepseek-v4-pro",
            "api_key_index": 0,
            "latency_seconds": 0.1,
            "response_sha256": "test",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }
        calls = []

        def request(**kwargs):
            calls.append(kwargs)
            return json.dumps(invalid), metadata

        writer._request = request  # type: ignore[method-assign]
        output, _ = writer.write(
            current_message={"role": "user", "timestamp": "2025-01-02T00:00:00+00:00", "content": content},
            previous_message=None,
        )
        self.assertEqual(len(calls), 1)
        self.assertEqual(output["assertions"], [])
        self.assertEqual(output["quarantined_item_count"], 1)
        self.assertEqual(output["validation_warnings"][0]["code"], "invalid_assertion_quarantined")

    def test_product_writer_keeps_root_contract_errors_fatal(self) -> None:
        writer = DeepSeekProductWriter(
            base_url="https://example.invalid/v1",
            model="deepseek-v4-flash",
            reviewer_model="deepseek-v4-pro",
            api_keys=["test"],
            timeout=1,
            max_tokens=256,
        )
        metadata = {
            "model": "deepseek-v4-pro",
            "api_key_index": 0,
            "latency_seconds": 0.1,
            "response_sha256": "test",
            "finish_reason": "stop",
            "prompt_tokens": 1,
            "completion_tokens": 1,
            "total_tokens": 2,
        }
        writer._request = lambda **_kwargs: (json.dumps({"schema_version": "tmcra.memory-write.v3.4"}), metadata)  # type: ignore[method-assign]
        with self.assertRaises(ProductWriterResponseError):
            writer.write(
                current_message={"role": "user", "timestamp": "2025-01-02T00:00:00+00:00", "content": "Hello."},
                previous_message=None,
            )

    def test_product_writer_accepts_exact_adjacent_facet_evidence(self) -> None:
        content = "I agree, especially now during the pandemic, reliable sources matter."
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [
                {
                    "memory_type": "preference",
                    "entity_key": "news.sources",
                    "attribute_key": "reliability",
                    "operation": "replace",
                    "evidence_span_id": "e0",
                    "relation": "values",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "facets": [{"type": "time", "role": "context", "token_start": 4, "token_end": 6}],
                }
            ],
            "interactions": [],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="user")
        self.assertEqual(validated["assertions"][0]["facets"][0]["quote"], "during the pandemic,")

    def test_product_writer_drops_bad_optional_facet_but_keeps_assertion(self) -> None:
        content = "I prefer reliable news sources."
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [
                {
                    "memory_type": "preference",
                    "entity_key": "news.sources",
                    "attribute_key": "reliability",
                    "operation": "replace",
                    "evidence_span_id": "e0",
                    "relation": "prefers",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "facets": [{"type": "state", "role": "quality", "token_start": 99, "token_end": 100}],
                }
            ],
            "interactions": [],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="user")
        self.assertEqual(len(validated["assertions"]), 1)
        self.assertEqual(validated["assertions"][0]["facets"], [])
        self.assertEqual(validated["validation_warnings"][0]["code"], "invalid_facet_quarantined")

    def test_product_writer_ignores_redundant_facet_quote_without_dropping_about(self) -> None:
        content = "Please add other categories to this list."
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [
                {
                    "interaction_type": "question",
                    "status": "open",
                    "evidence_span_id": "e0",
                    "intent": "add_categories",
                    "about": [
                        {
                            "type": "entity",
                            "role": "subject",
                            "token_start": 2,
                            "token_end": 3,
                            "quote": "other categories",
                        },
                        {
                            "type": "entity",
                            "role": "object",
                            "token_start": 5,
                            "token_end": 6,
                            "quote": "model supplied mismatch",
                        },
                    ],
                }
            ],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="assistant")
        self.assertEqual(len(validated["interactions"][0]["about"]), 2)
        self.assertEqual(validated["interactions"][0]["about"][1]["quote"], "this list.")
        self.assertEqual(validated["quarantined_item_count"], 0)
        self.assertEqual(
            [warning["code"] for warning in validated["validation_warnings"]],
            ["redundant_facet_quote_ignored", "mismatched_redundant_facet_quote_ignored"],
        )

    def test_product_writer_keeps_assertion_and_question_in_separate_layers(self) -> None:
        content = "Actually, I need 120 stars to reach Gold, not 300. How should I organize my loyalty cards?"
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [
                {
                    "memory_type": "state",
                    "entity_key": "starbucks.rewards.gold.level",
                    "attribute_key": "required.stars",
                    "operation": "replace",
                    "evidence_span_id": "e1",
                    "relation": "requires_stars_for_gold",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "facets": [
                        {"type": "quantity", "role": "required_stars", "token_start": 3, "token_end": 4}
                    ],
                }
            ],
            "interactions": [
                {
                    "interaction_type": "question",
                    "status": "open",
                    "evidence_span_id": "e2",
                    "intent": "organize_loyalty_cards",
                    "about": [
                        {"type": "entity", "role": "target", "token_start": 15, "token_end": 16}
                    ],
                }
            ],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="user")
        self.assertEqual(
            validated["assertions"][0]["canonical_key"],
            "user.starbucks.rewards.gold.level.fact.required.stars",
        )
        self.assertEqual(len(validated["assertions"]), 1)
        self.assertEqual(len(validated["interactions"]), 1)

    def test_product_writer_preserves_item_after_warned_identifier_case_normalization(self) -> None:
        content = "Please reference this as FER."
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [],
            "interactions": [
                {
                    "interaction_type": "request",
                    "status": "open",
                    "evidence_span_id": "e0",
                    "intent": "reference_as_FER",
                    "about": [],
                }
            ],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, content, message_role="user")
        self.assertEqual(validated["interactions"][0]["intent"], "reference_as_fer")
        self.assertEqual(validated["quarantined_item_count"], 0)
        self.assertEqual(validated["validation_warnings"][0]["code"], "identifier_case_normalized")
        self.assertEqual(validated["validation_warnings"][0]["dropped_count"], 0)

    def test_product_writer_structurally_separates_goal_and_fact_slots(self) -> None:
        def assertion_payload(memory_type: str) -> dict:
            return {
                "schema_version": "tmcra.memory-write.v3.4",
                "message_role": "user",
                "assertions": [
                    {
                        "memory_type": memory_type,
                        "entity_key": "starbucks.rewards",
                        "attribute_key": "gold.level",
                        "operation": "replace",
                        "evidence_span_id": "e0",
                        "relation": "describes_gold_level",
                        "temporal_status": "current",
                        "polarity": "positive",
                        "facets": [],
                    }
                ],
                "interactions": [],
                "resolutions": [],
            }

        goal = validate_writer_output(
            assertion_payload("goal"),
            "My goal is to reach Gold.",
            message_role="user",
        )["assertions"][0]
        fact = validate_writer_output(
            assertion_payload("state"),
            "Gold requires 120 stars.",
            message_role="user",
        )["assertions"][0]
        self.assertEqual(goal["canonical_key"], "user.starbucks.rewards.goal.gold.level")
        self.assertEqual(fact["canonical_key"], "user.starbucks.rewards.fact.gold.level")
        self.assertNotEqual(goal["canonical_key"], fact["canonical_key"])

        generic = assertion_payload("plan")
        generic["assertions"][0].update(
            {
                "entity_key": "user",
                "attribute_key": "laptop.purchase.consideration",
            }
        )
        normalized = validate_writer_output(
            generic,
            "I plan to buy a laptop.",
            message_role="user",
        )["assertions"][0]
        self.assertEqual(normalized["graph_entity_key"], "user.laptop.purchase")

    def test_product_writer_quarantines_unknown_evidence_span(self) -> None:
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [
                {
                    "memory_type": "fact",
                    "entity_key": "project",
                    "attribute_key": "name",
                    "operation": "replace",
                    "evidence_span_id": "e999",
                    "relation": "project_name",
                    "temporal_status": "current",
                    "polarity": "positive",
                    "facets": [],
                }
            ],
            "interactions": [],
            "resolutions": [],
        }
        validated = validate_writer_output(payload, "Yes, that is correct.", message_role="user")
        self.assertEqual(validated["assertions"], [])
        self.assertEqual(validated["quarantined_item_count"], 1)

    def test_product_writer_accepts_assistant_resolution_but_not_assistant_fact(self) -> None:
        interaction_id = "interaction.s000_m000.0:1"
        content = "You need 120 stars to reach Gold."
        payload = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [],
            "resolutions": [
                {
                    "interaction_id": interaction_id,
                    "resolution": "resolved",
                    "evidence_span_id": "e0",
                }
            ],
        }
        validated = validate_writer_output(
            payload,
            content,
            message_role="assistant",
            pending_interaction_ids=[interaction_id],
        )
        self.assertEqual(validated["resolutions"][0]["resolution"], "resolved")
        payload["assertions"] = [
            {
                "memory_type": "fact",
                "entity_key": "starbucks.rewards.gold.level",
                "attribute_key": "required.stars",
                "operation": "replace",
                "evidence_span_id": "e0",
                "relation": "requires_stars",
                "temporal_status": "current",
                "polarity": "positive",
                "facets": [],
            }
        ]
        with self.assertRaises(ProductWriterError):
            validate_writer_output(
                payload,
                content,
                message_role="assistant",
                pending_interaction_ids=[interaction_id],
            )

    def test_product_question_answer_lifecycle_updates_interaction_without_fact_promotion(self) -> None:
        repo = Path(
            "/opt/tmcra-data/migration/legacy/"
            "tmcra_api_service/private/tmcra-integrated"
        )
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from experiments.replacement.adapters.memory_adapters import GraphSessionMemoryAdapter
        from experiments.replacement.memory_graph import SessionMemoryEdgeV2, SessionMemoryRecordV2

        adapter = GraphSessionMemoryAdapter(auto_extract=False, storage_backend="memory", scope_id="test:lifecycle")
        user_extraction = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "user",
            "assertions": [],
            "interactions": [
                {
                    "interaction_type": "question",
                    "status": "open",
                    "evidence_quote": "How many stars do I need?",
                    "intent": "ask_required_stars",
                    "about": [],
                }
            ],
            "resolutions": [],
        }
        ingest_product_message(
            adapter,
            SessionMemoryRecordV2,
            SessionMemoryEdgeV2,
            scope_id="test:lifecycle",
            session_id="session-a",
            session_index=0,
            message_id="s000_m000",
            message_index=0,
            date="2025/01/02 (Thu) 03:04",
            timestamp="2025-01-02T03:04:00+00:00",
            role="user",
            content="How many stars do I need?",
            extraction=user_extraction,
        )
        pending = pending_interaction_candidates(
            adapter.graph,
            current_role="assistant",
            session_id="session-a",
            session_index=0,
            current_message_index=1,
        )
        self.assertEqual(len(pending), 1)
        interaction_id = pending[0]["interaction_id"]
        self.assertNotIn(adapter.graph.records_by_id[interaction_id].slot_key, adapter.graph.slot_heads)
        assistant_extraction = {
            "schema_version": "tmcra.memory-write.v3.4",
            "message_role": "assistant",
            "assertions": [],
            "interactions": [],
            "resolutions": [
                {
                    "interaction_id": interaction_id,
                    "resolution": "resolved",
                    "evidence_quote": "You need 120 stars.",
                }
            ],
        }
        persisted = ingest_product_message(
            adapter,
            SessionMemoryRecordV2,
            SessionMemoryEdgeV2,
            scope_id="test:lifecycle",
            session_id="session-a",
            session_index=0,
            message_id="s000_m001",
            message_index=1,
            date="2025/01/02 (Thu) 03:04",
            timestamp="2025-01-02T03:04:01+00:00",
            role="assistant",
            content="You need 120 stars.",
            extraction=assistant_extraction,
        )
        self.assertEqual(persisted["resolution_count"], 1)
        self.assertEqual(
            adapter.graph.records_by_id[interaction_id].metadata["interaction_status"],
            "resolved",
        )
        self.assertTrue(any(edge.edge_type == "answered_by" for edge in adapter.graph.memory_edges.values()))
        self.assertTrue(any(edge.edge_type == "grounded_in" for edge in adapter.graph.memory_edges.values()))

    def test_pending_interactions_are_scoped_to_the_current_reply_turn(self) -> None:
        repo = Path(
            "/opt/tmcra-data/migration/legacy/"
            "tmcra_api_service/private/tmcra-integrated"
        )
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from experiments.replacement.adapters.memory_adapters import GraphSessionMemoryAdapter
        from experiments.replacement.memory_graph import SessionMemoryEdgeV2, SessionMemoryRecordV2

        adapter = GraphSessionMemoryAdapter(auto_extract=False, storage_backend="memory", scope_id="test:reply-scope")

        def ingest_question(session_id: str, session_index: int, message_id: str, content: str) -> None:
            ingest_product_message(
                adapter,
                SessionMemoryRecordV2,
                SessionMemoryEdgeV2,
                scope_id="test:reply-scope",
                session_id=session_id,
                session_index=session_index,
                message_id=message_id,
                message_index=0,
                date="2025/01/02 (Thu) 03:04",
                timestamp="2025-01-02T03:04:00+00:00",
                role="user",
                content=content,
                extraction={
                    "schema_version": "tmcra.memory-write.v3.4",
                    "message_role": "user",
                    "assertions": [],
                    "interactions": [
                        {
                            "interaction_type": "question",
                            "status": "open",
                            "evidence_quote": content,
                            "intent": "ask_question",
                            "about": [],
                        }
                    ],
                    "resolutions": [],
                },
            )

        ingest_question("session-a", 0, "s000_m000", "What happened yesterday?")
        ingest_question("session-b", 1, "s001_m000", "How should I set my alarm?")
        pending = pending_interaction_candidates(
            adapter.graph,
            current_role="assistant",
            session_id="session-b",
            session_index=1,
            current_message_index=1,
        )
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["evidence_quote"], "How should I set my alarm?")

    def test_pending_interactions_and_graph_anchors_have_no_fixed_count_cap(self) -> None:
        repo = Path(
            "/opt/tmcra-data/migration/legacy/"
            "tmcra_api_service/private/tmcra-integrated"
        )
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        from experiments.replacement.adapters.memory_adapters import GraphSessionMemoryAdapter
        from experiments.replacement.memory_graph import SessionMemoryEdgeV2, SessionMemoryRecordV2

        adapter = GraphSessionMemoryAdapter(auto_extract=False, storage_backend="memory", scope_id="test:no-caps")
        for index in range(40):
            content = f"Question {index}?"
            ingest_product_message(
                adapter,
                SessionMemoryRecordV2,
                SessionMemoryEdgeV2,
                scope_id="test:no-caps",
                session_id="session-a",
                session_index=0,
                message_id=f"s000_m{index:03d}",
                message_index=index,
                date="2025/01/02 (Thu) 03:04",
                timestamp=f"2025-01-02T03:04:{index:02d}+00:00",
                role="user",
                content=content,
                extraction={
                    "schema_version": "tmcra.memory-write.v3.4",
                    "message_role": "user",
                    "assertions": [],
                    "interactions": [
                        {
                            "interaction_type": "question",
                            "status": "open",
                            "evidence_quote": content,
                            "intent": f"ask_question_{index}",
                            "about": [
                                {"type": "entity", "role": "topic", "quote": f"topic {index}"}
                            ],
                        }
                    ],
                    "resolutions": [],
                },
            )
        pending = pending_interaction_candidates(
            adapter.graph,
            current_role="assistant",
            session_id="session-a",
            session_index=0,
            current_message_index=40,
        )
        self.assertEqual(len(pending), 40)
        self.assertEqual(pending[0]["about"][0]["role"], "topic")

        anchors = [f"anchor-{index}" for index in range(12)]
        record = SessionMemoryRecordV2(
            memory_id="anchor-test",
            category="fact",
            slot_key="anchor.test",
            value="anchor test",
            relation="has_anchors",
            anchor_concepts=anchors,
            evidence_anchors=anchors,
            salience=1.0,
            confidence=1.0,
            source_kind="test",
            turn_index=100,
            state="active",
            metadata={},
        )
        adapter.graph.add_records([record])
        self.assertEqual(adapter.graph.records_by_id["anchor-test"].anchor_concepts, anchors)
        self.assertEqual(adapter.graph.records_by_id["anchor-test"].evidence_anchors, anchors)

    def test_writer_strict_json_is_parsed_before_jsonish_repairs(self) -> None:
        repo = Path(
            "/opt/tmcra-data/migration/legacy/"
            "tmcra_api_service/private/tmcra-integrated"
        )
        writer_path = repo / "experiments/replacement/semantic_memory_writer.py"
        self.assertTrue(writer_path.exists())
        if str(repo) not in sys.path:
            sys.path.insert(0, str(repo))
        spec = importlib.util.spec_from_file_location("tmcra_writer_parser_regression", writer_path)
        self.assertIsNotNone(spec)
        self.assertIsNotNone(spec.loader if spec else None)
        module = importlib.util.module_from_spec(spec)
        assert spec is not None and spec.loader is not None
        sys.modules[spec.name] = module
        spec.loader.exec_module(module)
        payload = {
            "turn_intent": {"intent": "memory_assertion", "write_allowed": True},
            "write_proposals": [
                {
                    "category": "fact",
                    "value": "I've named the room 'nerd cave'",
                    "source_span": "I've named the room 'nerd cave'",
                },
                {
                    "category": "fact",
                    "value": "I don't use fallback parsing",
                    "source_span": "I don't use fallback parsing",
                },
            ],
        }
        parsed = module._extract_json_object(json.dumps(payload))
        self.assertEqual(parsed, payload)
        self.assertNotIn("_tmcra_json_repair_status", parsed)

    def test_persisted_graph_audit_rebuilds_unlabeled_dense_inventory(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            db_path = Path(raw_tmp) / "memory.sqlite3"
            scope = "tenant:test"
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE records (scope_id TEXT, memory_id TEXT, turn_index INTEGER, metadata_json TEXT)"
                )
                connection.execute(
                    "CREATE TABLE audit_turn_log (scope_id TEXT, event_index INTEGER, payload_json TEXT)"
                )
                metadata = {
                    "dia_id": "longmemeval:test:s003_c02",
                    "sidecar_hint_metadata": {"chunk_id": "s003_c02"},
                }
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?)",
                    (scope, "memory-1", 1, json.dumps(metadata)),
                )
                parent_text = (
                    "LongMemEval session_id=session-a date=2025/01/02 (Thu) 03:04 "
                    "[session-a turn=1 role=user] I bought a red bicycle."
                )
                payload = {
                    "kind": "memory_write",
                    "turn_index": 1,
                    "record_ids": ["memory-1"],
                    "text": f"[2026-01-01T00:00:00+00:00] user: {parent_text}",
                }
                connection.execute(
                    "INSERT INTO audit_turn_log VALUES (?, ?, ?)",
                    (scope, 0, json.dumps(payload)),
                )
            parents = load_persisted_parent_chunks(db_path, scope)
            self.assertEqual(len(parents), 1)
            self.assertEqual(parents[0]["session_index"], 3)
            self.assertEqual(parents[0]["parent_chunk_index"], 2)
            self.assertEqual(parents[0]["session_id"], "session-a")
            candidates = parent_subchunks(
                parents,
                scope_id=scope,
                subchunk_chars=1800,
                subchunk_overlap=200,
            )
            self.assertEqual(len(candidates), 1)
            self.assertNotIn("labels", candidates[0])
            self.assertIn("I bought a red bicycle.", candidates[0]["text"])

    def test_graph_events_map_to_persisted_parent_locations(self) -> None:
        locations, unmapped = ordered_graph_parents(
            [
                "event::longmemeval:test:s003_c02",
                "event::longmemeval:test:s003_c02",
                "tmcra.profile.cluster.unmapped",
            ],
            valid_locations={(3, 2)},
            strict_prefix=True,
        )
        self.assertEqual(locations, [(3, 2)])
        self.assertEqual(unmapped, [])

    def test_persisted_product_messages_rebuild_inventory_after_restart(self) -> None:
        with tempfile.TemporaryDirectory() as raw_tmp:
            db_path = Path(raw_tmp) / "memory.sqlite3"
            scope = "tenant:product"
            message_id = "s002_m005"
            metadata = {
                "content_variant": "source_message",
                "raw_content": "I bought a red bicycle.",
                "speaker": "user",
                "timestamp": "2025-01-02T03:04:05+00:00",
                "session_id": "session-product",
                "session_index": 2,
                "message_id": message_id,
                "message_index": 5,
                "historical_date": "2025/01/02 (Thu) 03:04",
            }
            audit = {"metadata": {"message_id": message_id}}
            with sqlite3.connect(db_path) as connection:
                connection.execute(
                    "CREATE TABLE records (scope_id TEXT, memory_id TEXT, turn_index INTEGER, metadata_json TEXT)"
                )
                connection.execute(
                    "CREATE TABLE audit_turn_log (scope_id TEXT, event_index INTEGER, payload_json TEXT)"
                )
                connection.execute(
                    "INSERT INTO records VALUES (?, ?, ?, ?)",
                    (scope, "source-1", 1, json.dumps(metadata)),
                )
                connection.execute(
                    "INSERT INTO audit_turn_log VALUES (?, ?, ?)",
                    (scope, 0, json.dumps(audit)),
                )
            parents = load_persisted_parent_chunks(db_path, scope)
            self.assertEqual(parents[0]["parent_kind"], "message")
            self.assertEqual(parents[0]["parent_chunk_index"], 5)
            candidates = parent_subchunks(
                parents,
                scope_id=scope,
                subchunk_chars=1800,
                subchunk_overlap=200,
            )
            self.assertIn("role=user", candidates[0]["text"])
            self.assertIn("I bought a red bicycle.", candidates[0]["text"])
            self.assertEqual(candidates[0]["message_role"], "user")
            self.assertEqual(
                candidates[0]["historical_date"], "2025/01/02 (Thu) 03:04"
            )
            self.assertEqual(
                candidates[0]["timestamp"], "2025-01-02T03:04:05+00:00"
            )
            locations, unmapped = ordered_graph_parents(
                [f"event::tmcra:{scope}:{message_id}"],
                valid_locations={(2, 5)},
                strict_prefix=True,
            )
            self.assertEqual(locations, [(2, 5)])
            self.assertEqual(unmapped, [])


if __name__ == "__main__":
    unittest.main()
