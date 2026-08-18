import unittest

from ops.compare_tmcra_v4_slow_reviews import compare


def _export(groups):
    entries = []
    for index, group in enumerate(groups):
        entries.append(
            {
                "worker": "worker_000",
                "region_key": "region",
                "resulting_capsule": {
                    "value": f"summary {index}",
                    "metadata": {
                        "capsule_key": f"key.{index}",
                        "claims": [
                            {
                                "canonical_slot": f"slot.{support_id}",
                                "text": f"text {support_id}",
                                "support": [support_id],
                            }
                            for support_id in group
                        ],
                    },
                },
            }
        )
    return {"prompt_version": "test", "entries": entries}


class CompareSlowReviewsTests(unittest.TestCase):
    def test_partition_change_is_nonblocking_when_support_is_lossless(self) -> None:
        report = compare(_export([["a", "b"]]), _export([["a"], ["b"]]))
        self.assertEqual(report["status"], "passed")
        self.assertEqual(report["partition_changed_support_count"], 2)
        self.assertEqual(report["partition_changed_region_count"], 1)

    def test_support_loss_is_blocking(self) -> None:
        report = compare(_export([["a", "b"]]), _export([["a"]]))
        self.assertEqual(report["status"], "failed")
        self.assertEqual(report["missing_support_ids"], ["b"])


if __name__ == "__main__":
    unittest.main()
