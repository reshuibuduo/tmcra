import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import run_tmcra_v4_compile_semantic_evidence as compiler
from test_tmcra_v4_semantic_evidence import contract, resolution, row


def metadata(stage):
    return {
        "physical_call_id": f"call-{stage}",
        "physical_api_call": True,
        "physical_api_calls": 1,
        "stage": stage,
        "model": "deepseek-v4-pro",
        "provider": "deepseek",
        "prompt_version": "test",
        "repair_call": False,
        "status": "completed",
        "usage": {
            "prompt_tokens": 100,
            "completion_tokens": 20,
            "prompt_cache_hit_tokens": 0,
            "prompt_cache_miss_tokens": 100,
            "total_tokens": 120,
        },
    }


class FakePlanner:
    task_calls = 0
    resolution_calls = 0

    def __init__(self, **_kwargs):
        pass

    def plan_task_contract(self, _row, repair_context=None):
        if repair_context is not None:
            raise AssertionError("unexpected repair")
        type(self).task_calls += 1
        return contract(), metadata("task_contract_planner")

    def plan_resolution(self, _row, _contract, _catalog, repair_context=None):
        if repair_context is not None:
            raise AssertionError("unexpected repair")
        type(self).resolution_calls += 1
        return resolution(), metadata("semantic_evidence_resolver")


class SemanticCompilerRunnerTests(unittest.TestCase):
    def test_stage_journals_prevent_repeated_api_calls(self):
        FakePlanner.task_calls = 0
        FakePlanner.resolution_calls = 0
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            evidence = root / "evidence.jsonl"
            evidence.write_text(json.dumps(row()) + "\n", encoding="utf-8")
            out = root / "out"
            argv = [
                "run_tmcra_v4_compile_semantic_evidence.py",
                "--evidence",
                str(evidence),
                "--out-dir",
                str(out),
                "--workers",
                "1",
            ]
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(compiler, "SemanticJsonPlanner", FakePlanner),
                mock.patch.object(compiler, "_load_shell_environment", return_value={}),
                mock.patch.object(compiler, "_key_pool", return_value=["k"]),
            ):
                self.assertEqual(compiler.main(), 0)
            self.assertEqual((FakePlanner.task_calls, FakePlanner.resolution_calls), (1, 1))
            self.assertTrue(next((out / "rows").glob("*.task_contract.json")).is_file())
            self.assertTrue(next((out / "rows").glob("*.resolution.json")).is_file())
            final_artifact = next(
                path
                for path in (out / "rows").glob("*.json")
                if not path.name.endswith(".task_contract.json")
                and not path.name.endswith(".resolution.json")
                and not path.name.endswith(".failure.json")
            )
            final_artifact.unlink()
            with (
                mock.patch.object(sys, "argv", argv),
                mock.patch.object(compiler, "SemanticJsonPlanner", FakePlanner),
                mock.patch.object(compiler, "_load_shell_environment", return_value={}),
                mock.patch.object(compiler, "_key_pool", return_value=["k"]),
            ):
                self.assertEqual(compiler.main(), 0)
            self.assertEqual((FakePlanner.task_calls, FakePlanner.resolution_calls), (1, 1))
            report = json.loads((out / "report.json").read_text(encoding="utf-8"))
            self.assertEqual(report["physical_call_count"], 2)
            self.assertEqual(report["row_count"], 1)


if __name__ == "__main__":
    unittest.main()
