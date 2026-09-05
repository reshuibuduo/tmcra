from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from tmcra_local_models import profile_by_id, qwen_rerank_windows, signature, verify_index_identity
from tmcra_local_only import ROLES, configure_routes, loopback_url, validate_environment

ROOT = Path(__file__).resolve().parents[2]


def local_env():
    result = configure_routes({"TMCRA_SERVICE_BIND_HOST": "127.0.0.1", "TMCRA_SERVICE_BIND_PORT": "2009",
                               "TMCRA_SERVICE_PUBLIC_BASE_URL": "http://127.0.0.1:2009",
                               "DEEPSEEK_API_KEY": "test-cloud-secret", "HTTPS_PROXY": "http://192.0.2.1:9"},
                              base_url="http://127.0.0.1:2010/v1", model="tmcra-qwen3-4b-q4km", key="local-test-only")
    return result


class LocalBoundaryTests(unittest.TestCase):
    def test_prepare_discovers_ports_and_reuses_identity_without_account(self):
        from tmcra_service.local_deployment import prepare
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            server = root / "synthetic-server.exe"
            server.write_bytes(b"synthetic installer test; never executed")
            with patch("tmcra_service.local_deployment.hardware", return_value={"ram_gib": 64, "cuda_available": False}), \
                 patch("tmcra_service.local_deployment.download_model", side_effect=lambda model, path: path.mkdir(parents=True, exist_ok=True)), \
                 patch("tmcra_service.local_deployment.install_llama", return_value=server):
                first = prepare(root, "lite-cpu", auto_ports=True)
                identity = (root / "state/lite-cpu/secrets/client-plugin.json").read_text()
                second = prepare(root, "lite-cpu", auto_ports=True)
            self.assertNotEqual(first["api_port"], first["model_port"])
            self.assertEqual(first["api_port"], second["api_port"])
            self.assertEqual(identity, (root / "state/lite-cpu/secrets/client-plugin.json").read_text())
            self.assertEqual(first["api_root"], str(ROOT))

    def test_all_routes_local_cloud_keys_removed(self):
        env = local_env()
        validate_environment(env)
        self.assertNotIn("DEEPSEEK_API_KEY", env)
        self.assertNotIn("HTTPS_PROXY", env)
        for role in ROLES:
            self.assertEqual(env[f"TMCRA_{role}_BASE_URL"], "http://127.0.0.1:2010/v1")

    def test_each_hidden_route_rejects_cloud(self):
        for role in ROLES:
            with self.subTest(role=role):
                env = local_env()
                env[f"TMCRA_{role}_BASE_URL"] = "https://api.example.invalid/v1"
                with self.assertRaises(ValueError):
                    validate_environment(env)

    def test_loopback_url_rejects_ambiguous_hosts(self):
        for value in ["http://localhost:2009", "http://127.1:2009", "http://2130706433:2009",
                      "http://127.0.0.1.example.com:2009", "http://user@127.0.0.1:2009",
                      "http://0.0.0.0:2009", "http://[::ffff:192.0.2.1]:2009", "http://127.0.0.1:2009?x=1"]:
            with self.subTest(url=value), self.assertRaises(ValueError):
                loopback_url(value)

    def test_guard_blocks_tcp_dns_udp_and_wildcard(self):
        code = '''
import socket
from tmcra_local_only import install_network_guard
install_network_guard()
checks = [lambda: socket.getaddrinfo("example.invalid", 443),
          lambda: socket.socket().connect(("192.0.2.1", 9)),
          lambda: socket.socket(type=socket.SOCK_DGRAM).sendto(b"synthetic", ("192.0.2.1", 9)),
          lambda: socket.socket().bind(("0.0.0.0", 0))]
for check in checks:
    try: check()
    except PermissionError: pass
    else: raise AssertionError("external network operation permitted")
with socket.socket() as server: server.bind(("127.0.0.1", 0))
print("guard_ok")
'''
        result = subprocess.run([sys.executable, "-c", code], cwd=ROOT, capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("guard_ok", result.stdout)

    def test_child_bootstrap_fails_closed(self):
        env = dict(os.environ)
        env.update(local_env())
        env["PYTHONPATH"] = os.pathsep.join([str(ROOT / "deploy" / "local-bootstrap"), str(ROOT)])
        env["TMCRA_WRITER_BASE_URL"] = "https://example.invalid/v1"
        result = subprocess.run([sys.executable, "-c", "print('should_not_run')"], env=env,
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 78)
        self.assertNotIn("should_not_run", result.stdout)

    def test_local_identity_reused_without_account_server(self):
        from tmcra_service.local_deployment import initialize_identity
        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory) / "state"
            with patch("tmcra_service.local_deployment.private_directory", side_effect=lambda p: p.mkdir(parents=True, exist_ok=True)):
                key = initialize_identity(state)
                credential = (state / "secrets" / "client.json").read_text()
                self.assertEqual(initialize_identity(state), key)
                self.assertEqual((state / "secrets" / "client.json").read_text(), credential)


