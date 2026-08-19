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
            self.assertIn("build_v3_runtime_dataset.py", names)
            self.assertIn("tmcra_v3_reranker.py", names)
            self.assertIn("tmcra_v3_schema.py", names)
            self.assertIn("models/tmcra_v3_reranker.pt", names)
            self.assertIn("deploy/tmcra-local-llm-control.sh", names)
            self.assertIn("deploy/tmcra-production-maintenance.sh", names)
            self.assertIn("deploy/writer.env.example", names)

            writer_template = (extract / "deploy" / "writer.env.example").read_bytes()
            self.assertNotIn(b"\r\n", writer_template)
            self.assertIn(b"TMCRA_WRITER_PROVIDER=local-qwen\n", writer_template)

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
