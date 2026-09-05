from __future__ import annotations
import copy
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from fastapi.testclient import TestClient
from tmcra_service.app import create_app
from tmcra_service.api_models import FeedbackRequest, PromptEvidenceView
from tmcra_service.feedback_effects import apply_feedback
from tmcra_service.evidence_view import build_prompt_evidence
from tmcra_service.tests.test_content_deletion_contract import _settings, _seed_scope


class FeedbackEffectsTests(unittest.TestCase):
    def test_filters_derived_and_neighbor_evidence_without_mutating_original(self):
        evidence = {"evidence_windows": [
            {"source_record_id": "a", "text": "OLD SECRET", "actor_role": "user", "memory_contexts": [{"memory_id": "derived-a", "claim_text": "OLD DERIVED"}]},
            {"source_record_id": "b", "text": "unrelated", "actor_role": "user", "source_group_context": [{"source_record_id": "a", "text": "OLD NEIGHBOR"}]},
            {"source_record_id": "c", "text": "keep this", "actor_role": "assistant"},
        ], "compiled_evidence_packet": {"stale": "OLD SECRET"}}
        original = copy.deepcopy(evidence)
        effect = {"a": {"action": "correct", "feedback_id": "fb1", "replacement": "Correct user fact", "created_at": 100.0}}
        filtered = apply_feedback(evidence, effect)
        prompt = build_prompt_evidence(filtered, selected_route="raw")
        PromptEvidenceView.model_validate(prompt)
        self.assertNotIn("OLD", str(filtered))
        self.assertEqual(prompt["content"].count("Correct user fact"), 1)
        self.assertIn("keep this", prompt["content"])
        self.assertIn("explicit_user_correction", prompt["content"])
        self.assertEqual(evidence, original)
        self.assertEqual(len(prompt["sources"]), 2)

    def test_ignored_all_is_valid_empty_recall(self):
        filtered = apply_feedback({"evidence_windows": [{"source_record_id": "a", "text": "old"}]},
                                  {"a": {"action": "ignore"}})
        result = build_prompt_evidence(filtered, selected_route="raw")
        self.assertEqual(result["content"], "")
        PromptEvidenceView.model_validate(result)

    def test_correction_requires_replacement_and_targets(self):
        for body in ({"rating": "incorrect", "action": "correct", "memory_ids": ["a"]},
                     {"rating": "incorrect", "action": "ignore"}):
            with self.assertRaises(ValueError):
                FeedbackRequest.model_validate(body)

    def test_indexed_corrections_follow_recorrection_ignore_and_restore(self):
        with tempfile.TemporaryDirectory() as directory:
            commercial = create_app(_settings(Path(directory))).state.components.commercial

            def feedback(action, replacement=None, targets=None):
                return commercial.add_feedback("tenant-a", "scope-a", query_id=None, rating="incorrect",
                    memory_ids=targets or ["source-a"], comment=None, credential_id="test",
                    metadata={"_tmcra_action": action, "_tmcra_replacement": replacement})

            first = feedback("correct", "First corrected value")
            # The writer creates its own source ID; V4 recall retains session_id.
            indexed = {"evidence_windows": [{"source_record_id": "indexed-source-random-id",
                "session_id": f"correction-{first['feedback_id']}", "text": "First corrected value",
                "memory_contexts": [{"memory_id": "derived-correction", "claim_text": "First derived value"}]}]}
            second = feedback("correct", "Final corrected value")
            effects = commercial.feedback_effects("tenant-a", "scope-a")
            rendered = build_prompt_evidence(apply_feedback(indexed, effects), selected_route="raw")
            self.assertNotIn("First", rendered["content"])
            self.assertEqual(rendered["content"].count("Final corrected value"), 1)
            self.assertIn(second["feedback_id"], rendered["content"])

            feedback("ignore", targets=["indexed-source-random-id"])
            self.assertEqual(apply_feedback(indexed, commercial.feedback_effects("tenant-a", "scope-a"))["evidence_windows"], [])
            feedback("restore", targets=["indexed-source-random-id"])
            feedback("restore")
            restored = apply_feedback({"evidence_windows": [
                {"source_record_id": "source-a", "text": "Original source"}, *indexed["evidence_windows"]]},
                commercial.feedback_effects("tenant-a", "scope-a"))
            self.assertEqual([window["text"] for window in restored["evidence_windows"]], ["Original source"])

    def test_api_correction_idempotency_restore_scope_and_write_permission(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(_settings(Path(directory)))
            components = app.state.components
            components.auth.set_tenant_scopes("tenant-a", {"memory:read", "memory:write", "memory:feedback"})
            key = components.auth.create_key("tenant-a").api_key
            _seed_scope(components.storage, "tenant-a", "scope-a")
            headers = {"Authorization": f"Bearer {key}", "Idempotency-Key": "correction-test-one"}
            client = TestClient(app)
            self.addCleanup(client.close)
            body = {"rating": "incorrect", "memory_ids": ["source-a"], "action": "correct", "replacement": "The new value is 42"}
            result = client.post("/v1/scopes/scope-a/feedback", headers=headers, json=body)
            self.assertEqual(result.status_code, 201, result.text)
            self.assertTrue(result.json()["effective"])
            self.assertIsNotNone(result.json()["correction_job_id"], result.text)
            self.assertIn(result.json()["correction_index_status"], {"pending", "queued", "submission_pending"})
            replay = client.post("/v1/scopes/scope-a/feedback", headers=headers, json=body)
            self.assertEqual(replay.status_code, 201, replay.text)
            self.assertEqual(replay.json()["feedback_id"], result.json()["feedback_id"])
            changed = client.post("/v1/scopes/scope-a/feedback", headers=headers, json={**body, "replacement": "Different"})
            self.assertEqual(changed.status_code, 409, changed.text)
            self.assertEqual(components.commercial.feedback_effects("tenant-b", "scope-a"), {})
            self.assertEqual(components.commercial.feedback_effects("tenant-a", "scope-b"), {})
            effects = components.commercial.feedback_effects("tenant-a", "scope-a")
            self.assertEqual(effects["source-a"]["replacement"], body["replacement"])
            restore = client.post("/v1/scopes/scope-a/feedback", headers={**headers, "Idempotency-Key": "restore-feedback-one"},
                                  json={"rating": "helpful", "action": "restore", "memory_ids": [result.json()["feedback_id"]]})
            self.assertEqual(restore.status_code, 201, restore.text)
            restored_effects = components.commercial.feedback_effects("tenant-a", "scope-a")
            self.assertNotIn("source-a", restored_effects)
            self.assertEqual(restored_effects[result.json()["feedback_id"]]["corrections"], [])
            unknown = client.post("/v1/scopes/scope-a/feedback", headers={**headers, "Idempotency-Key": "unknown-feedback-one"},
                                 json={"rating": "incorrect", "action": "ignore", "memory_ids": ["nonexistent"]})
            self.assertEqual(unknown.status_code, 404, unknown.text)
            components.auth.set_tenant_scopes("tenant-read", {"memory:feedback"})
            read_key = components.auth.create_key("tenant-read").api_key
            denied = client.post("/v1/scopes/scope-a/feedback", headers={**headers, "Authorization": f"Bearer {read_key}"}, json=body)
            self.assertEqual(denied.status_code, 403, denied.text)

    def test_recall_endpoint_applies_feedback_before_rendering(self):
        with tempfile.TemporaryDirectory() as directory:
            app = create_app(_settings(Path(directory)))
            components = app.state.components
            components.auth.set_tenant_scopes("tenant-a", {"memory:read", "memory:feedback"})
            key = components.auth.create_key("tenant-a").api_key
            _seed_scope(components.storage, "tenant-a", "scope-a")
            paths = components.storage.scope_paths("tenant-a", "scope-a")
            components.commercial.add_feedback("tenant-a", "scope-a", query_id=None, rating="incorrect", memory_ids=["source-a"],
                comment=None, metadata={"_tmcra_action": "ignore"}, credential_id="test")
            snapshot = {"database": paths.database, "scope_id": paths.scope_id, "job_id": "index-test"}
            evidence = {"evidence_windows": [{"source_record_id": "source-a", "text": "SHOULD DISAPPEAR", "actor_role": "user"}]}
            online = mock.Mock()
            online.recall.return_value = (evidence, {})
            with mock.patch.object(components.storage, "active_snapshot", return_value=snapshot), \
                 mock.patch.object(components.online, "get", return_value=online), \
                 mock.patch.object(components.worker, "start"), mock.patch.object(components.worker, "stop"), TestClient(app) as client:
                result = client.post("/v1/scopes/scope-a/recall", headers={"Authorization": f"Bearer {key}"},
                    json={"query": "old value", "max_windows": 8, "evidence_mode": "raw"})
                self.assertEqual(result.status_code, 200, result.text)
                self.assertEqual(result.json()["prompt_evidence"]["content"], "")


if __name__ == "__main__":
    unittest.main()