class ModelContractTests(unittest.TestCase):
    def test_token_windows_cover_all_source_characters(self):
        from tmp_tmcra_v2_lme_pipeline import HuggingFaceDenseVectorizer
        class CharacterTokenizer:
            def encode(self, text, **_):
                return list(range(len(text) + 2))
        vectorizer = HuggingFaceDenseVectorizer.__new__(HuggingFaceDenseVectorizer)
        vectorizer.tokenizer = CharacterTokenizer()
        vectorizer.document_prefix = "passage: "
        vectorizer.max_length = 64
        text = "中文来源🙂mixed long source " * 31
        prefix = "message=001 role=user\n"
        spans = vectorizer.source_spans(text, prefix=prefix, max_chars=1800, overlap_chars=200)
        covered = set()
        self.assertGreater(len(spans), 1)
        for start, end in spans:
            self.assertLessEqual(len(vectorizer.tokenizer.encode(vectorizer.document_prefix + prefix + text[start:end])), 64)
            covered.update(range(start, end))
        self.assertEqual(covered, set(range(len(text))))
        self.assertTrue(all(b[0] > a[0] for a, b in zip(spans, spans[1:])))

    def test_embedding_index_identity(self):
        profile = profile_by_id("lite-cpu")
        args = argparse.Namespace(text_dim=384, embedding_index_signature=signature(profile))
        verify_index_identity({"text_dim": 384, "embedding_index_signature": signature(profile)}, args)
        for payload in [{"text_dim": 1024}, {"text_dim": 384, "embedding_index_signature": "older"}]:
            with self.assertRaises(RuntimeError):
                verify_index_identity(payload, args)

    def test_profile_signatures_unique_and_change_on_pooling(self):
        first = profile_by_id("lite-cpu")
        before = signature(first)
        first["embedding"]["pooling"] = "cls"
        self.assertNotEqual(signature(first), before)
        self.assertEqual(len({signature(profile_by_id(p)) for p in ["lite-cpu", "balanced-bge", "quality-qwen"]}), 3)

    def test_qwen_format_and_document_coverage(self):
        class CharacterTokenizer:
            def encode(self, text, **_):
                return list(map(ord, text))
        document = "记忆证据" * 500
        windows = qwen_rerank_windows(CharacterTokenizer(), "synthetic question", document, max_length=512)
        self.assertGreater(len(windows), 1)
        self.assertTrue(all(len(w) <= 512 for w in windows))
        self.assertTrue(all("<|im_start|>system" in "".join(map(chr, w)) for w in windows))
        with self.assertRaises(ValueError):
            qwen_rerank_windows(CharacterTokenizer(), "Q" * 500, document, max_length=512)


if __name__ == "__main__":
    unittest.main()
