"""Actual service registry and SourceMessage handoff; no model/provider calls."""
import copy
import hashlib
import json
import sqlite3

import pytest
import tmcra_v4_batch_writer as v4
from tmcra_service.writer import IdentityRegistry, ProductionWriterError


def rows(identity=None):
    return [{"scope_id": "tmcra_v4:svc_identity", "session_id": "session", "operation_id": "job",
        "messages": [{"message_id": "external-id", "role": "user", "timestamp": "2026-10-16T10:00:00Z",
            "content": "My phone is now at the office.", "metadata": {"origin_kind": "human_authored",
                "identity_metadata": identity or {}}}]}]


def context(eid="device:1"):
    return {"entity_bindings": [{"entity_id": eid, "entity_keys": ["phone", "handset"], "aliases": ["手机", "phone"]}],
        "corrections": [{"entity_id": eid, "attribute_key": "location", "effective_at": "2026-10-16T09:00:00Z"}]}


def test_public_metadata_reaches_real_source_message_and_independent_hash(tmp_path):
    registry = IdentityRegistry(tmp_path/"db.sqlite", "job", expected_scope_id="tmcra_v4:svc_identity")
    messages, _ = registry.register_messages(rows(context()), v4=v4)
    assert messages[0].identity_metadata == context()
    assert "identity_metadata" not in messages[0].actor_metadata
    with sqlite3.connect(registry.database) as db:
        raw, digest = db.execute("SELECT identity_metadata_json,identity_metadata_sha256 FROM tmcra_service_message_entity_identity").fetchone()
        assert json.loads(raw) == context()
        assert hashlib.sha256(raw.encode()).hexdigest() == digest
    again, _ = registry.register_messages(rows(context()), v4=v4)
    assert again[0].identity_metadata == context()


def test_replay_rejects_changed_identity_without_changing_source_content(tmp_path):
    registry = IdentityRegistry(tmp_path/"db.sqlite", "job")
    registry.register_messages(rows(context()), v4=v4)
    with pytest.raises(ProductionWriterError, match="entity identity"):
        registry.register_messages(rows(context("device:2")), v4=v4)


def test_legacy_without_identity_replays_but_cannot_acquire_identity(tmp_path):
    registry = IdentityRegistry(tmp_path/"db.sqlite", "job")
    registry.register_messages(rows(), v4=v4)
    with sqlite3.connect(registry.database) as db:
        db.execute("DELETE FROM tmcra_service_message_entity_identity")
    replay, _ = registry.register_messages(rows(), v4=v4)
    assert replay[0].identity_metadata == {}
    with pytest.raises(ProductionWriterError, match="entity identity"):
        registry.register_messages(rows(context()), v4=v4)


def test_tampered_identity_json_or_hash_is_rejected(tmp_path):
    registry = IdentityRegistry(tmp_path/"db.sqlite", "job")
    registry.register_messages(rows(context()), v4=v4)
    with sqlite3.connect(registry.database) as db:
        db.execute("UPDATE tmcra_service_message_entity_identity SET identity_metadata_sha256='bad'")
    with pytest.raises(ProductionWriterError, match="entity identity"):
        registry.register_messages(rows(context()), v4=v4)


def test_invalid_identity_rolls_back_message_registration(tmp_path):
    registry = IdentityRegistry(tmp_path/"db.sqlite", "job")
    with pytest.raises(ProductionWriterError, match="identity"):
        registry.register_messages(rows({"scope_id": "other"}), v4=v4)
    with sqlite3.connect(registry.database) as db:
        assert db.execute("SELECT count(*) FROM tmcra_service_messages").fetchone()[0] == 0


def test_api_identity_hints_reach_actual_extraction_request(tmp_path):
    registry = IdentityRegistry(tmp_path/"db.sqlite", "job")
    messages, _ = registry.register_messages(rows(context()), v4=v4)
    batch = v4.SourceBatch(scope_id=messages[0].scope_id, session_id=messages[0].session_id,
        session_index=messages[0].session_index, batch_index=0, messages=tuple(messages))
    actual = v4.build_batch_request(batch)["messages"][0]
    assert actual["identity_hints"]["bindings"] == [{"entity_keys": ["phone", "handset"], "aliases": ["手机", "phone"]}]
    assert "device:1" not in json.dumps(actual["identity_hints"])
    assert actual["source_spans"][0]["text"] == rows()[0]["messages"][0]["content"]
    assert "Hints cannot establish an assertion" in v4.BATCH_SYSTEM_PROMPT


