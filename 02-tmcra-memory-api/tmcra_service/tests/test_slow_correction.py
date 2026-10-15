import copy
import json
import unittest
from unittest.mock import patch

import tmcra_v4_slow_graph as g


def proposal(slot="preference.beverage", support=None):
    return {"operations": [{"action": "create", "capsule_key": "morning.coffee",
        "summary": "I prefer coffee in the morning.", "claims": [{
            "canonical_slot": slot, "text": "I prefer coffee in the morning.",
            "support": ["fast.one"] if support is None else support, "counterevidence": []}]}]}


class Response:
    def __init__(self, value):
        self.value = value

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def getcode(self):
        return 200

    def read(self):
        return json.dumps({"choices": [{"finish_reason": "stop", "message": {
            "content": json.dumps(self.value)}}], "usage": {
                "prompt_tokens": 10, "completion_tokens": 20}}).encode()


class SlowCorrectionTests(unittest.TestCase):
    def manager(self):
        cfg = g.DeepSeekTierConfig(g.LOCAL_QWEN_BASE_URL, ("test-only",), 1000,
            model=g.LOCAL_QWEN_MODEL, provider=g.LOCAL_QWEN_PROVIDER,
            prompt_adapter=g.LOCAL_QWEN_SLOW_PROMPT_ADAPTER, timeout_seconds=300)
        return g.TieredGraphPatchManager(flash_config=cfg, pro_config=cfg)

    def invoke(self, manager, route="flash"):
        region = {"region_key": "morning.coffee", "evidence": [{
            "memory_id": "fast.one", "value": "I prefer coffee in the morning.",
            "record_state": "active", "metadata": {
                "canonical_slot_key": "preference.beverage", "durability": "durable"}}]}
        return manager._invoke(route, "test", getattr(manager, route), region, [],
                               {"delta_evidence_ids": ["fast.one"]})

    def test_flash_slot_error_is_corrected_without_relaxing_validation(self):
        manager = self.manager()
        with patch.object(g.urllib.request, "urlopen", side_effect=[
            Response(proposal("wrong.slot")), Response(proposal())]) as call:
            result = self.invoke(manager)
        self.assertEqual(result["operations"][0]["claims"][0]["canonical_slot"], "preference.beverage")
        self.assertEqual(call.call_count, 2)
        self.assertEqual(call.call_args.kwargs["timeout"], 300)
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 2)
        self.assertEqual(manager.last_call_metadata["usage"]["completion_tokens"], 40)
        self.assertEqual(manager.last_call_metadata["tier_calls"][0]["tier_stage"], "initial_flash")

    def test_structurally_invalid_pro_response_gets_one_correction(self):
        manager = self.manager()
        invalid = proposal()
        invalid["operations"].append(copy.deepcopy(invalid["operations"][0]))
        with patch.object(g.urllib.request, "urlopen", side_effect=[
            Response(invalid), Response(proposal())]) as call:
            self.invoke(manager, "pro")
        self.assertEqual(call.call_count, 2)
        self.assertTrue(manager.last_call_metadata["semantic_correction_applied"])

    def test_second_invalid_patch_fails_closed(self):
        manager = self.manager()
        with patch.object(g.urllib.request, "urlopen", side_effect=[
            Response(proposal("wrong.slot")), Response(proposal("still.wrong"))]) as call:
            with self.assertRaises(g.PatchValidationError):
                self.invoke(manager)
        self.assertEqual(call.call_count, 2)
        self.assertFalse(manager.last_call_metadata["semantic_correction_applied"])

    def test_transport_failure_is_not_retried(self):
        manager = self.manager()
        with patch.object(g.urllib.request, "urlopen", side_effect=TimeoutError()) as call:
            with self.assertRaises(g.TieredAPIError):
                self.invoke(manager)
        self.assertEqual(call.call_count, 1)

    def test_flash_correction_can_explicitly_request_pro(self):
        manager = self.manager()
        escalation = {"operations": [{"action": "escalate", "reason": g.FLASH_ESCALATION_REASON}]}
        with patch.object(g.urllib.request, "urlopen", side_effect=[
            Response(proposal("wrong.slot")), Response(escalation), Response(proposal())]) as call:
            self.invoke(manager)
        self.assertEqual(call.call_count, 3)
        self.assertEqual(manager.last_call_metadata["route"], "flash_to_pro")
        self.assertEqual(manager.last_call_metadata["physical_api_calls"], 3)


if __name__ == "__main__":
    unittest.main()
