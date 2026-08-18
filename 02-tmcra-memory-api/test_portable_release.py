from __future__ import annotations

import os
import subprocess
import sys
import tarfile
import tempfile
import unittest
from pathlib import Path

from ops.build_tmcra_service_release import build_release


ROOT = Path(__file__).resolve().parent


class PortableReleaseTests(unittest.TestCase):
    def test_runtime_dependency_closure_is_in_the_service_archive(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            work = Path(directory)
            archive = work / "service.tar.gz"
            extract = work / "extract"
            build_release(ROOT, archive)
            with tarfile.open(archive, "r:gz") as handle:
                names = set(handle.getnames())
                handle.extractall(extract)

            self.assertIn("experiments/replacement/adapters/memory_adapters.py", names)
            self.assertIn("experiments/replacement/memory_graph.py", names)
            self.assertIn("core/session_memory.py", names)
            self.assertIn("models/tmcra_v3_reranker.pt", names)

            environment = dict(os.environ)
            environment["PYTHONDONTWRITEBYTECODE"] = "1"
            environment["PYTHONPATH"] = str(extract)
            result = subprocess.run(
                [
                    sys.executable,
                    "-c",
                    "from experiments.replacement.adapters.memory_adapters "
                    "import GraphSessionMemoryAdapter; "
                    "from core.session_memory import SessionMemoryExtractor; "
                    "print('portable-import-ok')",
                ],
                cwd=extract,
                env=environment,
                text=True,
                capture_output=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("portable-import-ok", result.stdout)


if __name__ == "__main__":
    unittest.main()
