from __future__ import annotations

import http.client
import importlib.util
import json
import pathlib
import re
import sqlite3
import tempfile
import threading
import unittest
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


PROXY_PATH = (
    pathlib.Path(__file__).resolve().parents[1] / "deploy" / "gpuhome" / "proxy.py"
)
SPEC = importlib.util.spec_from_file_location("tmcra_gpuhome_proxy", PROXY_PATH)
assert SPEC and SPEC.loader
proxy = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(proxy)


class RecordingUpstream(BaseHTTPRequestHandler):
    requests: list[tuple[str, str, dict[str, str], bytes]] = []
    status_by_path: dict[str, int] = {}

    def do_GET(self) -> None:  # noqa: N802
        self._record()

    def do_HEAD(self) -> None:  # noqa: N802
        self._record()

    def do_POST(self) -> None:  # noqa: N802
        self._record()

    def _record(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length) if length else b""
        self.__class__.requests.append(
            (
                self.command,
                self.path,
                {key.lower(): value for key, value in self.headers.items()},
                body,
            )
        )
        payload = b"" if self.command == "HEAD" else b"ok"
        self.send_response(self.__class__.status_by_path.get(self.path, 200))
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        if payload:
            self.wfile.write(payload)

    def log_message(self, _format: str, *_args: object) -> None:
        return


class QuietProxyHandler(proxy.TmcraProxyHandler):
    def log_message(self, _format: str, *_args: object) -> None:
        return


