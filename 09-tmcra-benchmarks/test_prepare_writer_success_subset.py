import json
import tempfile
import unittest
from pathlib import Path

from ops.prepare_writer_success_subset import _selected_indices


class WriterSubsetSelectionTests(unittest.TestCase):
    def test_audit_passed_policy_uses_current_repaired_assets(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            workers = []
            for index, passed in enumerate((True, False)):
                worker = root / f"worker_{index:03d}"
                worker.mkdir()
                (worker / "native_memory.sqlite3").write_bytes(b"sqlite")
                (worker / "product_writer_report.json").write_text(
                    json.dumps({"completed": True}), encoding="utf-8"
                )
                (worker / "writer_chain_audit.json").write_text(
                    json.dumps({"status": "passed" if passed else "failed"}),
                    encoding="utf-8",
                )
                workers.append({"worker_index": index, "worker_dir": str(worker)})
            self.assertEqual(
                _selected_indices(root, {"workers": workers}, "audit_passed"),
                {0},
            )


if __name__ == "__main__":
    unittest.main()
