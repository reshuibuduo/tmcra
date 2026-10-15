"""Real loopback sockets; no external endpoint, credential, or model is used."""
from __future__ import annotations

import json
import threading
import unittest
import urllib.request
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch
from types import SimpleNamespace

from tmcra_service.client_cli import ClientCLIError, ClientConfig, HTTPClient
from tmcra_service.http_transport import is_loopback_url, open_request


@contextmanager
def server(*, status=200, location=None, payload=None):
    seen = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            length = int(self.headers.get("Content-Length", "0"))
            seen.append((self.command, self.path, self.rfile.read(length)))
            self.send_response(status)
            if location is not None:
                self.send_header("Location", location)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(payload or {"ok": True}).encode())

        do_POST = do_GET

        def log_message(self, *_):
            pass

    instance = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=instance.serve_forever, kwargs={"poll_interval": 0.01})
    thread.start()
    try:
        yield f"http://127.0.0.1:{instance.server_port}", seen
    finally:
        instance.shutdown()
        instance.server_close()
        thread.join(timeout=5)


class LoopbackTransportTests(unittest.TestCase):
    def client(self, url):
        return HTTPClient(ClientConfig(url, "synthetic-test-token", timeout_seconds=2))

    def test_loopback_get_and_post_bypass_system_proxy(self):
        with server(payload={"proxy": True}) as (proxy, proxy_seen), server() as (url, seen):
            with patch.object(urllib.request, "_opener", None), \
                 patch("urllib.request.getproxies", return_value={"http": proxy}), \
                 patch("urllib.request.proxy_bypass", return_value=False):
                for method, body in [("GET", None), ("POST", {"content": "synthetic evidence"})]:
                    with self.subTest(method=method):
                        self.assertEqual(self.client(url).request(method, "/probe", body=body)[2], {"ok": True})
            self.assertEqual(proxy_seen, [])
            self.assertEqual([row[0] for row in seen], ["GET", "POST"])
            self.assertEqual(json.loads(seen[-1][2]), {"content": "synthetic evidence"})

    def test_loopback_redirects_never_contact_destination(self):
        with server() as (target, target_seen):
            for status in [301, 302, 303, 307, 308]:
                with self.subTest(status=status), server(status=status, location=target) as (url, _):
                    with patch.object(urllib.request, "_opener", None), \
                         patch("urllib.request.getproxies", return_value={}):
                        with self.assertRaises(ClientCLIError) as raised:
                            self.client(url).request("GET", "/redirect")
                        self.assertEqual(raised.exception.status_code, status)
            self.assertEqual(target_seen, [])

    def test_local_model_preflight_bypasses_proxy(self):
        from tmcra_service.startup import StartupPreflight

        with server(payload={"n_ctx": 131072}) as (url, seen), server() as (proxy, proxy_seen):
            with patch.object(urllib.request, "_opener", None), \
                 patch("urllib.request.getproxies", return_value={"http": proxy}), \
                 patch("urllib.request.proxy_bypass", return_value=False):
                result = StartupPreflight._local_model_context_check(SimpleNamespace(
                    base_url=url + "/v1", api_keys=["synthetic-test-token"], model="synthetic-model"
                ))
            self.assertEqual(result["n_ctx"], 131072)
            self.assertEqual(seen[0][1], "/props")
            self.assertEqual(proxy_seen, [])

    def test_url_classification_never_resolves_remote_hosts(self):
        for url in ["http://127.0.0.1:2009", "http://127.0.0.2", "http://[::1]", "https://localhost"]:
            with self.subTest(url=url):
                self.assertTrue(is_loopback_url(url))
        for url in ["https://example.invalid", "http://127.0.0.1.example.invalid", "http://localhost.example.invalid",
                    "http://127.1", "http://2130706433", "file:///tmp/sample", "http://192.0.2.1"]:
            with self.subTest(url=url):
                self.assertFalse(is_loopback_url(url))

    def test_remote_provider_keeps_existing_transport(self):
        request = urllib.request.Request("https://provider.example.invalid/v1")
        with patch("urllib.request.urlopen") as remote:
            self.assertIs(open_request(request, timeout=3), remote.return_value)
            remote.assert_called_once_with(request, timeout=3)


if __name__ == "__main__":
    unittest.main()