class GatewaySecurityTests(unittest.TestCase):
    DESKTOP_HEADERS = {
        proxy.DESKTOP_CLIENT_HEADER: "com.tmcra.memory/0.1.8",
    }

    @classmethod
    def setUpClass(cls) -> None:
        cls.temp_directory = tempfile.TemporaryDirectory()
        proxy.ACCOUNT_DATABASE = str(
            pathlib.Path(cls.temp_directory.name) / "account-store.sqlite3"
        )
        proxy.SESSION_SECRET = "test-only-session-secret-that-is-long-enough"
        proxy.PASSWORD_ITERATIONS = 10_000
        proxy.SESSION_IDLE_SECONDS = 3_600
        proxy.SESSION_ABSOLUTE_SECONDS = 7_200
        proxy.SESSION_ROTATE_SECONDS = 3_600
        proxy._READY_DATABASE_PATH = None
        proxy._ensure_schema()

        cls.download_payload = bytes(range(256)) * 513
        cls.download_root = pathlib.Path(cls.temp_directory.name) / "downloads"
        cls.download_root.mkdir()
        (cls.download_root / proxy.DOWNLOAD_FILENAME).write_bytes(cls.download_payload)
        cls.update_metadata_payload = (
            b"version: 0.1.1\npath: TMCRA-Memory-Setup-0.1.1-x64.exe\n"
        )
        cls.update_installer_name = "TMCRA-Memory-Setup-0.1.1-x64.exe"
        cls.update_blockmap_name = f"{cls.update_installer_name}.blockmap"
        cls.update_root = cls.download_root / "desktop" / "windows" / "x64"
        cls.update_root.mkdir(parents=True)
        (cls.update_root / proxy.DESKTOP_UPDATE_METADATA_FILENAME).write_bytes(
            cls.update_metadata_payload
        )
        (cls.update_root / cls.update_installer_name).write_bytes(cls.download_payload)
        (cls.update_root / cls.update_blockmap_name).write_bytes(b"blockmap")
        cls.mac_update_payload = b"version: 0.2.8\n"
        cls.mac_artifacts: dict[str, dict[str, str]] = {}
        for architecture in ("x64", "arm64"):
            latest_name = f"TMCRA-Memory-latest-{architecture}.dmg"
            (cls.download_root / latest_name).write_bytes(cls.download_payload)
            update_root = cls.download_root / "desktop" / "macos" / architecture
            update_root.mkdir(parents=True)
            dmg_name = f"TMCRA-Memory-0.2.8-{architecture}.dmg"
            zip_name = f"TMCRA-Memory-0.2.8-{architecture}.zip"
            blockmap_name = f"{zip_name}.blockmap"
            (update_root / "latest-mac.yml").write_bytes(cls.mac_update_payload)
            (update_root / dmg_name).write_bytes(cls.download_payload)
            (update_root / zip_name).write_bytes(cls.download_payload)
            (update_root / blockmap_name).write_bytes(b"mac-blockmap")
            cls.mac_artifacts[architecture] = {
                "latest": latest_name,
                "dmg": dmg_name,
                "zip": zip_name,
                "blockmap": blockmap_name,
            }
        proxy.DOWNLOAD_ROOT = str(cls.download_root)
        proxy.RELEASE_ID = "test-release"
        proxy.MAIL_TRANSPORT = "smtp"
        proxy.MAIL_FROM_EMAIL = "no-reply@tmcra.com"
        proxy.MARKETING_ENABLED = True
        proxy.MARKETING_MAIL_TRANSPORT = "smtp"
        proxy.MARKETING_API_TOKEN = "test-marketing-token-that-is-at-least-thirty-two-bytes"
        proxy.MARKETING_MAX_ATTEMPTS = 3
        proxy.GOOGLE_CLIENT_ID = "test-google-client"
        proxy.GOOGLE_CLIENT_SECRET = "test-google-secret"
        proxy.GOOGLE_REDIRECT_URI = "https://tmcra.com/oauth/google/callback"
        cls.original_google_oauth_release_enabled = (
            proxy.GOOGLE_OAUTH_RELEASE_ENABLED
        )
        proxy.GOOGLE_OAUTH_RELEASE_ENABLED = True
        cls.sent_emails: list[tuple[str, str, str, str]] = []
        cls.sent_marketing_emails: list[tuple[str, str, str, str, str]] = []
        cls.original_email_sender = proxy._send_transactional_email
        cls.original_marketing_sender = proxy._send_marketing_email

        def record_email(recipient: str, subject: str, text_body: str, html_body: str) -> None:
            cls.sent_emails.append((recipient, subject, text_body, html_body))

        proxy._send_transactional_email = record_email

        def record_marketing_email(
            recipient: str,
            subject: str,
            text_body: str,
            html_body: str,
            unsubscribe_url: str,
        ) -> None:
            cls.sent_marketing_emails.append(
                (recipient, subject, text_body, html_body, unsubscribe_url)
            )

        proxy._send_marketing_email = record_marketing_email

        cls.upstream = ThreadingHTTPServer(("127.0.0.1", 0), RecordingUpstream)
        proxy.UPSTREAM_HOST = "127.0.0.1"
        proxy.UPSTREAM_PORT = cls.upstream.server_address[1]
        proxy.INTERNAL_ALLOWED_NETWORKS = proxy._network_allowlist(
            "203.0.113.10/32", "test internal allowlist"
        )
        proxy.TRUSTED_PROXY_NETWORKS = proxy._network_allowlist(
            "127.0.0.1/32,::1/128", "test trusted proxies"
        )
        proxy.PUBLIC_HOSTS = frozenset(
            {
                "tmcra.com",
                "euvbyqa1jpvdm7yq-2000.sc01-webservice.gpuhome.cc:8443",
            }
        )
        cls.gateway = ThreadingHTTPServer(("127.0.0.1", 0), QuietProxyHandler)
        cls.threads = [
            threading.Thread(target=cls.upstream.serve_forever, daemon=True),
            threading.Thread(target=cls.gateway.serve_forever, daemon=True),
        ]
        for thread in cls.threads:
            thread.start()

    @classmethod
    def tearDownClass(cls) -> None:
        proxy._send_transactional_email = cls.original_email_sender
        proxy._send_marketing_email = cls.original_marketing_sender
        proxy.GOOGLE_OAUTH_RELEASE_ENABLED = (
            cls.original_google_oauth_release_enabled
        )
        cls.gateway.shutdown()
        cls.upstream.shutdown()
        cls.gateway.server_close()
        cls.upstream.server_close()
        cls.temp_directory.cleanup()

    def setUp(self) -> None:
        RecordingUpstream.requests.clear()
        RecordingUpstream.status_by_path.clear()
        self.sent_emails.clear()
        self.sent_marketing_emails.clear()
        proxy._reset_internal_gateway_audit_rate_limit()
        with proxy._database() as database:
            database.execute("DELETE FROM internal_gateway_audit")
            database.execute("DELETE FROM email_campaign_deliveries")
            database.execute("DELETE FROM email_campaigns")
            database.execute("DELETE FROM account_oauth_states")
            database.execute("DELETE FROM account_rate_limits")
            database.execute("DELETE FROM account_sessions")
            database.execute("DELETE FROM account_users")

    def request(
        self,
        method: str,
        path: str,
        *,
        headers: dict[str, str] | None = None,
        body: bytes | None = None,
    ):
        connection = http.client.HTTPConnection(
            "127.0.0.1", self.gateway.server_address[1], timeout=5
        )
        connection.request(
            method,
            path,
            body=body,
            headers={"Host": "tmcra.com", **(headers or {})},
        )
        response = connection.getresponse()
        payload = response.read()
        result = response.status, response.getheaders(), payload
        connection.close()
        return result

    @staticmethod
    def headers_map(headers: list[tuple[str, str]]) -> dict[str, str]:
        return {name: value for name, value in headers}

    def assert_hsts(self, headers: list[tuple[str, str]]) -> None:
        self.assertEqual(
            self.headers_map(headers).get("Strict-Transport-Security"),
            "max-age=31536000; includeSubDomains",
        )

    @staticmethod
    def update_cookies(jar: dict[str, str], headers: list[tuple[str, str]]) -> None:
        for name, value in headers:
            if name.lower() != "set-cookie":
                continue
            pair = value.split(";", 1)[0]
            cookie_name, cookie_value = pair.split("=", 1)
            if cookie_value:
                jar[cookie_name] = cookie_value
            else:
                jar.pop(cookie_name, None)

    @staticmethod
    def cookie_header(jar: dict[str, str]) -> str:
        return "; ".join(f"{name}={value}" for name, value in jar.items())

    def auth_page(
        self,
        jar: dict[str, str],
        return_to: str = "/personal",
        host: str = "tmcra.com",
    ) -> str:
        status, headers, payload = self.request(
            "GET",
            "/login?return_to="
            + urllib.parse.quote(return_to, safe=""),
            headers={"Cookie": self.cookie_header(jar), "Host": host},
        )
        self.assertEqual(status, 200)
        self.assert_hsts(headers)
        self.update_cookies(jar, headers)
        match = re.search(rb'name="csrf" value="([^"]+)"', payload)
        self.assertIsNotNone(match)
        return match.group(1).decode("ascii")

    def submit_auth(
        self,
        jar: dict[str, str],
        *,
        action: str,
        email: str,
        password: str,
        full_name: str = "",
        return_to: str = "/personal",
        csrf: str | None = None,
        origin: str = "https://tmcra.com",
        fetch_site: str = "same-origin",
        host: str = "tmcra.com",
    ):
        csrf = csrf or self.auth_page(jar, return_to, host)
        body = urllib.parse.urlencode(
            {
                "action": action,
                "csrf": csrf,
                "return_to": return_to,
                "email": email,
                "password": password,
                "full_name": full_name,
            }
        ).encode("utf-8")
        result = self.request(
            "POST",
            "/register" if action == "register" else "/login",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Host": host,
                "Cookie": self.cookie_header(jar),
                "Origin": origin,
                "Sec-Fetch-Site": fetch_site,
            },
            body=body,
        )
        self.update_cookies(jar, result[1])
        return result

    def register(
        self,
        jar: dict[str, str],
        email: str = "alice@example.com",
        name: str = "Alice Example",
        password: str = "Correct-Horse-42!",
        return_to: str = "/personal",
    ):
        status, headers, payload = self.submit_auth(
            jar,
            action="register",
            email=email,
            password=password,
            full_name=name,
            return_to=return_to,
        )
        self.assertEqual(status, 200)
        self.assert_hsts(headers)
        self.assertNotIn(proxy.SESSION_COOKIE, jar)
        self.assertEqual(self.sent_emails[-1][0], proxy.TmcraProxyHandler._normalize_email(email))
        code = re.search(r"verification code is ([0-9]{6})", self.sent_emails[-1][2])
        self.assertIsNotNone(code)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', payload)
        self.assertIsNotNone(csrf)
        body = urllib.parse.urlencode(
            {
                "csrf": csrf.group(1).decode("ascii"),
                "return_to": return_to,
                "email": email,
                "code": code.group(1),
            }
        ).encode("utf-8")
        status, headers, payload = self.request(
            "POST",
            "/verify-email",
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Cookie": self.cookie_header(jar),
                "Origin": "https://tmcra.com",
                "Sec-Fetch-Site": "same-origin",
            },
            body=body,
        )
        self.update_cookies(jar, headers)
        self.assertEqual((status, payload), (303, b""))
        self.assertEqual(self.headers_map(headers)["Location"], return_to)
        return headers

    def post_form(
        self,
        jar: dict[str, str],
        path: str,
        values: dict[str, str],
        *,
        origin: str = "https://tmcra.com",
        host: str = "tmcra.com",
    ):
        body = urllib.parse.urlencode(values).encode("utf-8")
        result = self.request(
            "POST",
            path,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
                "Cookie": self.cookie_header(jar),
                "Origin": origin,
                "Sec-Fetch-Site": "same-origin",
                "Host": host,
            },
            body=body,
        )
        self.update_cookies(jar, result[1])
        return result

    def request_json(
        self,
        method: str,
        path: str,
        value: dict[str, object] | None = None,
        *,
        headers: dict[str, str] | None = None,
        jar: dict[str, str] | None = None,
        origin: str | None = None,
    ):
        body = json.dumps(value, separators=(",", ":")).encode("utf-8") if value else None
        request_headers = {
            "Accept": "application/json",
            **({"Content-Type": "application/json", "Content-Length": str(len(body))} if body else {}),
            **(headers or {}),
        }
        if jar:
            request_headers["Cookie"] = self.cookie_header(jar)
        if origin:
            request_headers["Origin"] = origin
            request_headers["Sec-Fetch-Site"] = "same-origin"
        result = self.request(method, path, headers=request_headers, body=body)
        if jar is not None:
            self.update_cookies(jar, result[1])
        return result

    def request_desktop_json(
        self,
        method: str,
        path: str,
        value: dict[str, object] | None = None,
        *,
        jar: dict[str, str] | None = None,
        headers: dict[str, str] | None = None,
        origin: str | None = None,
    ):
        return self.request_json(
            method,
            path,
            value,
            headers={**self.DESKTOP_HEADERS, **(headers or {})},
            jar=jar,
            origin=origin,
        )

    @staticmethod
    def csrf_from(payload: bytes) -> str:
        match = re.search(rb'name="csrf" value="([^"]+)"', payload)
        assert match
        return match.group(1).decode("ascii")

    @staticmethod
    def code_from_email(message: tuple[str, str, str, str]) -> str:
        match = re.search(r"(?:verification|reset) code is ([0-9]{6})", message[2])
        assert match
        return match.group(1)

    def test_registration_requires_a_valid_single_use_email_code(self) -> None:
        jar: dict[str, str] = {}
        status, headers, payload = self.submit_auth(
            jar,
            action="register",
            email="pending@example.com",
            password="Another-Strong-42!",
            full_name="Pending User",
        )
        self.assertEqual(status, 200)
        self.assertNotIn(proxy.SESSION_COOKIE, jar)
        code = self.code_from_email(self.sent_emails[-1])
        csrf = self.csrf_from(payload)
        with proxy._database() as database:
            stored_code_hash = database.execute(
                "SELECT token_hash FROM account_email_tokens WHERE purpose = 'verify_email'"
            ).fetchone()[0]
        self.assertNotEqual(stored_code_hash, code)
        self.assertNotIn(code, stored_code_hash)

        status, _headers, _payload = self.post_form(
            jar,
            "/verify-email",
            {
                "csrf": csrf,
                "return_to": "/personal",
                "email": "pending@example.com",
                "code": "000000" if code != "000000" else "999999",
            },
        )
        self.assertEqual(status, 400)
        self.assertNotIn(proxy.SESSION_COOKIE, jar)

        status, headers, payload = self.request(
            "GET",
            "/verify-email?email=pending%40example.com&return_to=%2Fpersonal",
            headers={"Cookie": self.cookie_header(jar)},
        )
        self.update_cookies(jar, headers)
        self.assertEqual(status, 200)
        status, headers, _payload = self.post_form(
            jar,
            "/verify-email",
            {
                "csrf": self.csrf_from(payload),
                "return_to": "/personal",
                "email": "pending@example.com",
                "code": code,
            },
        )
        self.assertEqual(status, 303)
        self.assertIn(proxy.SESSION_COOKIE, jar)
        with proxy._database() as database:
            verified_at = database.execute(
                "SELECT email_verified_at FROM account_users WHERE email = ?",
                ("pending@example.com",),
            ).fetchone()[0]
        self.assertIsNotNone(verified_at)

        replay_jar: dict[str, str] = {}
        status, headers, payload = self.request(
            "GET", "/verify-email?email=pending%40example.com", headers={}
        )
        self.update_cookies(replay_jar, headers)
        status, _headers, _payload = self.post_form(
            replay_jar,
            "/verify-email",
            {
                "csrf": self.csrf_from(payload),
                "return_to": "/personal",
                "email": "pending@example.com",
                "code": code,
            },
        )
        self.assertEqual(status, 400)
        self.assertNotIn(proxy.SESSION_COOKIE, replay_jar)

    def test_account_code_emails_use_the_branded_bilingual_template(self) -> None:
        proxy.TmcraProxyHandler._send_verification_email(
            None, "alice@example.com", "Alice & Bob", "123456"
        )
        recipient, subject, text_body, html_body = self.sent_emails[-1]
        self.assertEqual(recipient, "alice@example.com")
        self.assertIn("TMCRA 邮箱验证码", subject)
        self.assertIn("Your TMCRA verification code is 123456", text_body)
        self.assertIn("TMCRA 邮箱验证码：123456", text_body)
        self.assertIn('src="https://tmcra.com/brand/tmcra-logo.png"', html_body)
        self.assertIn('alt="TMCRA"', html_body)
        self.assertIn("Verify your email", html_body)
        self.assertIn("验证您的邮箱", html_body)
        self.assertIn("Alice &amp; Bob", html_body)
        self.assertIn(">123456</td>", html_body)
        self.assertNotIn("<script", html_body.lower())

        proxy.TmcraProxyHandler._send_password_reset_email(
            None, "alice@example.com", "Alice", "654321"
        )
        _recipient, reset_subject, reset_text, reset_html = self.sent_emails[-1]
        self.assertIn("TMCRA 密码重置码", reset_subject)
        self.assertIn("Your TMCRA password reset code is 654321", reset_text)
        self.assertIn("Reset your password", reset_html)
        self.assertIn("重置您的密码", reset_html)
        self.assertIn(">654321</td>", reset_html)

    def test_password_reset_code_revokes_existing_sessions(self) -> None:
        old_jar: dict[str, str] = {}
        self.register(old_jar, email="reset@example.com", password="Original-Strong-42!")
        reset_jar: dict[str, str] = {}
        status, headers, payload = self.request("GET", "/forgot-password")
        self.update_cookies(reset_jar, headers)
        self.assertEqual(status, 200)
        status, _headers, payload = self.post_form(
            reset_jar,
            "/forgot-password",
            {
                "csrf": self.csrf_from(payload),
                "return_to": "/personal",
                "email": "reset@example.com",
            },
        )
        self.assertEqual(status, 200)
        code = self.code_from_email(self.sent_emails[-1])
        status, headers, payload = self.request(
            "GET",
            "/reset-password?email=reset%40example.com",
            headers={"Cookie": self.cookie_header(reset_jar)},
        )
        self.update_cookies(reset_jar, headers)
        status, _headers, _payload = self.post_form(
            reset_jar,
            "/reset-password",
            {
                "csrf": self.csrf_from(payload),
                "return_to": "/personal",
                "email": "reset@example.com",
                "code": code,
                "password": "Replacement-Strong-73!",
            },
        )
        self.assertEqual(status, 303)
        self.assertIn(proxy.SESSION_COOKIE, reset_jar)

        status, headers, _payload = self.request(
            "GET", "/personal", headers={"Cookie": self.cookie_header(old_jar)}
        )
        self.assertEqual(status, 303)
        self.assertTrue(self.headers_map(headers)["Location"].startswith("/login"))

    def test_google_oidc_state_is_single_use_and_creates_verified_account(self) -> None:
        jar: dict[str, str] = {}
        status, headers, payload = self.request(
            "GET", "/oauth/google/start?return_to=%2Fpersonal"
        )
        self.assertEqual((status, payload), (303, b""))
        self.update_cookies(jar, headers)
        authorization = urllib.parse.urlsplit(self.headers_map(headers)["Location"])
        parameters = urllib.parse.parse_qs(authorization.query)
        self.assertEqual(parameters["code_challenge_method"], ["S256"])
        self.assertEqual(parameters["scope"], ["openid email profile"])
        state = parameters["state"][0]

        original_exchange = proxy._google_identity_from_code
        proxy._google_identity_from_code = lambda code, verifier: {
            "subject": "google-subject-1",
            "email": "google-user@example.com",
            "full_name": "Google User",
        }
        try:
            status, headers, payload = self.request(
                "GET",
                "/oauth/google/callback?"
                + urllib.parse.urlencode({"state": state, "code": "authorization-code"}),
                headers={"Cookie": self.cookie_header(jar)},
            )
            self.update_cookies(jar, headers)
        finally:
            proxy._google_identity_from_code = original_exchange
        self.assertEqual((status, payload), (303, b""))
        self.assertIn(proxy.SESSION_COOKIE, jar)
        with proxy._database() as database:
            user = database.execute(
                """SELECT u.email, u.email_verified_at, u.password_enabled
                   FROM account_users u
                   JOIN account_oauth_identities i ON i.user_id = u.id
                   WHERE i.provider = 'google' AND i.provider_subject = ?""",
                ("google-subject-1",),
            ).fetchone()
        self.assertEqual(user["email"], "google-user@example.com")
        self.assertIsNotNone(user["email_verified_at"])
        self.assertEqual(user["password_enabled"], 0)

        original_exchange = proxy._google_identity_from_code
        proxy._google_identity_from_code = lambda code, verifier: {
            "subject": "google-subject-1",
            "email": "google-user@example.com",
            "full_name": "Google User",
        }
        try:
            status, _headers, _payload = self.request(
                "GET",
                "/oauth/google/callback?"
                + urllib.parse.urlencode({"state": state, "code": "replayed-code"}),
                headers={"Cookie": self.cookie_header(jar)},
            )
        finally:
            proxy._google_identity_from_code = original_exchange
        self.assertEqual(status, 400)

    def test_google_oauth_release_gate_stays_closed_with_credentials_present(
        self,
    ) -> None:
        original = proxy.GOOGLE_OAUTH_RELEASE_ENABLED
        proxy.GOOGLE_OAUTH_RELEASE_ENABLED = False
        try:
            status, _headers, payload = self.request(
                "GET", "/oauth/google/start?return_to=%2Fpersonal"
            )
            self.assertEqual(status, 404)
            self.assertEqual(
                json.loads(payload)["error"]["code"],
                "google_login_not_configured",
            )

            status, _headers, payload = self.request("GET", "/login")
            self.assertEqual(status, 200)
            self.assertNotIn(b"Continue with Google", payload)

            status, _headers, payload = self.request(
                "GET", "/__deployment/health"
            )
            self.assertEqual(status, 200)
            self.assertFalse(json.loads(payload)["googleLogin"])
        finally:
            proxy.GOOGLE_OAUTH_RELEASE_ENABLED = original

    def test_installer_get_and_head_are_served_directly(self) -> None:
        status, headers, payload = self.request("GET", proxy.DOWNLOAD_REQUEST_PATH)
        mapped = self.headers_map(headers)
        self.assertEqual((status, payload), (200, self.download_payload))
        self.assertEqual(mapped["Content-Type"], proxy.DOWNLOAD_CONTENT_TYPE)
        self.assertEqual(
            mapped["Content-Disposition"],
            f'attachment; filename="{proxy.DOWNLOAD_FILENAME}"',
        )
        self.assertEqual(mapped["Content-Length"], str(len(self.download_payload)))
        self.assertEqual(mapped["Accept-Ranges"], "bytes")
        self.assertRegex(mapped["ETag"], r'^"sha256-[0-9a-f]{64}"$')
        self.assertEqual(mapped["Cache-Control"], proxy.DOWNLOAD_CACHE_CONTROL)
        self.assertEqual(
            [value for name, value in headers if name.lower() == "cache-control"],
            [proxy.DOWNLOAD_CACHE_CONTROL],
        )
        self.assertEqual(RecordingUpstream.requests, [])
        self.assert_hsts(headers)

        status, headers, payload = self.request("HEAD", proxy.DOWNLOAD_REQUEST_PATH)
        mapped = self.headers_map(headers)
        self.assertEqual((status, payload), (200, b""))
        self.assertEqual(mapped["Content-Length"], str(len(self.download_payload)))
        self.assertEqual(mapped["Accept-Ranges"], "bytes")
        self.assertRegex(mapped["ETag"], r'^"sha256-[0-9a-f]{64}"$')
        self.assertEqual(RecordingUpstream.requests, [])

    def test_desktop_update_feed_is_allowlisted_and_served_directly(self) -> None:
        metadata_path = (
            proxy.DESKTOP_UPDATE_REQUEST_PREFIX
            + proxy.DESKTOP_UPDATE_METADATA_FILENAME
        )
        status, headers, payload = self.request("GET", metadata_path)
        mapped = self.headers_map(headers)
        self.assertEqual((status, payload), (200, self.update_metadata_payload))
        self.assertEqual(
            mapped["Content-Type"], proxy.DESKTOP_UPDATE_METADATA_CONTENT_TYPE
        )
        self.assertEqual(mapped["Cache-Control"], proxy.DOWNLOAD_CACHE_CONTROL)
        self.assertNotIn("Content-Disposition", mapped)
        self.assertEqual(RecordingUpstream.requests, [])

        installer_path = proxy.DESKTOP_UPDATE_REQUEST_PREFIX + self.update_installer_name
        status, headers, payload = self.request(
            "GET", installer_path, headers={"Range": "bytes=10-19"}
        )
        mapped = self.headers_map(headers)
        self.assertEqual((status, payload), (206, self.download_payload[10:20]))
        self.assertEqual(mapped["Content-Type"], proxy.DOWNLOAD_CONTENT_TYPE)
        self.assertEqual(
            mapped["Cache-Control"], proxy.DESKTOP_UPDATE_IMMUTABLE_CACHE_CONTROL
        )
        self.assertEqual(
            mapped["Content-Disposition"],
            f'attachment; filename="{self.update_installer_name}"',
        )

        blockmap_path = proxy.DESKTOP_UPDATE_REQUEST_PREFIX + self.update_blockmap_name
        status, headers, payload = self.request("GET", blockmap_path)
        self.assertEqual((status, payload), (200, b"blockmap"))
        self.assertEqual(
            self.headers_map(headers)["Content-Type"],
            proxy.DESKTOP_UPDATE_BLOCKMAP_CONTENT_TYPE,
        )

        for rejected in (
            proxy.DESKTOP_UPDATE_REQUEST_PREFIX + "../latest.yml",
            proxy.DESKTOP_UPDATE_REQUEST_PREFIX + "latest.yml/extra",
            proxy.DESKTOP_UPDATE_REQUEST_PREFIX + "other.exe",
            proxy.DESKTOP_UPDATE_REQUEST_PREFIX + "%6catest.yml",
        ):
            with self.subTest(rejected=rejected):
                RecordingUpstream.requests.clear()
                status, _headers, payload = self.request("GET", rejected)
                self.assertEqual(status, 404)
                self.assertIn(b"download_not_found", payload)
                self.assertEqual(RecordingUpstream.requests, [])

    def test_macos_downloads_and_architecture_feeds_are_allowlisted(self) -> None:
        for architecture, artifacts in self.mac_artifacts.items():
            with self.subTest(architecture=architecture, kind="latest"):
                path = f"/downloads/{artifacts['latest']}"
                status, headers, payload = self.request("GET", path)
                mapped = self.headers_map(headers)
                self.assertEqual((status, payload), (200, self.download_payload))
                self.assertEqual(mapped["Content-Type"], proxy.MAC_DOWNLOAD_CONTENT_TYPE)
                self.assertEqual(mapped["Cache-Control"], proxy.DOWNLOAD_CACHE_CONTROL)

            prefix = f"/downloads/desktop/macos/{architecture}/"
            with self.subTest(architecture=architecture, kind="metadata"):
                status, headers, payload = self.request("GET", prefix + "latest-mac.yml")
                mapped = self.headers_map(headers)
                self.assertEqual((status, payload), (200, self.mac_update_payload))
                self.assertEqual(
                    mapped["Content-Type"], proxy.DESKTOP_UPDATE_METADATA_CONTENT_TYPE
                )
                self.assertNotIn("Content-Disposition", mapped)

            for name, expected_type in (
                (artifacts["dmg"], proxy.MAC_DOWNLOAD_CONTENT_TYPE),
                (artifacts["zip"], proxy.MAC_ZIP_CONTENT_TYPE),
                (artifacts["blockmap"], proxy.DESKTOP_UPDATE_BLOCKMAP_CONTENT_TYPE),
            ):
                with self.subTest(architecture=architecture, artifact=name):
                    status, headers, payload = self.request("GET", prefix + name)
                    self.assertEqual(status, 200)
                    self.assertTrue(payload)
                    self.assertEqual(self.headers_map(headers)["Content-Type"], expected_type)

            for rejected in (
                prefix + "../latest-mac.yml",
                prefix + "latest-mac.yml/extra",
                prefix + f"TMCRA-Memory-0.2.8-{'arm64' if architecture == 'x64' else 'x64'}.zip",
                prefix + "other.zip",
            ):
                with self.subTest(architecture=architecture, rejected=rejected):
                    RecordingUpstream.requests.clear()
                    status, _headers, payload = self.request("GET", rejected)
                    self.assertEqual(status, 404)
                    self.assertIn(b"download_not_found", payload)
                    self.assertEqual(RecordingUpstream.requests, [])

    def test_health_identifies_the_exact_running_release(self) -> None:
        status, headers, payload = self.request("GET", "/__deployment/health")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["release"], "test-release")
        self.assert_hsts(headers)

    def test_installer_supports_closed_open_and_suffix_byte_ranges(self) -> None:
        cases = (
            ("bytes=10-19", 10, 19),
            ("bytes=131320-", 131320, len(self.download_payload) - 1),
            ("bytes=-17", len(self.download_payload) - 17, len(self.download_payload) - 1),
            ("bytes=0-999999", 0, len(self.download_payload) - 1),
        )
        for range_header, start, end in cases:
            with self.subTest(range_header=range_header):
                status, headers, payload = self.request(
                    "GET",
                    proxy.DOWNLOAD_REQUEST_PATH,
                    headers={"Range": range_header},
                )
                mapped = self.headers_map(headers)
                self.assertEqual(status, 206)
                self.assertEqual(payload, self.download_payload[start : end + 1])
                self.assertEqual(
                    mapped["Content-Range"],
                    f"bytes {start}-{end}/{len(self.download_payload)}",
                )
                self.assertEqual(mapped["Content-Length"], str(end - start + 1))
                self.assertEqual(mapped["Accept-Ranges"], "bytes")
                self.assertEqual(RecordingUpstream.requests, [])

        status, headers, payload = self.request(
            "HEAD",
            proxy.DOWNLOAD_REQUEST_PATH,
            headers={"Range": "bytes=5-12"},
        )
        mapped = self.headers_map(headers)
        self.assertEqual((status, payload), (206, b""))
        self.assertEqual(
            mapped["Content-Range"],
            f"bytes 5-12/{len(self.download_payload)}",
        )
        self.assertEqual(mapped["Content-Length"], "8")

    def test_if_range_never_splices_two_installer_versions(self) -> None:
        status, headers, payload = self.request("HEAD", proxy.DOWNLOAD_REQUEST_PATH)
        self.assertEqual((status, payload), (200, b""))
        etag = self.headers_map(headers)["ETag"]

        status, headers, payload = self.request(
            "GET",
            proxy.DOWNLOAD_REQUEST_PATH,
            headers={"Range": "bytes=10-19", "If-Range": etag},
        )
        self.assertEqual((status, payload), (206, self.download_payload[10:20]))
        self.assertEqual(self.headers_map(headers)["ETag"], etag)

        status, headers, payload = self.request(
            "GET",
            proxy.DOWNLOAD_REQUEST_PATH,
            headers={"Range": "bytes=10-19", "If-Range": '"sha256-stale"'},
        )
        self.assertEqual((status, payload), (200, self.download_payload))
        self.assertNotIn("Content-Range", self.headers_map(headers))

        installer = self.download_root / proxy.DOWNLOAD_FILENAME
        replacement = self.download_root / "replacement.exe"
        next_payload = b"next release" * 12000
        replacement.write_bytes(next_payload)
        replacement.replace(installer)
        try:
            status, headers, payload = self.request(
                "GET",
                proxy.DOWNLOAD_REQUEST_PATH,
                headers={"Range": "bytes=10-19", "If-Range": etag},
            )
        finally:
            installer.write_bytes(self.download_payload)
        self.assertEqual((status, payload), (200, next_payload))
        self.assertNotEqual(self.headers_map(headers)["ETag"], etag)

    def test_installer_rejects_invalid_or_unsatisfiable_ranges(self) -> None:
        invalid_ranges = (
            "bytes=",
            "items=0-1",
            "bytes=0-1,3-4",
            "bytes=-0",
            "bytes=20-10",
            f"bytes={len(self.download_payload)}-",
            "bytes=abc-def",
        )
        for range_header in invalid_ranges:
            with self.subTest(range_header=range_header):
                status, headers, payload = self.request(
                    "GET",
                    proxy.DOWNLOAD_REQUEST_PATH,
                    headers={"Range": range_header},
                )
                mapped = self.headers_map(headers)
                self.assertEqual((status, payload), (416, b""))
                self.assertEqual(
                    mapped["Content-Range"],
                    f"bytes */{len(self.download_payload)}",
                )
                self.assertEqual(mapped["Content-Length"], "0")
                self.assertEqual(mapped["Accept-Ranges"], "bytes")
                self.assertEqual(RecordingUpstream.requests, [])

    def test_only_the_exact_installer_path_is_served_and_other_methods_fail_closed(self) -> None:
        status, headers, payload = self.request("POST", proxy.DOWNLOAD_REQUEST_PATH)
        self.assertEqual((status, payload), (405, b""))
        self.assertEqual(self.headers_map(headers)["Allow"], "GET, HEAD")
        self.assertEqual(RecordingUpstream.requests, [])

        for path in (
            "/downloads/TMCRA-Memory-Setup-latest.exe/extra",
            "/downloads/../TMCRA-Memory-Setup-latest.exe",
            "/downloads/%54MCRA-Memory-Setup-latest.exe",
            "/downloads/another.exe",
        ):
            with self.subTest(path=path):
                RecordingUpstream.requests.clear()
                status, _headers, payload = self.request("GET", path)
                self.assertEqual((status, payload), (200, b"ok"))
                self.assertEqual(len(RecordingUpstream.requests), 1)
                self.assertEqual(RecordingUpstream.requests[0][1], path)

    def test_missing_installer_does_not_fall_back_to_upstream(self) -> None:
        original_root = proxy.DOWNLOAD_ROOT
        proxy.DOWNLOAD_ROOT = str(self.download_root / "missing")
        try:
            status, headers, payload = self.request("GET", proxy.DOWNLOAD_REQUEST_PATH)
        finally:
            proxy.DOWNLOAD_ROOT = original_root
        self.assertEqual(status, 404)
        self.assertIn(b"download_not_found", payload)
        self.assertEqual(RecordingUpstream.requests, [])
        self.assert_hsts(headers)

    def test_registration_proxies_console_with_normalized_identity_and_secure_cookie(self) -> None:
        jar: dict[str, str] = {}
        headers = self.register(jar, email="Alice@Bücher.example", name="Alice 陈")
        session_cookies = [
            value
            for name, value in headers
            if name.lower() == "set-cookie" and value.startswith(proxy.SESSION_COOKIE + "=")
        ]
        self.assertEqual(len(session_cookies), 1)
        self.assertIn("Secure", session_cookies[0])
        self.assertIn("HttpOnly", session_cookies[0])
        self.assertIn("SameSite=Lax", session_cookies[0])
        self.assertIn(proxy.SESSION_COOKIE, jar)

        status, _headers, _payload = self.request(
            "GET", "/personal", headers={"Cookie": self.cookie_header(jar)}
        )
        self.assertEqual(status, 200)
        self.assert_hsts(_headers)
        forwarded = RecordingUpstream.requests[-1][2]
        self.assertEqual(forwarded["oai-authenticated-user-email"], "alice@xn--bcher-kva.example")
        self.assertEqual(
            urllib.parse.unquote(forwarded["oai-authenticated-user-full-name"]),
            "Alice 陈",
        )
        self.assertEqual(
            forwarded["oai-authenticated-user-full-name-encoding"],
            "percent-encoded-utf-8",
        )

        with proxy._database() as database:
            stored = database.execute(
                "SELECT password_hash, password_salt FROM account_users"
            ).fetchone()
        self.assertNotIn(b"Correct-Horse-42!", bytes(stored["password_hash"]))
        self.assertEqual(len(bytes(stored["password_salt"])), 16)

    def test_signed_out_customer_routes_stay_local_and_api_returns_401(self) -> None:
        status, headers, _payload = self.request("GET", "/console?tab=usage")
        self.assertEqual(status, 303)
        self.assert_hsts(headers)
        location = self.headers_map(headers)["Location"]
        self.assertTrue(location.startswith("/login?return_to="))
        self.assertNotIn("chatgpt.site", location)
        self.assertEqual(RecordingUpstream.requests, [])

        status, _headers, payload = self.request("GET", "/api/personal")
        self.assertEqual(status, 401)
        self.assert_hsts(_headers)
        self.assertIn(b"authentication_required", payload)
        self.assertEqual(RecordingUpstream.requests, [])

    def test_gpuhome_8443_authority_can_register_and_unlisted_authorities_fail(self) -> None:
        authority = "euvbyqa1jpvdm7yq-2000.sc01-webservice.gpuhome.cc:8443"
        jar: dict[str, str] = {}
        status, headers, payload = self.request(
            "GET",
            "/login?return_to=%2Fpersonal",
            headers={"Host": authority},
        )
        self.assertEqual(status, 200)
        decoded = payload.decode("utf-8")
        self.assertIn("Welcome back", decoded)
        self.update_cookies(jar, headers)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', payload).group(1).decode()
        status, headers, _payload = self.submit_auth(
            jar,
            action="register",
            email="gpu-user@example.com",
            password="GPUHome-Strong-84!",
            full_name="GPU 用户",
            csrf=csrf,
            origin=f"https://{authority}",
            host=authority,
        )
        self.assertEqual(status, 200)
        self.assertNotIn(proxy.SESSION_COOKIE, jar)
        self.assertEqual(self.sent_emails[-1][0], "gpu-user@example.com")

        for bad_host in (
            "euvbyqa1jpvdm7yq-2000.sc01-webservice.gpuhome.cc:9443",
            "evil.example:8443",
        ):
            status, _headers, payload = self.request(
                "GET",
                "/signin-with-chatgpt",
                headers={"Host": bad_host, "X-TMCRA-Public-Host": "tmcra.com"},
            )
            self.assertEqual(status, 421)
            self.assert_hsts(_headers)
            self.assertIn(b"untrusted_host", payload)

    def test_auth_pages_use_branded_responsive_account_shell(self) -> None:
        status, headers, payload = self.request(
            "GET", "/login?return_to=%2Fpersonal"
        )
        self.assertEqual(status, 200)
        self.assertIn(
            "img-src 'self'",
            self.headers_map(headers)["Content-Security-Policy"],
        )
        decoded = payload.decode("utf-8")
        self.assertIn('class="account-frame"', decoded)
        self.assertIn('src="/brand/tmcra-mark.png"', decoded)
        self.assertIn('class="auth-switch"', decoded)
        self.assertIn('href="/login?return_to=%2Fpersonal" aria-current="page"', decoded)
        self.assertIn("Welcome back", decoded)
        self.assertIn("欢迎回来", decoded)
        self.assertIn("@media(max-width:680px)", decoded)
        self.assertNotIn('<a class="brand" href="/">TMCRA</a>', decoded)

        status, _headers, payload = self.request(
            "GET", "/register?return_to=%2Fpersonal"
        )
        self.assertEqual(status, 200)
        decoded = payload.decode("utf-8")
        self.assertIn(
            'href="/register?return_to=%2Fpersonal" aria-current="page"', decoded
        )
        self.assertIn("Create your account", decoded)
        self.assertIn("创建账户", decoded)
        self.assertIn('autocomplete="new-password"', decoded)
        self.assertIn('minlength="8"', decoded)
        self.assertIn("English letter and a number", decoded)
        self.assertIn("无需大写字母或符号", decoded)

    def test_general_auth_defaults_to_console_and_preserves_explicit_product_routes(
        self,
    ) -> None:
        status, headers, _payload = self.request("GET", "/signin-with-chatgpt")
        self.assertEqual(status, 303)
        self.assertEqual(
            self.headers_map(headers)["Location"],
            "/login?return_to=%2Fconsole",
        )

        status, _headers, payload = self.request("GET", "/login")
        self.assertEqual(status, 200)
        self.assertIn(b'name="return_to" value="/console"', payload)

        jar: dict[str, str] = {}
        self.register(jar)
        status, headers, _payload = self.request(
            "GET",
            "/login",
            headers={"Cookie": self.cookie_header(jar)},
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.headers_map(headers)["Location"], "/console")

        status, headers, _payload = self.request(
            "GET",
            "/login?return_to=%2Fpersonal",
            headers={"Cookie": self.cookie_header(jar)},
        )
        self.assertEqual(status, 303)
        self.assertEqual(self.headers_map(headers)["Location"], "/personal")

    def test_registration_accepts_eight_character_lowercase_letter_number_password(
        self,
    ) -> None:
        for password in ("abc1234", "abcdefgh", "12345678"):
            with self.subTest(password=password):
                status, _headers, payload = self.submit_auth(
                    {},
                    action="register",
                    email=f"weak-{password}@example.com",
                    password=password,
                    full_name="Password Policy",
                )
                self.assertEqual(status, 400)
                self.assertIn(b"at least one English letter", payload)

        status, _headers, payload = self.submit_auth(
            {},
            action="register",
            email="simple-policy@example.com",
            password="simple12",
            full_name="Simple Policy",
        )
        self.assertEqual(status, 200)
        self.assertIn(b"The code was sent automatically", payload)
        self.assertEqual(self.sent_emails[-1][0], "simple-policy@example.com")

    def test_registration_reports_field_conflict_and_expired_form_errors(self) -> None:
        status, _headers, payload = self.submit_auth(
            {},
            action="register",
            email="invalid-email",
            password="simple12",
            full_name="Valid Name",
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Enter a valid email address", payload)

        status, _headers, payload = self.submit_auth(
            {},
            action="register",
            email="invalid-name@example.com",
            password="simple12",
            full_name="",
        )
        self.assertEqual(status, 400)
        self.assertIn(b"Enter a valid name", payload)

        registered: dict[str, str] = {}
        self.register(registered, email="existing@example.com")
        status, _headers, payload = self.submit_auth(
            {},
            action="register",
            email="existing@example.com",
            password="another12",
            full_name="Existing User",
        )
        self.assertEqual(status, 400)
        self.assertIn(b"already registered", payload)

        jar: dict[str, str] = {}
        self.auth_page(jar)
        status, _headers, payload = self.submit_auth(
            jar,
            action="register",
            email="expired-form@example.com",
            password="simple12",
            full_name="Expired Form",
            csrf="1.tampered.invalid",
        )
        self.assertEqual(status, 400)
        self.assertIn(b"form has expired", payload)

    def test_login_reports_incorrect_credentials_and_rotates_session(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar)
        first_token = jar[proxy.SESSION_COOKIE]
        failed_login: dict[str, str] = {}
        csrf = self.auth_page(failed_login)
        status, _headers, payload = self.submit_auth(
            failed_login,
            action="sign_in",
            email="alice@example.com",
            password="definitely-wrong",
            csrf=csrf,
        )
        self.assertEqual(status, 400)
        self.assertIn(b"email or password is incorrect", payload)
        self.assertNotIn(b"alice@example.com", payload)

        login_jar: dict[str, str] = {}
        status, _headers, _payload = self.submit_auth(
            login_jar,
            action="sign_in",
            email="ALICE@example.com",
            password="Correct-Horse-42!",
        )
        self.assertEqual(status, 303)
        self.assertNotEqual(login_jar[proxy.SESSION_COOKIE], first_token)

    def test_session_periodic_rotation_invalidates_old_cookie(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar)
        old_token = jar[proxy.SESSION_COOKIE]
        original_interval = proxy.SESSION_ROTATE_SECONDS
        proxy.SESSION_ROTATE_SECONDS = 0
        try:
            status, headers, _payload = self.request(
                "GET", "/personal", headers={"Cookie": self.cookie_header(jar)}
            )
            self.assertEqual(status, 200)
            self.update_cookies(jar, headers)
        finally:
            proxy.SESSION_ROTATE_SECONDS = original_interval
        self.assertNotEqual(jar[proxy.SESSION_COOKIE], old_token)
        status, headers, _payload = self.request(
            "GET", "/personal", headers={"Cookie": f"{proxy.SESSION_COOKIE}={old_token}"}
        )
        self.assertEqual(status, 303)
        self.assertTrue(
            self.headers_map(headers)["Location"].startswith("/login")
        )

    def test_cross_user_sessions_and_forged_identity_are_isolated(self) -> None:
        alice: dict[str, str] = {}
        bob: dict[str, str] = {}
        self.register(alice, "alice@example.com", "Alice", "Correct-Horse-42!")
        self.register(bob, "bob@example.com", "Bob", "Another-Strong-73!")

        self.request(
            "GET",
            "/api/personal",
            headers={
                "Cookie": self.cookie_header(alice),
                "oai-authenticated-user-email": "attacker@example.test",
            },
        )
        alice_headers = RecordingUpstream.requests[-1][2]
        self.request(
            "GET", "/api/personal", headers={"Cookie": self.cookie_header(bob)}
        )
        bob_headers = RecordingUpstream.requests[-1][2]
        self.assertEqual(alice_headers["oai-authenticated-user-email"], "alice@example.com")
        self.assertEqual(bob_headers["oai-authenticated-user-email"], "bob@example.com")
        self.assertNotEqual(
            alice_headers["oai-authenticated-user-email"],
            bob_headers["oai-authenticated-user-email"],
        )

    def test_public_request_cannot_forge_account_identity(self) -> None:
        status, _headers, _payload = self.request(
            "GET",
            "/",
            headers={
                "oai-authenticated-user-email": "attacker@example.test",
                "oai-authenticated-user-full-name": "Attacker",
            },
        )
        self.assertEqual(status, 200)
        forwarded = RecordingUpstream.requests[0][2]
        self.assertNotIn("oai-authenticated-user-email", forwarded)
        self.assertNotIn("oai-authenticated-user-full-name", forwarded)

    def test_device_start_and_poll_are_anonymous_but_approval_is_not(self) -> None:
        for path in (
            "/api/device/v1/authorizations",
            "/api/device/v1/token",
        ):
            status, _headers, _payload = self.request(
                "POST",
                path,
                headers={
                    "Content-Type": "application/json",
                    "oai-authenticated-user-email": "forged@example.test",
                    "CF-Connecting-IP": "203.0.113.99",
                },
                body=b"{}",
            )
            self.assertEqual(status, 200)
            self.assertNotIn(
                "oai-authenticated-user-email", RecordingUpstream.requests[-1][2]
            )
            self.assertEqual(
                RecordingUpstream.requests[-1][2]["cf-connecting-ip"], "127.0.0.1"
            )

        status, _headers, payload = self.request(
            "POST", "/api/device/v1/private", body=b"{}"
        )
        self.assertEqual(status, 401)
        self.assertIn(b"authentication_required", payload)

    def test_auth_post_rejects_cross_origin_and_tampered_csrf(self) -> None:
        jar: dict[str, str] = {}
        csrf = self.auth_page(jar)
        status, _headers, _payload = self.submit_auth(
            jar,
            action="register",
            email="alice@example.com",
            password="Correct-Horse-42!",
            full_name="Alice",
            csrf=csrf,
            origin="https://evil.example",
        )
        self.assertEqual(status, 400)
        with proxy._database() as database:
            self.assertEqual(database.execute("SELECT COUNT(*) FROM account_users").fetchone()[0], 0)

        jar = {}
        self.auth_page(jar)
        status, _headers, _payload = self.submit_auth(
            jar,
            action="register",
            email="alice@example.com",
            password="Correct-Horse-42!",
            full_name="Alice",
            csrf="1.tampered.invalid",
        )
        self.assertEqual(status, 400)

    def test_auth_post_accepts_opaque_origin_only_with_same_origin_fetch_metadata(self) -> None:
        jar: dict[str, str] = {}
        status, _headers, _payload = self.submit_auth(
            jar,
            action="register",
            email="webview@example.com",
            password="Opaque-Signal-42!",
            full_name="Webview User",
            origin="null",
            fetch_site="same-origin",
        )
        self.assertEqual(status, 200)
        self.assertEqual(self.sent_emails[-1][0], "webview@example.com")

        blocked_jar: dict[str, str] = {}
        status, _headers, _payload = self.submit_auth(
            blocked_jar,
            action="register",
            email="blocked-webview@example.com",
            password="Blocked-Signal-73!",
            full_name="Blocked Webview",
            origin="null",
            fetch_site="cross-site",
        )
        self.assertEqual(status, 400)
        with proxy._database() as database:
            self.assertIsNone(
                database.execute(
                    "SELECT id FROM account_users WHERE email = ?",
                    ("blocked-webview@example.com",),
                ).fetchone()
            )

    def test_return_to_cannot_escape_origin_or_inject_response_headers(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar)
        for value in ("//evil.example/path", "/\\evil.example", "/ok\r\nX-Evil: yes"):
            status, headers, _payload = self.request(
                "GET",
                "/login?return_to=" + urllib.parse.quote(value, safe=""),
                headers={"Cookie": self.cookie_header(jar)},
            )
            self.assertEqual(status, 303)
            location = self.headers_map(headers)["Location"]
            self.assertEqual(location, "/console")
            self.assertFalse(any(name.lower() == "x-evil" for name, _ in headers))

    def test_signout_requires_confirmation_and_revokes_session(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar)
        status, headers, payload = self.request(
            "GET",
            "/signout-with-chatgpt?return_to=%2F",
            headers={"Cookie": self.cookie_header(jar)},
        )
        self.assertEqual(status, 200)
        self.update_cookies(jar, headers)
        csrf = re.search(rb'name="csrf" value="([^"]+)"', payload).group(1).decode()
        body = urllib.parse.urlencode({"csrf": csrf, "return_to": "/"}).encode()
        status, headers, _payload = self.request(
            "POST",
            "/signout-with-chatgpt",
            headers={
                "Cookie": self.cookie_header(jar),
                "Origin": "https://tmcra.com",
                "Sec-Fetch-Site": "same-origin",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(body)),
            },
            body=body,
        )
        self.assertEqual(status, 303)
        self.update_cookies(jar, headers)
        self.assertNotIn(proxy.SESSION_COOKIE, jar)

    def test_internal_requires_allowlisted_ip_email_session_and_replaces_headers(self) -> None:
        status, headers, _payload = self.request("GET", "/internal")
        self.assertEqual(status, 404)
        self.assertNotIn("WWW-Authenticate", self.headers_map(headers))
        self.assertEqual(RecordingUpstream.requests, [])

        status, _headers, _payload = self.request(
            "GET",
            "/internal",
            headers={
                "X-TMCRA-Client-IP": "203.0.113.10",
                "Authorization": "Basic obsolete-credential",
                "oai-authenticated-user-email": "attacker@example.test",
            },
        )
        self.assertEqual(status, 303)
        self.assertTrue(
            self.headers_map(_headers)["Location"].startswith(
                "/login?return_to=%2Finternal"
            )
        )
        self.assertEqual(RecordingUpstream.requests, [])

        jar: dict[str, str] = {}
        self.register(
            jar,
            email="real-owner@example.test",
            name="Real Owner",
            return_to="/internal",
        )
        status, _headers, _payload = self.request(
            "GET",
            "/internal",
            headers={
                "X-TMCRA-Client-IP": "203.0.113.10",
                "Cookie": self.cookie_header(jar),
                "oai-authenticated-user-email": "attacker@example.test",
            },
        )
        self.assertEqual(status, 200)
        forwarded = RecordingUpstream.requests[0][2]
        self.assertEqual(
            forwarded["oai-authenticated-user-email"], "real-owner@example.test"
        )
        self.assertEqual(forwarded["cf-connecting-ip"], "203.0.113.10")

        with proxy._database() as database:
            outcomes = [
                row[0]
                for row in database.execute(
                    "SELECT outcome FROM internal_gateway_audit ORDER BY rowid"
                )
            ]
        self.assertEqual(
            outcomes,
            ["ip_denied", "authentication_required", "session_forwarded"],
        )

        status, headers, _payload = self.request(
            "GET",
            "/internal/system",
            headers={"X-TMCRA-Client-IP": "203.0.113.10"},
        )
        self.assertEqual(status, 303)
        self.assertTrue(
            self.headers_map(headers)["Location"].startswith(
                "/login?return_to=%2Finternal%2Fsystem"
            )
        )

    def test_untrusted_peer_cannot_forge_the_effective_client_ip(self) -> None:
        original = proxy.TRUSTED_PROXY_NETWORKS
        proxy.TRUSTED_PROXY_NETWORKS = ()
        try:
            status, _headers, _payload = self.request(
                "GET",
                "/internal",
                headers={"X-TMCRA-Client-IP": "203.0.113.10"},
            )
        finally:
            proxy.TRUSTED_PROXY_NETWORKS = original
        self.assertEqual(status, 404)
        self.assertEqual(RecordingUpstream.requests, [])

    def test_trusted_proxy_networks_must_be_loopback_host_routes(self) -> None:
        proxy._validate_trusted_proxy_networks(
            proxy._network_allowlist(
                "127.0.0.1/32,::1/128", "test trusted proxies"
            )
        )
        for value in (
            "",
            "127.0.0.0/24",
            "203.0.113.1/32",
            "::1/64",
            "2001:db8::1/128",
            "0.0.0.0/0",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                RuntimeError, "loopback host routes only"
            ):
                proxy._validate_trusted_proxy_networks(
                    proxy._network_allowlist(value, "test trusted proxies")
                )

    def test_internal_allowed_networks_must_be_exact_host_routes(self) -> None:
        for value in ("", "203.0.113.10/32", "2001:db8::10/128"):
            proxy._validate_internal_allowed_networks(
                proxy._network_allowlist(value, "test internal allowlist")
            )
        for value in (
            "203.0.113.0/24",
            "2001:db8::/64",
            "0.0.0.0/0",
            "::/0",
        ):
            with self.subTest(value=value), self.assertRaisesRegex(
                RuntimeError, "exact host routes only"
            ):
                proxy._validate_internal_allowed_networks(
                    proxy._network_allowlist(value, "test internal allowlist")
                )

    def test_internal_audit_rate_limits_unauthenticated_writes_and_keeps_404(
        self,
    ) -> None:
        original_per_client = proxy.INTERNAL_GATEWAY_AUDIT_UNAUTH_PER_CLIENT_WRITES
        original_global = proxy.INTERNAL_GATEWAY_AUDIT_UNAUTH_GLOBAL_WRITES
        proxy.INTERNAL_GATEWAY_AUDIT_UNAUTH_PER_CLIENT_WRITES = 3
        proxy.INTERNAL_GATEWAY_AUDIT_UNAUTH_GLOBAL_WRITES = 3
        proxy._reset_internal_gateway_audit_rate_limit()
        try:
            for _ in range(12):
                status, headers, _payload = self.request("GET", "/internal")
                self.assertEqual(status, 404)
                self.assertNotIn("WWW-Authenticate", self.headers_map(headers))
            with proxy._database() as database:
                rows = database.execute(
                    """SELECT outcome, event_type, response_status, final_outcome,
                              rate_limited
                       FROM internal_gateway_audit ORDER BY rowid"""
                ).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertTrue(all(row["outcome"] == "ip_denied" for row in rows))
            self.assertTrue(
                all(row["event_type"] == "internal_access" for row in rows)
            )
            self.assertTrue(all(row["response_status"] == 404 for row in rows))
            self.assertTrue(all(row["final_outcome"] == "ip_denied" for row in rows))
            self.assertEqual([row["rate_limited"] for row in rows], [0, 0, 1])
            self.assertEqual(RecordingUpstream.requests, [])
        finally:
            proxy.INTERNAL_GATEWAY_AUDIT_UNAUTH_PER_CLIENT_WRITES = (
                original_per_client
            )
            proxy.INTERNAL_GATEWAY_AUDIT_UNAUTH_GLOBAL_WRITES = original_global
            proxy._reset_internal_gateway_audit_rate_limit()

    def test_internal_audit_records_final_upstream_authorization_status(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar, email="audited-owner@example.test")
        RecordingUpstream.status_by_path["/api/internal"] = 403
        status, _headers, _payload = self.request(
            "GET",
            "/api/internal",
            headers={
                "X-TMCRA-Client-IP": "203.0.113.10",
                "Cookie": self.cookie_header(jar),
            },
        )
        self.assertEqual(status, 403)
        with proxy._database() as database:
            row = database.execute(
                """SELECT outcome, event_type, response_status, final_outcome,
                          account_user_id
                   FROM internal_gateway_audit ORDER BY rowid DESC LIMIT 1"""
            ).fetchone()
        self.assertEqual(row["outcome"], "session_forwarded")
        self.assertEqual(row["event_type"], "internal_access")
        self.assertEqual(row["response_status"], 403)
        self.assertEqual(row["final_outcome"], "upstream_forbidden")
        self.assertTrue(row["account_user_id"])

    def test_internal_audit_enforces_retention_and_row_cap(self) -> None:
        original_max_rows = proxy.INTERNAL_GATEWAY_AUDIT_MAX_ROWS
        original_retention = proxy.INTERNAL_GATEWAY_AUDIT_RETENTION_SECONDS
        proxy.INTERNAL_GATEWAY_AUDIT_MAX_ROWS = 3
        proxy.INTERNAL_GATEWAY_AUDIT_RETENTION_SECONDS = 10
        jar: dict[str, str] = {}
        self.register(jar, email="retention-owner@example.test")
        try:
            with proxy._database() as database:
                database.execute(
                    """INSERT INTO internal_gateway_audit (
                           id, occurred_at, client_ip_hash, method, path, outcome,
                           event_type, response_status, final_outcome
                       ) VALUES ('legacy-old', ?, 'old-hash', 'GET', '/internal',
                                 'ip_denied', 'internal_access', 404, 'ip_denied')""",
                    (int(proxy.time.time()) - 100,),
                )
            for index in range(5):
                status, _headers, _payload = self.request(
                    "GET",
                    f"/internal?sample={index}",
                    headers={
                        "X-TMCRA-Client-IP": "203.0.113.10",
                        "Cookie": self.cookie_header(jar),
                    },
                )
                self.assertEqual(status, 200)
            with proxy._database() as database:
                rows = database.execute(
                    """SELECT id, response_status, final_outcome
                       FROM internal_gateway_audit ORDER BY occurred_at, id"""
                ).fetchall()
            self.assertEqual(len(rows), 3)
            self.assertNotIn("legacy-old", {row["id"] for row in rows})
            self.assertTrue(all(row["response_status"] == 200 for row in rows))
            self.assertTrue(all(row["final_outcome"] == "authorized" for row in rows))
        finally:
            proxy.INTERNAL_GATEWAY_AUDIT_MAX_ROWS = original_max_rows
            proxy.INTERNAL_GATEWAY_AUDIT_RETENTION_SECONDS = original_retention

    def test_internal_audit_schema_migrates_existing_rows_in_place(self) -> None:
        original_database = proxy.ACCOUNT_DATABASE
        with tempfile.TemporaryDirectory() as directory:
            legacy_database = pathlib.Path(directory) / "legacy.sqlite3"
            connection = sqlite3.connect(legacy_database)
            try:
                connection.executescript(
                    """CREATE TABLE internal_gateway_audit (
                           id TEXT PRIMARY KEY,
                           occurred_at INTEGER NOT NULL,
                           client_ip_hash TEXT NOT NULL,
                           method TEXT NOT NULL,
                           path TEXT NOT NULL,
                           outcome TEXT NOT NULL CHECK(
                               outcome IN ('ip_denied', 'authentication_required', 'session_forwarded')
                           ),
                           account_user_id TEXT
                       );
                       INSERT INTO internal_gateway_audit (
                           id, occurred_at, client_ip_hash, method, path, outcome
                       ) VALUES ('legacy-row', 4102444800, 'hash', 'GET',
                                 '/internal', 'ip_denied');"""
                )
                connection.commit()
            finally:
                connection.close()
            try:
                proxy.ACCOUNT_DATABASE = str(legacy_database)
                proxy._READY_DATABASE_PATH = None
                proxy._ensure_schema()
                connection = sqlite3.connect(legacy_database)
                try:
                    connection.row_factory = sqlite3.Row
                    columns = {
                        row[1]
                        for row in connection.execute(
                            "PRAGMA table_info(internal_gateway_audit)"
                        )
                    }
                    row = connection.execute(
                        "SELECT * FROM internal_gateway_audit WHERE id = 'legacy-row'"
                    ).fetchone()
                finally:
                    connection.close()
                self.assertTrue(
                    {"event_type", "response_status", "final_outcome", "rate_limited"}
                    <= columns
                )
                self.assertEqual(row["event_type"], "internal_access")
                self.assertIsNone(row["response_status"])
                self.assertIsNone(row["final_outcome"])
                self.assertEqual(row["rate_limited"], 0)
            finally:
                proxy.ACCOUNT_DATABASE = original_database
                proxy._READY_DATABASE_PATH = None
                proxy._ensure_schema()

    def test_login_rate_limit_is_persistent_and_returns_429(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar)
        latest_status = 0
        for _ in range(7):
            attempt: dict[str, str] = {}
            latest_status, _headers, _payload = self.submit_auth(
                attempt,
                action="sign_in",
                email="alice@example.com",
                password="wrong-password-value",
            )
        self.assertEqual(latest_status, 429)

    def test_verification_json_api_is_generic_and_confirms_the_code(self) -> None:
        jar: dict[str, str] = {}
        status, _headers, _payload = self.submit_auth(
            jar,
            action="register",
            email="pending-api@example.com",
            password="Correct-Horse-42!",
            full_name="Pending API",
        )
        self.assertEqual(status, 200)
        self.assertEqual(len(self.sent_emails), 1)

        status, _headers, payload = self.request_json(
            "POST",
            "/api/auth/v1/email-verifications",
            {"email": "pending-api@example.com"},
            origin="https://tmcra.com",
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload), {"ok": True, "status": "accepted"})
        self.assertEqual(len(self.sent_emails), 2)

        status, _headers, payload = self.request_json(
            "POST",
            "/api/auth/v1/email-verifications",
            {"email": "missing@example.com"},
            origin="https://tmcra.com",
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload), {"ok": True, "status": "accepted"})
        self.assertEqual(len(self.sent_emails), 2)

        code = self.code_from_email(self.sent_emails[-1])
        status, headers, payload = self.request_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "pending-api@example.com", "code": code},
            jar=jar,
            origin="https://tmcra.com",
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["ok"])
        self.assertIn(proxy.SESSION_COOKIE, jar)
        self.assert_hsts(headers)

    def test_verification_json_api_rejects_cross_origin_requests(self) -> None:
        status, _headers, payload = self.request_json(
            "POST",
            "/api/auth/v1/email-verifications",
            {"email": "person@example.com"},
            origin="https://evil.example",
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")

    def test_desktop_registration_verification_session_and_logout_contract(
        self,
    ) -> None:
        jar: dict[str, str] = {}
        status, headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/registrations",
            {
                "fullName": "Desktop User",
                "email": "Desktop.User@example.com",
                "password": "desktop8",
            },
            jar=jar,
        )
        self.assertEqual(status, 202)
        self.assert_hsts(headers)
        registration = json.loads(payload)
        self.assertEqual(registration["status"], "verification_required")
        self.assertEqual(
            set(registration["account"]), {"id", "email", "fullName"}
        )
        self.assertEqual(
            registration["account"] | {"id": "<dynamic>"},
            {
                "id": "<dynamic>",
                "email": "desktop.user@example.com",
                "fullName": "Desktop User",
            },
        )
        self.assertNotIn(proxy.SESSION_COOKIE, jar)
        code = self.code_from_email(self.sent_emails[-1])

        missing_status, _missing_headers, missing_payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "missing@example.com", "code": "000000"},
        )
        wrong_status, _wrong_headers, wrong_payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "desktop.user@example.com", "code": "000000"},
        )
        self.assertEqual((missing_status, wrong_status), (400, 400))
        self.assertEqual(json.loads(missing_payload), json.loads(wrong_payload))
        self.assertEqual(
            json.loads(wrong_payload)["error"]["code"],
            "invalid_or_expired_code",
        )

        status, headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "desktop.user@example.com", "code": code},
            jar=jar,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["account"], registration["account"])
        self.assertIn(proxy.SESSION_COOKIE, jar)
        session_cookie = next(
            value
            for name, value in headers
            if name.lower() == "set-cookie"
            and value.startswith(proxy.SESSION_COOKIE + "=")
        )
        self.assertIn("Secure", session_cookie)
        self.assertIn("HttpOnly", session_cookie)
        self.assertIn("SameSite=Lax", session_cookie)

        status, _headers, payload = self.request_desktop_json(
            "GET", "/api/auth/v1/sessions", jar=jar
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["account"], registration["account"])

        status, _headers, payload = self.request_desktop_json(
            "DELETE", "/api/auth/v1/sessions", jar=jar
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload), {"ok": True})
        self.assertNotIn(proxy.SESSION_COOKIE, jar)
        status, _headers, payload = self.request_desktop_json(
            "GET", "/api/auth/v1/sessions", jar=jar
        )
        self.assertEqual(status, 401)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "authentication_required"
        )

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "desktop.user@example.com", "code": code},
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "invalid_or_expired_code"
        )

    def test_desktop_auth_requires_an_explicit_native_header_or_same_origin(
        self,
    ) -> None:
        registration = {
            "fullName": "Origin User",
            "email": "origin-user@example.com",
            "password": "origin123",
        }
        status, _headers, payload = self.request_json(
            "POST", "/api/auth/v1/registrations", registration
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")

        status, _headers, payload = self.request_json(
            "POST",
            "/api/auth/v1/registrations",
            registration,
            headers={proxy.DESKTOP_CLIENT_HEADER: "tmcra-memory/0.1.8"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/registrations",
            registration,
            origin="https://evil.example",
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")

        status, _headers, payload = self.request_json(
            "POST",
            "/api/auth/v1/registrations",
            registration,
            origin="https://tmcra.com",
        )
        self.assertEqual(status, 202)
        self.assertTrue(json.loads(payload)["ok"])

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/registrations",
            {
                "full_name": "Snake Case",
                "email": "snake-case@example.com",
                "password": "snake123",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")

    def test_desktop_registration_error_taxonomy_and_rate_limit(self) -> None:
        cases = (
            (
                {
                    "fullName": "Invalid Email",
                    "email": "not-an-email",
                    "password": "valid123",
                },
                400,
                "invalid_email",
            ),
            (
                {
                    "fullName": "Weak Password",
                    "email": "weak@example.com",
                    "password": "abcdefgh",
                },
                400,
                "weak_password",
            ),
        )
        for request, expected_status, expected_code in cases:
            with self.subTest(expected_code=expected_code):
                status, _headers, payload = self.request_desktop_json(
                    "POST", "/api/auth/v1/registrations", request
                )
                self.assertEqual(status, expected_status)
                self.assertEqual(json.loads(payload)["error"]["code"], expected_code)

        existing: dict[str, str] = {}
        self.register(existing, email="desktop-conflict@example.com")
        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/registrations",
            {
                "fullName": "Conflict",
                "email": "desktop-conflict@example.com",
                "password": "conflict8",
            },
        )
        self.assertEqual(status, 409)
        self.assertEqual(json.loads(payload)["error"]["code"], "account_conflict")

        original_transport = proxy.MAIL_TRANSPORT
        proxy.MAIL_TRANSPORT = "disabled"
        try:
            status, _headers, payload = self.request_desktop_json(
                "POST",
                "/api/auth/v1/registrations",
                {
                    "fullName": "No Mail",
                    "email": "no-mail@example.com",
                    "password": "nomail123",
                },
            )
        finally:
            proxy.MAIL_TRANSPORT = original_transport
        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "email_delivery_unavailable"
        )

        original_sender = proxy._send_transactional_email

        def unavailable_sender(
            _recipient: str, _subject: str, _text: str, _html: str
        ) -> None:
            raise proxy.EmailDeliveryError("test_delivery_failure")

        proxy._send_transactional_email = unavailable_sender
        try:
            status, _headers, payload = self.request_desktop_json(
                "POST",
                "/api/auth/v1/registrations",
                {
                    "fullName": "Delivery Failure",
                    "email": "registration-delivery@example.com",
                    "password": "delivery8",
                },
            )
        finally:
            proxy._send_transactional_email = original_sender
        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "email_delivery_unavailable"
        )

        pending_request = {
            "fullName": "Rate Limited",
            "email": "desktop-register-rate@example.com",
            "password": "ratelimit8",
        }
        statuses = [
            self.request_desktop_json(
                "POST", "/api/auth/v1/registrations", pending_request
            )[0]
            for _ in range(4)
        ]
        self.assertEqual(statuses, [202, 202, 202, 429])

    def test_desktop_verification_error_taxonomy_and_rate_limit(self) -> None:
        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "invalid-email", "code": "000000"},
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_email")

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/email-verifications/confirm",
            {"email": "valid@example.com", "code": 123456},
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "invalid_or_expired_code"
        )

        statuses = [
            self.request_desktop_json(
                "POST",
                "/api/auth/v1/email-verifications/confirm",
                {"email": "verify-rate@example.com", "code": "000000"},
            )[0]
            for _ in range(9)
        ]
        self.assertEqual(statuses, [400, 400, 400, 400, 400, 400, 400, 400, 429])

    def test_desktop_sessions_distinguish_unverified_from_invalid_credentials(
        self,
    ) -> None:
        status, _headers, _payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/registrations",
            {
                "fullName": "Pending Login",
                "email": "pending-login@example.com",
                "password": "pending8",
            },
        )
        self.assertEqual(status, 202)
        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {"email": "pending-login@example.com", "password": "pending8"},
        )
        self.assertEqual(status, 403)
        self.assertEqual(json.loads(payload)["error"]["code"], "unverified_account")

        verified: dict[str, str] = {}
        self.register(verified, email="desktop-session@example.com")
        existing = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {"email": "desktop-session@example.com", "password": "wrong-pass"},
        )
        missing = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {"email": "missing-session@example.com", "password": "wrong-pass"},
        )
        self.assertEqual(existing[0], 401)
        self.assertEqual(existing[0], missing[0])
        self.assertEqual(json.loads(existing[2]), json.loads(missing[2]))
        self.assertEqual(
            json.loads(existing[2])["error"]["code"], "invalid_credentials"
        )

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {
                "email": "desktop-session@example.com",
                "password": "Correct-Horse-42!",
                "fullName": "Not accepted here",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")

        login_jar: dict[str, str] = {}
        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {
                "email": "DESKTOP-SESSION@example.com",
                "password": "Correct-Horse-42!",
            },
            jar=login_jar,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload)["account"]["email"], "desktop-session@example.com"
        )
        self.assertIn(proxy.SESSION_COOKIE, login_jar)

        rate_statuses = [
            self.request_desktop_json(
                "POST",
                "/api/auth/v1/sessions",
                {"email": "desktop-rate@example.com", "password": "wrong-pass"},
            )[0]
            for _ in range(7)
        ]
        self.assertEqual(rate_statuses, [401, 401, 401, 401, 401, 401, 429])

    def test_desktop_password_reset_is_generic_single_use_and_revokes_sessions(
        self,
    ) -> None:
        old_jar: dict[str, str] = {}
        self.register(old_jar, email="desktop-reset@example.com")
        self.sent_emails.clear()

        existing = self.request_desktop_json(
            "POST",
            "/api/auth/v1/password-resets",
            {"email": "desktop-reset@example.com"},
        )
        missing = self.request_desktop_json(
            "POST",
            "/api/auth/v1/password-resets",
            {"email": "missing-reset@example.com"},
        )
        self.assertEqual(existing[0], 202)
        self.assertEqual(existing[0], missing[0])
        self.assertEqual(json.loads(existing[2]), json.loads(missing[2]))
        self.assertEqual(len(self.sent_emails), 1)
        code = self.code_from_email(self.sent_emails[-1])

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/password-resets/confirm",
            {
                "email": "not-an-email",
                "code": code,
                "password": "replacement9",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_email")

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/password-resets/confirm",
            {
                "email": "desktop-reset@example.com",
                "code": code,
                "password": "abcdefgh",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "weak_password")

        reset_jar: dict[str, str] = {}
        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/password-resets/confirm",
            {
                "email": "desktop-reset@example.com",
                "code": code,
                "password": "replacement9",
            },
            jar=reset_jar,
        )
        self.assertEqual(status, 200)
        self.assertEqual(
            json.loads(payload)["account"]["email"], "desktop-reset@example.com"
        )
        self.assertIn(proxy.SESSION_COOKIE, reset_jar)

        status, _headers, payload = self.request_desktop_json(
            "GET", "/api/auth/v1/sessions", jar=old_jar
        )
        self.assertEqual(status, 401)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "authentication_required"
        )

        status, _headers, payload = self.request_desktop_json(
            "POST",
            "/api/auth/v1/password-resets/confirm",
            {
                "email": "desktop-reset@example.com",
                "code": code,
                "password": "replacement9",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "invalid_or_expired_code"
        )

        old_login = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {
                "email": "desktop-reset@example.com",
                "password": "Correct-Horse-42!",
            },
        )
        new_login = self.request_desktop_json(
            "POST",
            "/api/auth/v1/sessions",
            {
                "email": "desktop-reset@example.com",
                "password": "replacement9",
            },
        )
        self.assertEqual(old_login[0], 401)
        self.assertEqual(new_login[0], 200)

    def test_desktop_password_reset_delivery_and_rate_limit_contract(self) -> None:
        account: dict[str, str] = {}
        self.register(account, email="reset-delivery@example.com")
        original_sender = proxy._send_transactional_email

        def unavailable_sender(
            _recipient: str, _subject: str, _text: str, _html: str
        ) -> None:
            raise proxy.EmailDeliveryError("test_delivery_failure")

        proxy._send_transactional_email = unavailable_sender
        try:
            existing = self.request_desktop_json(
                "POST",
                "/api/auth/v1/password-resets",
                {"email": "reset-delivery@example.com"},
            )
            missing = self.request_desktop_json(
                "POST",
                "/api/auth/v1/password-resets",
                {"email": "missing-delivery@example.com"},
            )
        finally:
            proxy._send_transactional_email = original_sender
        self.assertEqual(existing[0], 202)
        self.assertEqual(existing[0], missing[0])
        self.assertEqual(json.loads(existing[2]), json.loads(missing[2]))

        original_transport = proxy.MAIL_TRANSPORT
        proxy.MAIL_TRANSPORT = "disabled"
        try:
            status, _headers, payload = self.request_desktop_json(
                "POST",
                "/api/auth/v1/password-resets",
                {"email": "any-account@example.com"},
            )
        finally:
            proxy.MAIL_TRANSPORT = original_transport
        self.assertEqual(status, 503)
        self.assertEqual(
            json.loads(payload)["error"]["code"], "email_delivery_unavailable"
        )

        statuses = [
            self.request_desktop_json(
                "POST",
                "/api/auth/v1/password-resets",
                {"email": "desktop-reset-rate@example.com"},
            )[0]
            for _ in range(4)
        ]
        self.assertEqual(statuses, [202, 202, 202, 429])

        confirm_statuses = [
            self.request_desktop_json(
                "POST",
                "/api/auth/v1/password-resets/confirm",
                {
                    "email": "desktop-reset-confirm-rate@example.com",
                    "code": "000000",
                    "password": "replacement9",
                },
            )[0]
            for _ in range(9)
        ]
        self.assertEqual(
            confirm_statuses,
            [400, 400, 400, 400, 400, 400, 400, 400, 429],
        )

    def test_desktop_auth_endpoint_methods_are_fixed(self) -> None:
        for path, method in (
            ("/api/auth/v1/registrations", "GET"),
            ("/api/auth/v1/email-verifications/confirm", "GET"),
            ("/api/auth/v1/sessions", "PUT"),
            ("/api/auth/v1/password-resets", "GET"),
            ("/api/auth/v1/password-resets/confirm", "DELETE"),
        ):
            with self.subTest(path=path, method=method):
                status, _headers, payload = self.request_desktop_json(method, path)
                self.assertEqual(status, 405)
                self.assertEqual(
                    json.loads(payload)["error"]["code"], "method_not_allowed"
                )

    def test_marketing_campaign_is_consented_queued_and_idempotent(self) -> None:
        opted_in: dict[str, str] = {}
        self.register(opted_in, email="subscriber@example.com")
        not_opted_in: dict[str, str] = {}
        self.register(not_opted_in, email="account-only@example.com")

        status, _headers, payload = self.request_json(
            "PUT",
            "/api/account/v1/email-preferences",
            {"marketing": True},
            jar=opted_in,
            origin="https://tmcra.com",
        )
        self.assertEqual(status, 200)
        self.assertTrue(json.loads(payload)["preferences"]["marketing"])

        campaign_request = {
            "idempotency_key": "launch-2026-07-17",
            "subject": "TMCRA launch update",
            "text_body": "The launch is ready.",
            "html_body": "<p>The launch is ready.</p>",
        }
        auth = {
            "Authorization": f"Bearer {proxy.MARKETING_API_TOKEN}",
            "X-TMCRA-Client-IP": "203.0.113.10",
        }
        status, _headers, payload = self.request_json(
            "POST",
            "/internal/email/v1/campaigns",
            campaign_request,
            headers=auth,
        )
        self.assertEqual(status, 202)
        campaign = json.loads(payload)["campaign"]
        self.assertEqual(campaign["recipient_count"], 1)
        self.assertEqual(self.sent_marketing_emails, [])

        self.assertTrue(proxy._process_marketing_delivery_once())
        self.assertFalse(proxy._process_marketing_delivery_once())
        self.assertEqual(len(self.sent_marketing_emails), 1)
        sent = self.sent_marketing_emails[0]
        self.assertEqual(sent[0], "subscriber@example.com")
        self.assertIn("Unsubscribe:", sent[2])
        self.assertIn("/email/unsubscribe?token=", sent[4])

        status, _headers, payload = self.request_json(
            "GET",
            f"/internal/email/v1/campaigns/{campaign['id']}",
            headers=auth,
        )
        self.assertEqual(status, 200)
        completed = json.loads(payload)["campaign"]
        self.assertEqual(completed["state"], "completed")
        self.assertEqual(completed["sent_count"], 1)

        status, _headers, payload = self.request_json(
            "POST",
            "/internal/email/v1/campaigns",
            campaign_request,
            headers=auth,
        )
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(payload)["campaign"]["id"], campaign["id"])
        with proxy._database() as database:
            audit_rows = database.execute(
                """SELECT event_type, response_status, final_outcome
                   FROM internal_gateway_audit ORDER BY rowid"""
            ).fetchall()
        self.assertEqual(
            [row["event_type"] for row in audit_rows],
            ["marketing_campaign", "marketing_campaign", "marketing_campaign"],
        )
        self.assertEqual(
            [row["response_status"] for row in audit_rows], [202, 200, 200]
        )
        self.assertEqual(
            [row["final_outcome"] for row in audit_rows],
            ["marketing_created", "marketing_read", "marketing_idempotent_replay"],
        )

    def test_marketing_api_requires_its_own_token_and_rejects_recipient_lists(self) -> None:
        campaign_request = {
            "idempotency_key": "security-2026-07-17",
            "subject": "Security update",
            "text_body": "A security update is available.",
            "html_body": "<p>A security update is available.</p>",
        }
        status, _headers, _payload = self.request_json(
            "POST",
            "/internal/email/v1/campaigns",
            campaign_request,
            headers={"Authorization": f"Bearer {proxy.MARKETING_API_TOKEN}"},
        )
        self.assertEqual(status, 404)

        status, headers, _payload = self.request_json(
            "POST",
            "/internal/email/v1/campaigns",
            campaign_request,
            headers={"X-TMCRA-Client-IP": "203.0.113.10"},
        )
        self.assertEqual(status, 401)
        self.assertIn("Bearer", self.headers_map(headers)["WWW-Authenticate"])

        campaign_request["recipients"] = ["victim@example.com"]
        status, _headers, payload = self.request_json(
            "POST",
            "/internal/email/v1/campaigns",
            campaign_request,
            headers={
                "Authorization": f"Bearer {proxy.MARKETING_API_TOKEN}",
                "X-TMCRA-Client-IP": "203.0.113.10",
            },
        )
        self.assertEqual(status, 400)
        self.assertEqual(json.loads(payload)["error"]["code"], "invalid_request")
        with proxy._database() as database:
            audit_rows = database.execute(
                """SELECT outcome, event_type, response_status, final_outcome
                   FROM internal_gateway_audit ORDER BY rowid"""
            ).fetchall()
        self.assertEqual(
            [row["event_type"] for row in audit_rows],
            ["marketing_campaign", "marketing_campaign", "marketing_campaign"],
        )
        self.assertEqual(
            [row["response_status"] for row in audit_rows], [404, 401, 400]
        )
        self.assertEqual(
            [row["final_outcome"] for row in audit_rows],
            [
                "ip_denied",
                "marketing_authentication_required",
                "marketing_invalid_request",
            ],
        )
        self.assertEqual(
            [row["outcome"] for row in audit_rows],
            ["ip_denied", "authentication_required", "session_forwarded"],
        )

    def test_unsubscribe_requires_post_and_blocks_future_campaigns(self) -> None:
        jar: dict[str, str] = {}
        self.register(jar, email="unsubscribe@example.com")
        self.request_json(
            "PUT",
            "/api/account/v1/email-preferences",
            {"marketing": True},
            jar=jar,
            origin="https://tmcra.com",
        )
        with proxy._database() as database:
            user_id = str(
                database.execute(
                    "SELECT id FROM account_users WHERE email = ?",
                    ("unsubscribe@example.com",),
                ).fetchone()[0]
            )
        token = proxy._marketing_unsubscribe_token(user_id)
        encoded_token = urllib.parse.quote(token, safe="")

        status, _headers, _payload = self.request(
            "GET", f"/email/unsubscribe?token={encoded_token}"
        )
        self.assertEqual(status, 200)
        status, _headers, _payload = self.post_form(
            {},
            f"/email/unsubscribe?token={encoded_token}",
            {"List-Unsubscribe": "One-Click"},
        )
        self.assertEqual(status, 200)

        status, _headers, payload = self.request_json(
            "GET", "/api/account/v1/email-preferences", jar=jar
        )
        self.assertEqual(status, 200)
        self.assertFalse(json.loads(payload)["preferences"]["marketing"])

        status, _headers, payload = self.request_json(
            "POST",
            "/internal/email/v1/campaigns",
            {
                "idempotency_key": "after-unsubscribe-2026",
                "subject": "Later update",
                "text_body": "Later.",
                "html_body": "<p>Later.</p>",
            },
            headers={
                "Authorization": f"Bearer {proxy.MARKETING_API_TOKEN}",
                "X-TMCRA-Client-IP": "203.0.113.10",
            },
        )
        self.assertEqual(status, 202)
        self.assertEqual(json.loads(payload)["campaign"]["recipient_count"], 0)


if __name__ == "__main__":
    unittest.main()