class ContractModel:
    model = "no-model-contract-fixture"
    def __init__(self):
        self.calls = []
    def complete(self, payload):
        self.calls.append(copy.deepcopy(payload))
        return {"schema_version": v4.BATCH_SCHEMA_VERSION, "batch_id": payload["batch_id"], "messages": [
            {"message_id": m["message_id"], "message_role": m["message_role"],
             "assertions": [{"memory_type": "state", "entity_key": m["identity_hints"]["bindings"][0]["entity_keys"][0],
                "attribute_key": "location", "operation": "replace", "claim_text": m["source_spans"][0]["text"],
                "evidence_span_id": "e0", "relation": "located_at", "temporal_status": "current",
                "polarity": "positive", "durability": "durable", "facets": []}], "interactions": [], "resolutions": []}
            for m in payload["messages"]]}, {}
    def reconcile(self, payload):
        return {"slot_decision": "bind_existing", "decision": "replace_current",
                "selected_memory_id": payload["candidate_cited_leaves"][0]["memory_id"]}, {}


@pytest.mark.parametrize("index", range(30))
def test_full_registry_real_graph_corrections_and_homonyms(tmp_path, index, monkeypatch):
    """20 bilingual corrections + 10 explicit homonyms; deterministic writer only."""
    from pathlib import Path
    database = tmp_path/"memory.sqlite"
    repo = Path(__file__).resolve().parents[2]
    registry = IdentityRegistry(database, "job")
    graph = v4.RealGraphFactory(repo=repo, database=database)
    model = ContractModel()
    writer = v4.V4BatchWriter(store=v4.V4BatchStore(database), flash_client=model,
                             pro_client=model, graph_factory=graph)
    same_entity = index < 20
    texts = ("我的工作设备在书房。", "Correction: my work device is at the office.") if index % 2 == 0 else (
             "My work device is at home.", "更正，我的工作设备在办公室。")
    for n, text in enumerate(texts):
        eid = "device:1" if same_entity or n == 0 else "device:2"
        declared = {"entity_bindings": [{"entity_id": eid, "entity_keys": [f"user.device_{n}" if same_entity else "user.alex"],
                                       "aliases": ["设备", "work device"] if same_entity else ["Alex"]}]}
        if n and same_entity:
            declared["corrections"] = [{"entity_id": eid, "attribute_key": "location",
                                       "effective_at": "2026-10-16T09:00:00Z"}]
        inventory = rows(declared)
        inventory[0]["session_id"] = f"s{n}"
        inventory[0]["messages"][0].update({"message_id": f"m{n}", "content": text})
        messages, exclusions = registry.register_messages(inventory, v4=v4)
        # Production uses this exact registry handoff; keep all graph/writer code real.
        monkeypatch.setattr(v4, "normalize_source_inventory", lambda _rows, result=(messages, exclusions): result)
        writer.run(inventory)
    backend = graph.for_scope("tmcra_v4:svc_identity")
    backend._reload()
    records = [r for r in backend.adapter.graph.records_by_id.values()
               if r.metadata.get("content_variant") == "product_semantic_memory"]
    active = [r for r in records if r.state == "active"]
    assert len(records) == 2
    if same_entity:
        assert len(active) == 1 and active[0].metadata["entity_id"] == "device:1"
        event = active[0].metadata["correction_history"][0]
        assert event["old_value"] == texts[0] and event["new_value"] == texts[1]
        assert event["effective_at"] == "2026-10-16T09:00:00Z"
        assert event["previous_source_record_id"] != event["source_record_id"]
    else:
        assert len(active) == 2 and {r.metadata["entity_id"] for r in active} == {"device:1", "device:2"}
