from __future__ import annotations

import base64
import hashlib
import hmac
import html
import http.client
import ipaddress
import json
import os
import re
import secrets
import smtplib
import sqlite3
import ssl
import stat
import sys
import threading
import time
import unicodedata
import urllib.parse
from contextlib import contextmanager
from email.message import EmailMessage
from email.utils import formataddr
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


def _normalized_authority(value: str) -> str | None:
    candidate = value.split(",", 1)[0].strip().lower()
    if not candidate:
        return None
    try:
        parsed = urllib.parse.urlsplit(f"//{candidate}")
        hostname = parsed.hostname
        port = parsed.port
    except ValueError:
        return None
    if (
        not hostname
        or parsed.username
        or parsed.password
        or parsed.path
        or parsed.query
        or parsed.fragment
    ):
        return None
    normalized_host = hostname.rstrip(".")
    return normalized_host if port is None else f"{normalized_host}:{port}"


def _network_allowlist(
    value: str, setting_name: str
) -> tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...]:
    networks: list[ipaddress.IPv4Network | ipaddress.IPv6Network] = []
    for raw in value.split(","):
        candidate = raw.strip()
        if not candidate:
            continue
        try:
            networks.append(ipaddress.ip_network(candidate, strict=False))
        except ValueError as error:
            raise RuntimeError(f"{setting_name} contains an invalid IP or CIDR") from error
    return tuple(networks)


def _validate_trusted_proxy_networks(
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> None:
    if not networks or any(
        network.prefixlen != network.max_prefixlen
        or not network.network_address.is_loopback
        for network in networks
    ):
        raise RuntimeError(
            "TMCRA_TRUSTED_PROXY_IPS must contain loopback host routes only "
            "(/32 for IPv4 or /128 for IPv6)"
        )


def _validate_internal_allowed_networks(
    networks: tuple[ipaddress.IPv4Network | ipaddress.IPv6Network, ...],
) -> None:
    if any(network.prefixlen != network.max_prefixlen for network in networks):
        raise RuntimeError(
            "TMCRA_INTERNAL_ALLOWED_IPS must contain exact host routes only "
            "(/32 for IPv4 or /128 for IPv6)"
        )


UPSTREAM_HOST = os.environ.get("TMCRA_UPSTREAM_HOST", "127.0.0.1")
UPSTREAM_PORT = int(os.environ.get("TMCRA_UPSTREAM_PORT", "2001"))
PUBLIC_PORT = int(os.environ.get("TMCRA_PUBLIC_PORT", "2000"))
INTERNAL_BOOTSTRAP_OWNER_EMAIL = os.environ.get(
    "TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL", ""
)
INTERNAL_ALLOWED_NETWORKS = _network_allowlist(
    os.environ.get("TMCRA_INTERNAL_ALLOWED_IPS", ""), "TMCRA_INTERNAL_ALLOWED_IPS"
)
TRUSTED_PROXY_NETWORKS = _network_allowlist(
    os.environ.get("TMCRA_TRUSTED_PROXY_IPS", "127.0.0.1/32,::1/128"),
    "TMCRA_TRUSTED_PROXY_IPS",
)
ACCOUNT_DATABASE = os.environ.get(
    "TMCRA_ACCOUNT_DATABASE",
    "/opt/tmcra/tmcra-official/shared/auth/accounts.sqlite3",
)
DOWNLOAD_ROOT = os.environ.get(
    "TMCRA_DOWNLOAD_ROOT",
    "/opt/tmcra/tmcra-official/shared/downloads",
)
SESSION_SECRET = os.environ.get("TMCRA_SESSION_SECRET", "")
PUBLIC_HOSTS = frozenset(
    normalized
    for host in os.environ.get(
        "TMCRA_PUBLIC_HOSTS",
        "tmcra.com,www.tmcra.com,euvbyqa1jpvdm7yq-2000.sc01-webservice.gpuhome.cc:8443",
    ).split(",")
    if (normalized := _normalized_authority(host))
)

MAXIMUM_REQUEST_BYTES = 2 * 1024 * 1024
MAXIMUM_AUTH_FORM_BYTES = 16 * 1024
PASSWORD_ITERATIONS = int(os.environ.get("TMCRA_PASSWORD_ITERATIONS", "600000"))
SESSION_IDLE_SECONDS = int(os.environ.get("TMCRA_SESSION_IDLE_SECONDS", "43200"))
SESSION_ABSOLUTE_SECONDS = int(
    os.environ.get("TMCRA_SESSION_ABSOLUTE_SECONDS", "604800")
)
SESSION_ROTATE_SECONDS = int(os.environ.get("TMCRA_SESSION_ROTATE_SECONDS", "3600"))
CSRF_LIFETIME_SECONDS = 20 * 60
STRICT_TRANSPORT_SECURITY = "max-age=31536000; includeSubDomains"
DOWNLOAD_FILENAME = "TMCRA-Memory-Setup-latest.exe"
DOWNLOAD_REQUEST_PATH = f"/downloads/{DOWNLOAD_FILENAME}"
DOWNLOAD_CONTENT_TYPE = "application/vnd.microsoft.portable-executable"
DOWNLOAD_BUFFER_BYTES = 64 * 1024
DOWNLOAD_CACHE_CONTROL = "public, max-age=0, must-revalidate"
DESKTOP_UPDATE_REQUEST_PREFIX = "/downloads/desktop/windows/x64/"
DESKTOP_UPDATE_STORAGE_PREFIX = "desktop/windows/x64"
DESKTOP_UPDATE_METADATA_FILENAME = "latest.yml"
DESKTOP_UPDATE_FILENAME_PATTERN = re.compile(
    r"TMCRA-Memory-Setup-(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?-x64\.exe(?:\.blockmap)?\Z"
)
DESKTOP_CLIENT_HEADER = "X-TMCRA-Desktop-Client"
DESKTOP_CLIENT_PATTERN = re.compile(
    r"com\.tmcra\.memory/(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?\Z"
)
DESKTOP_UPDATE_METADATA_CONTENT_TYPE = "application/x-yaml; charset=utf-8"
DESKTOP_UPDATE_BLOCKMAP_CONTENT_TYPE = "application/octet-stream"
DESKTOP_UPDATE_IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"
MAC_DOWNLOAD_CONTENT_TYPE = "application/x-apple-diskimage"
MAC_ZIP_CONTENT_TYPE = "application/zip"
MAC_DOWNLOAD_ROUTES = {
    f"/downloads/TMCRA-Memory-latest-{architecture}.dmg": (
        f"TMCRA-Memory-latest-{architecture}.dmg",
        MAC_DOWNLOAD_CONTENT_TYPE,
    )
    for architecture in ("x64", "arm64")
}
MAC_DESKTOP_UPDATE_ROUTES = tuple(
    {
        "architecture": architecture,
        "request_prefix": f"/downloads/desktop/macos/{architecture}/",
        "storage_prefix": f"desktop/macos/{architecture}",
        "metadata": "latest-mac.yml",
        "artifact_pattern": re.compile(
            rf"TMCRA-Memory-(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
            rf"(?:-[0-9A-Za-z.-]+)?-{architecture}\.(?:dmg|zip)(?:\.blockmap)?\Z"
        ),
    }
    for architecture in ("x64", "arm64")
)
RELEASE_ID = os.environ.get("TMCRA_RELEASE_ID", "")
PUBLIC_BASE_URL = os.environ.get("TMCRA_PUBLIC_BASE_URL", "https://tmcra.com").rstrip("/")
MAIL_TRANSPORT = os.environ.get("TMCRA_MAIL_TRANSPORT", "disabled").strip().lower()
MAIL_FROM_EMAIL = os.environ.get("TMCRA_MAIL_FROM_EMAIL", "").strip()
MAIL_FROM_NAME = os.environ.get("TMCRA_MAIL_FROM_NAME", "TMCRA").strip() or "TMCRA"
MAIL_REPLY_TO = os.environ.get("TMCRA_MAIL_REPLY_TO", "").strip()
SMTP_HOST = os.environ.get("TMCRA_SMTP_HOST", "").strip()
SMTP_PORT = int(os.environ.get("TMCRA_SMTP_PORT", "465"))
SMTP_SECURITY = os.environ.get("TMCRA_SMTP_SECURITY", "smtps").strip().lower()
SMTP_USERNAME = os.environ.get("TMCRA_SMTP_USERNAME", "").strip()
SMTP_PASSWORD = os.environ.get("TMCRA_SMTP_PASSWORD", "")
SMTP_TIMEOUT_SECONDS = int(os.environ.get("TMCRA_SMTP_TIMEOUT_SECONDS", "15"))
MARKETING_ENABLED = os.environ.get("TMCRA_MARKETING_ENABLED", "false").strip().lower() in (
    "1",
    "true",
    "yes",
)
MARKETING_MAIL_TRANSPORT = os.environ.get(
    "TMCRA_MARKETING_MAIL_TRANSPORT", "disabled"
).strip().lower()
MARKETING_MAIL_FROM_EMAIL = os.environ.get(
    "TMCRA_MARKETING_MAIL_FROM_EMAIL", ""
).strip()
MARKETING_MAIL_FROM_NAME = (
    os.environ.get("TMCRA_MARKETING_MAIL_FROM_NAME", "TMCRA").strip() or "TMCRA"
)
MARKETING_MAIL_REPLY_TO = os.environ.get(
    "TMCRA_MARKETING_MAIL_REPLY_TO", ""
).strip()
MARKETING_SMTP_HOST = os.environ.get("TMCRA_MARKETING_SMTP_HOST", "").strip()
MARKETING_SMTP_PORT = int(os.environ.get("TMCRA_MARKETING_SMTP_PORT", "465"))
MARKETING_SMTP_SECURITY = os.environ.get(
    "TMCRA_MARKETING_SMTP_SECURITY", "smtps"
).strip().lower()
MARKETING_SMTP_USERNAME = os.environ.get(
    "TMCRA_MARKETING_SMTP_USERNAME", ""
).strip()
MARKETING_SMTP_PASSWORD = os.environ.get("TMCRA_MARKETING_SMTP_PASSWORD", "")
MARKETING_SMTP_TIMEOUT_SECONDS = int(
    os.environ.get("TMCRA_MARKETING_SMTP_TIMEOUT_SECONDS", "15")
)
MARKETING_API_TOKEN = os.environ.get("TMCRA_MARKETING_API_TOKEN", "")
MARKETING_SEND_INTERVAL_SECONDS = float(
    os.environ.get("TMCRA_MARKETING_SEND_INTERVAL_SECONDS", "0.25")
)
MARKETING_MAX_ATTEMPTS = int(os.environ.get("TMCRA_MARKETING_MAX_ATTEMPTS", "3"))
GOOGLE_CLIENT_ID = os.environ.get("TMCRA_GOOGLE_CLIENT_ID", "").strip()
GOOGLE_CLIENT_SECRET = os.environ.get("TMCRA_GOOGLE_CLIENT_SECRET", "")
GOOGLE_REDIRECT_URI = os.environ.get(
    "TMCRA_GOOGLE_REDIRECT_URI", f"{PUBLIC_BASE_URL}/oauth/google/callback"
).strip()
# Keep Google OAuth unreachable in this release even if credentials are
# accidentally left in the environment. Remove this gate only after the
# account-linking flow has completed its dedicated security review.
GOOGLE_OAUTH_RELEASE_ENABLED = False

SESSION_COOKIE = "__Host-tmcra_session"
CSRF_COOKIE = "__Host-tmcra_auth_csrf"
OAUTH_STATE_COOKIE = "__Host-tmcra_oauth_state"
COOKIE_FLAGS = "Path=/; Secure; HttpOnly; SameSite=Lax"
DEFAULT_ACCOUNT_RETURN_TO = "/console"
EMAIL_TOKEN_SECONDS = int(os.environ.get("TMCRA_EMAIL_TOKEN_SECONDS", "600"))
PASSWORD_RESET_SECONDS = int(os.environ.get("TMCRA_PASSWORD_RESET_SECONDS", "1800"))
MAXIMUM_EMAIL_API_BYTES = 256 * 1024
MAXIMUM_MARKETING_SUBJECT_CHARACTERS = 160
MAXIMUM_MARKETING_TEXT_CHARACTERS = 100_000
MAXIMUM_MARKETING_HTML_CHARACTERS = 200_000
OAUTH_STATE_SECONDS = 10 * 60
INTERNAL_GATEWAY_AUDIT_RETENTION_SECONDS = 30 * 24 * 60 * 60
INTERNAL_GATEWAY_AUDIT_MAX_ROWS = 20_000
INTERNAL_GATEWAY_AUDIT_UNAUTH_WINDOW_SECONDS = 60
INTERNAL_GATEWAY_AUDIT_UNAUTH_PER_CLIENT_WRITES = 4
INTERNAL_GATEWAY_AUDIT_UNAUTH_GLOBAL_WRITES = 120
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_HOST = "oauth2.googleapis.com"
GOOGLE_TOKEN_PATH = "/token"
GOOGLE_USERINFO_HOST = "openidconnect.googleapis.com"
GOOGLE_USERINFO_PATH = "/v1/userinfo"

INTERNAL_PREFIXES = ("/internal", "/api/internal")
CUSTOMER_WEB_PREFIXES = (
    "/console",
    "/personal",
    "/enterprise",
    "/account-setup",
)
CUSTOMER_API_PREFIXES = (
    "/api/account",
    "/api/console",
    "/api/enterprise",
    "/api/personal",
)
ANONYMOUS_DEVICE_ENDPOINTS = frozenset(
    {
        "/api/device/v1/authorizations",
        "/api/device/v1/token",
    }
)
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}
PROXY_CONTROL_HEADERS = {
    "cf-connecting-ip",
    "cf-ray",
    "tencent-acceleration-domain",
    "x-forwarded-for",
    "x-forwarded-host",
    "x-forwarded-proto",
    "x-tmcra-client-ip",
    "x-tmcra-public-host",
}
EMAIL_LOCAL_CHARACTERS = frozenset(
    "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789.!#$%&'*+/=?^_`{|}~-"
)
_SCHEMA_LOCK = threading.Lock()
_READY_DATABASE_PATH: str | None = None
_DOWNLOAD_ETAG_LOCK = threading.Lock()
_DOWNLOAD_ETAG_CACHE: tuple[tuple[int, int, int, int], str] | None = None
_MARKETING_WAKE_EVENT = threading.Event()
_MARKETING_STOP_EVENT = threading.Event()
_MARKETING_WORKER: threading.Thread | None = None
_CAMPAIGN_IDEMPOTENCY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
_INTERNAL_AUDIT_RATE_LOCK = threading.Lock()
_INTERNAL_AUDIT_RATE_BUCKET: int | None = None
_INTERNAL_AUDIT_RATE_GLOBAL_WRITES = 0
_INTERNAL_AUDIT_RATE_CLIENT_WRITES: dict[tuple[str, str, str], int] = {}


def _parse_single_byte_range(value: str, size: int) -> tuple[int, int]:
    """Return an inclusive byte range or raise ValueError for an invalid range."""

    unit, separator, specification = value.strip().partition("=")
    if separator != "=" or unit.strip().lower() != "bytes" or "," in specification:
        raise ValueError("unsupported range")
    start_text, dash, end_text = specification.strip().partition("-")
    if dash != "-" or "-" in end_text:
        raise ValueError("invalid range")

    def ascii_digits(candidate: str) -> bool:
        return bool(candidate) and all("0" <= character <= "9" for character in candidate)

    if start_text:
        if not ascii_digits(start_text) or (end_text and not ascii_digits(end_text)):
            raise ValueError("invalid range")
        start = int(start_text)
        end = int(end_text) if end_text else size - 1
        if start >= size or end < start:
            raise ValueError("unsatisfiable range")
        return start, min(end, size - 1)

    if not ascii_digits(end_text):
        raise ValueError("invalid suffix range")
    suffix_length = int(end_text)
    if suffix_length <= 0 or size <= 0:
        raise ValueError("unsatisfiable suffix range")
    return max(size - suffix_length, 0), size - 1


def _download_etag(download: object, metadata: os.stat_result) -> str:
    """Return a content-derived strong ETag, cached for one immutable inode."""

    global _DOWNLOAD_ETAG_CACHE
    cache_key = (
        metadata.st_dev,
        metadata.st_ino,
        metadata.st_size,
        metadata.st_mtime_ns,
    )
    with _DOWNLOAD_ETAG_LOCK:
        if _DOWNLOAD_ETAG_CACHE and _DOWNLOAD_ETAG_CACHE[0] == cache_key:
            return _DOWNLOAD_ETAG_CACHE[1]
        digest = hashlib.sha256()
        download.seek(0)
        while chunk := download.read(1024 * 1024):
            digest.update(chunk)
        download.seek(0)
        etag = f'"sha256-{digest.hexdigest()}"'
        _DOWNLOAD_ETAG_CACHE = (cache_key, etag)
        return etag


def _mail_configured() -> bool:
    return MAIL_TRANSPORT == "smtp"


def _marketing_mail_configured() -> bool:
    return MARKETING_ENABLED and MARKETING_MAIL_TRANSPORT == "smtp"


def _google_configured() -> bool:
    return GOOGLE_OAUTH_RELEASE_ENABLED and bool(
        GOOGLE_CLIENT_ID and GOOGLE_CLIENT_SECRET
    )


def _send_smtp_email(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str,
    *,
    from_email: str,
    from_name: str,
    reply_to: str,
    smtp_host: str,
    smtp_port: int,
    smtp_security: str,
    smtp_username: str,
    smtp_password: str,
    timeout_seconds: int,
    headers: dict[str, str] | None = None,
) -> None:
    message = EmailMessage()
    message["From"] = formataddr((from_name, from_email))
    message["To"] = recipient
    message["Subject"] = subject
    if reply_to:
        message["Reply-To"] = reply_to
    for name, value in (headers or {}).items():
        message[name] = value
    message.set_content(text_body)
    message.add_alternative(html_body, subtype="html")
    context = ssl.create_default_context()
    try:
        if smtp_security == "smtps":
            client = smtplib.SMTP_SSL(
                smtp_host, smtp_port, timeout=timeout_seconds, context=context
            )
        else:
            client = smtplib.SMTP(smtp_host, smtp_port, timeout=timeout_seconds)
        with client:
            client.ehlo()
            if smtp_security == "starttls":
                client.starttls(context=context)
                client.ehlo()
            client.login(smtp_username, smtp_password)
            client.send_message(message)
    except (OSError, smtplib.SMTPException, ValueError) as error:
        raise EmailDeliveryError("mail_delivery_failed") from error


def _send_transactional_email(
    recipient: str, subject: str, text_body: str, html_body: str
) -> None:
    if not _mail_configured():
        raise EmailDeliveryError("mail_transport_unavailable")
    _send_smtp_email(
        recipient,
        subject,
        text_body,
        html_body,
        from_email=MAIL_FROM_EMAIL,
        from_name=MAIL_FROM_NAME,
        reply_to=MAIL_REPLY_TO,
        smtp_host=SMTP_HOST,
        smtp_port=SMTP_PORT,
        smtp_security=SMTP_SECURITY,
        smtp_username=SMTP_USERNAME,
        smtp_password=SMTP_PASSWORD,
        timeout_seconds=SMTP_TIMEOUT_SECONDS,
    )


def _branded_code_email_html(
    *,
    recipient_name: str,
    preheader: str,
    eyebrow: str,
    title: str,
    title_zh: str,
    explanation: str,
    explanation_zh: str,
    code: str,
    expires_minutes: int,
) -> str:
    safe_name = html.escape(recipient_name)
    safe_preheader = html.escape(preheader)
    safe_eyebrow = html.escape(eyebrow)
    safe_title = html.escape(title)
    safe_title_zh = html.escape(title_zh)
    safe_explanation = html.escape(explanation)
    safe_explanation_zh = html.escape(explanation_zh)
    safe_code = html.escape(code)
    logo_url = html.escape(
        f"{PUBLIC_BASE_URL}/brand/tmcra-logo.png", quote=True
    )
    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <title>{safe_title}</title>
  </head>
  <body style="margin:0;background:#f3f5f4;color:#17201d;font-family:Arial,'Noto Sans SC','Microsoft YaHei',sans-serif;">
    <div style="display:none;max-height:0;overflow:hidden;opacity:0;color:transparent;">{safe_preheader}</div>
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f3f5f4;">
      <tr>
        <td align="center" style="padding:32px 16px;">
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="max-width:600px;background:#ffffff;border:1px solid #dce3df;border-radius:8px;overflow:hidden;">
            <tr>
              <td style="padding:30px 36px 24px;border-bottom:1px solid #e4e9e6;">
                <img src="{logo_url}" width="150" alt="TMCRA" style="display:block;width:150px;max-width:100%;height:auto;border:0;">
              </td>
            </tr>
            <tr>
              <td style="padding:34px 36px 38px;">
                <p style="margin:0 0 12px;color:#176e57;font-size:12px;font-weight:700;line-height:18px;">{safe_eyebrow}</p>
                <h1 style="margin:0;color:#111714;font-size:28px;font-weight:750;line-height:36px;">{safe_title}</h1>
                <p style="margin:4px 0 24px;color:#53605a;font-size:17px;font-weight:600;line-height:26px;">{safe_title_zh}</p>
                <p style="margin:0 0 14px;color:#303a35;font-size:15px;line-height:24px;">Hello {safe_name}, / 你好，{safe_name}：</p>
                <p style="margin:0;color:#53605a;font-size:14px;line-height:23px;">{safe_explanation}<br>{safe_explanation_zh}</p>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="margin:24px 0;">
                  <tr>
                    <td align="center" style="padding:20px;border:1px solid #b9d9ce;border-radius:6px;background:#eef8f4;color:#10261f;font-family:Consolas,'Courier New',monospace;font-size:34px;font-weight:700;line-height:42px;">{safe_code}</td>
                  </tr>
                </table>
                <p style="margin:0 0 22px;color:#53605a;font-size:13px;line-height:21px;">Valid for {expires_minutes} minutes. / 验证码将在 {expires_minutes} 分钟后失效。</p>
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0" border="0" style="background:#f7f8f7;border-left:3px solid #56635d;">
                  <tr>
                    <td style="padding:14px 16px;color:#4b5751;font-size:12px;line-height:19px;">TMCRA staff will never ask you for this code. If you did not request it, you can ignore this email.<br>TMCRA 工作人员不会向你索要验证码。如非本人操作，请忽略此邮件。</td>
                  </tr>
                </table>
              </td>
            </tr>
            <tr>
              <td style="padding:18px 36px;background:#101512;color:#aeb8b3;font-size:11px;line-height:18px;">TMCRA Account Security · Automated message<br>TMCRA 账户安全 · 此邮件由系统自动发送</td>
            </tr>
          </table>
        </td>
      </tr>
    </table>
  </body>
</html>"""


def _send_marketing_email(
    recipient: str,
    subject: str,
    text_body: str,
    html_body: str,
    unsubscribe_url: str,
) -> None:
    if not _marketing_mail_configured():
        raise EmailDeliveryError("marketing_mail_transport_unavailable")
    _send_smtp_email(
        recipient,
        subject,
        text_body,
        html_body,
        from_email=MARKETING_MAIL_FROM_EMAIL,
        from_name=MARKETING_MAIL_FROM_NAME,
        reply_to=MARKETING_MAIL_REPLY_TO,
        smtp_host=MARKETING_SMTP_HOST,
        smtp_port=MARKETING_SMTP_PORT,
        smtp_security=MARKETING_SMTP_SECURITY,
        smtp_username=MARKETING_SMTP_USERNAME,
        smtp_password=MARKETING_SMTP_PASSWORD,
        timeout_seconds=MARKETING_SMTP_TIMEOUT_SECONDS,
        headers={
            "List-Unsubscribe": f"<{unsubscribe_url}>",
            "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
            "Precedence": "bulk",
        },
    )


def _google_identity_from_code(code: str, code_verifier: str) -> dict[str, str]:
    form = urllib.parse.urlencode(
        {
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "code": code,
            "code_verifier": code_verifier,
            "grant_type": "authorization_code",
            "redirect_uri": GOOGLE_REDIRECT_URI,
        }
    ).encode("ascii")
    connection = http.client.HTTPSConnection(
        GOOGLE_TOKEN_HOST, 443, timeout=SMTP_TIMEOUT_SECONDS, context=ssl.create_default_context()
    )
    try:
        connection.request(
            "POST",
            GOOGLE_TOKEN_PATH,
            body=form,
            headers={
                "Accept": "application/json",
                "Content-Type": "application/x-www-form-urlencoded",
                "Content-Length": str(len(form)),
            },
        )
        response = connection.getresponse()
        payload = response.read(1_048_577)
        if response.status != 200 or len(payload) > 1_048_576:
            raise OAuthProviderError("google_token_exchange_failed")
        token = json.loads(payload)
    except (OSError, http.client.HTTPException, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OAuthProviderError("google_token_exchange_failed") from error
    finally:
        connection.close()
    access_token = token.get("access_token") if isinstance(token, dict) else None
    token_type = token.get("token_type") if isinstance(token, dict) else None
    if (
        not isinstance(access_token, str)
        or not access_token
        or len(access_token) > 8192
        or any(ord(character) < 33 or ord(character) > 126 for character in access_token)
        or str(token_type).casefold() != "bearer"
    ):
        raise OAuthProviderError("google_token_exchange_failed")

    connection = http.client.HTTPSConnection(
        GOOGLE_USERINFO_HOST,
        443,
        timeout=SMTP_TIMEOUT_SECONDS,
        context=ssl.create_default_context(),
    )
    try:
        connection.request(
            "GET",
            GOOGLE_USERINFO_PATH,
            headers={"Accept": "application/json", "Authorization": f"Bearer {access_token}"},
        )
        response = connection.getresponse()
        payload = response.read(1_048_577)
        if response.status != 200 or len(payload) > 1_048_576:
            raise OAuthProviderError("google_userinfo_failed")
        profile = json.loads(payload)
    except (OSError, http.client.HTTPException, UnicodeDecodeError, json.JSONDecodeError) as error:
        raise OAuthProviderError("google_userinfo_failed") from error
    finally:
        connection.close()
    if not isinstance(profile, dict) or profile.get("email_verified") is not True:
        raise OAuthProviderError("google_email_not_verified")
    subject = profile.get("sub")
    email_value = profile.get("email")
    name = profile.get("name")
    if not isinstance(subject, str) or not subject or len(subject) > 255:
        raise OAuthProviderError("google_identity_invalid")
    if not isinstance(email_value, str):
        raise OAuthProviderError("google_identity_invalid")
    if not isinstance(name, str) or not name.strip():
        name = email_value.split("@", 1)[0]
    return {"subject": subject, "email": email_value, "full_name": name}


def _validate_smtp_settings(
    label: str,
    transport: str,
    from_email: str,
    from_name: str,
    reply_to: str,
    host: str,
    port: int,
    security: str,
    username: str,
    password: str,
    timeout_seconds: int,
) -> None:
    if transport not in ("disabled", "smtp"):
        raise RuntimeError(f"{label} mail transport must be disabled or smtp")
    if transport != "smtp":
        return
    if not all((from_email, host, username, password)):
        raise RuntimeError(f"{label} SMTP configuration is incomplete")
    if security not in ("smtps", "starttls") or not 1 <= port <= 65535:
        raise RuntimeError(f"{label} SMTP security or port configuration is invalid")
    try:
        normalized_from = TmcraProxyHandler._normalize_email(from_email)
    except AuthInputError as error:
        raise RuntimeError(f"{label} sender email is invalid") from error
    if normalized_from != from_email or any(
        unicodedata.category(character).startswith("C") for character in from_name
    ):
        raise RuntimeError(f"{label} SMTP sender identity is invalid")
    if reply_to:
        try:
            normalized_reply_to = TmcraProxyHandler._normalize_email(reply_to)
        except AuthInputError as error:
            raise RuntimeError(f"{label} reply-to email is invalid") from error
        if normalized_reply_to != reply_to:
            raise RuntimeError(f"{label} reply-to email must be normalized")
    if not 2 <= timeout_seconds <= 60:
        raise RuntimeError(f"{label} SMTP timeout must be between 2 and 60 seconds")


def require_configuration() -> None:
    _validate_internal_allowed_networks(INTERNAL_ALLOWED_NETWORKS)
    _validate_trusted_proxy_networks(TRUSTED_PROXY_NETWORKS)
    missing = [
        name
        for name, value in (
            (
                "TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL",
                INTERNAL_BOOTSTRAP_OWNER_EMAIL,
            ),
            ("TMCRA_SESSION_SECRET", SESSION_SECRET),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f"missing proxy configuration: {', '.join(missing)}")
    if len(SESSION_SECRET.encode("utf-8")) < 32:
        raise RuntimeError("TMCRA_SESSION_SECRET must contain at least 32 bytes")
    try:
        normalized_owner = TmcraProxyHandler._normalize_email(
            INTERNAL_BOOTSTRAP_OWNER_EMAIL
        )
    except AuthInputError as error:
        raise RuntimeError("TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL is invalid") from error
    if normalized_owner != INTERNAL_BOOTSTRAP_OWNER_EMAIL:
        raise RuntimeError("TMCRA_INTERNAL_BOOTSTRAP_OWNER_EMAIL must be normalized")
    if PASSWORD_ITERATIONS < 310_000:
        raise RuntimeError("TMCRA_PASSWORD_ITERATIONS must be at least 310000")
    if SESSION_IDLE_SECONDS < 300 or SESSION_ABSOLUTE_SECONDS < SESSION_IDLE_SECONDS:
        raise RuntimeError("invalid session lifetime configuration")
    try:
        public_base = urllib.parse.urlsplit(PUBLIC_BASE_URL)
    except ValueError as error:
        raise RuntimeError("TMCRA_PUBLIC_BASE_URL is invalid") from error
    if (
        public_base.scheme != "https"
        or not public_base.hostname
        or public_base.username
        or public_base.password
        or public_base.query
        or public_base.fragment
        or public_base.path not in ("", "/")
        or _normalized_authority(public_base.netloc) not in PUBLIC_HOSTS
    ):
        raise RuntimeError("TMCRA_PUBLIC_BASE_URL must be a trusted HTTPS origin")
    _validate_smtp_settings(
        "transactional",
        MAIL_TRANSPORT,
        MAIL_FROM_EMAIL,
        MAIL_FROM_NAME,
        MAIL_REPLY_TO,
        SMTP_HOST,
        SMTP_PORT,
        SMTP_SECURITY,
        SMTP_USERNAME,
        SMTP_PASSWORD,
        SMTP_TIMEOUT_SECONDS,
    )
    _validate_smtp_settings(
        "marketing",
        MARKETING_MAIL_TRANSPORT,
        MARKETING_MAIL_FROM_EMAIL,
        MARKETING_MAIL_FROM_NAME,
        MARKETING_MAIL_REPLY_TO,
        MARKETING_SMTP_HOST,
        MARKETING_SMTP_PORT,
        MARKETING_SMTP_SECURITY,
        MARKETING_SMTP_USERNAME,
        MARKETING_SMTP_PASSWORD,
        MARKETING_SMTP_TIMEOUT_SECONDS,
    )
    if MARKETING_ENABLED:
        if MARKETING_MAIL_TRANSPORT != "smtp":
            raise RuntimeError("marketing email is enabled without an SMTP transport")
        if len(MARKETING_API_TOKEN.encode("utf-8")) < 32:
            raise RuntimeError("TMCRA_MARKETING_API_TOKEN must contain at least 32 bytes")
        if not 0.05 <= MARKETING_SEND_INTERVAL_SECONDS <= 60:
            raise RuntimeError("marketing send interval must be between 0.05 and 60 seconds")
        if not 1 <= MARKETING_MAX_ATTEMPTS <= 10:
            raise RuntimeError("marketing maximum attempts must be between 1 and 10")
    if bool(GOOGLE_CLIENT_ID) != bool(GOOGLE_CLIENT_SECRET):
        raise RuntimeError("Google OAuth client ID and secret must be configured together")
    if _google_configured() and GOOGLE_REDIRECT_URI != f"{PUBLIC_BASE_URL}/oauth/google/callback":
        raise RuntimeError("TMCRA_GOOGLE_REDIRECT_URI must use the canonical callback URL")
    if EMAIL_TOKEN_SECONDS < 300 or PASSWORD_RESET_SECONDS < 300:
        raise RuntimeError("email token lifetimes must be at least five minutes")
    _ensure_schema()


@contextmanager
def _database():
    _ensure_schema()
    connection = sqlite3.connect(ACCOUNT_DATABASE, timeout=10, isolation_level=None)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA busy_timeout = 10000")
    try:
        yield connection
    finally:
        connection.close()


def _prune_internal_gateway_audit(
    connection: sqlite3.Connection, now: int
) -> None:
    cutoff = now - INTERNAL_GATEWAY_AUDIT_RETENTION_SECONDS
    connection.execute(
        "DELETE FROM internal_gateway_audit WHERE occurred_at < ?", (cutoff,)
    )
    row = connection.execute(
        "SELECT COUNT(*) FROM internal_gateway_audit"
    ).fetchone()
    overflow = max(0, int(row[0] if row else 0) - INTERNAL_GATEWAY_AUDIT_MAX_ROWS)
    if overflow:
        connection.execute(
            """DELETE FROM internal_gateway_audit
               WHERE id IN (
                   SELECT id FROM internal_gateway_audit
                   ORDER BY occurred_at ASC, id ASC
                   LIMIT ?
               )""",
            (overflow,),
        )


def _allow_unauthenticated_internal_audit_write(
    client_ip_hash: str, event_type: str, outcome: str, now: int
) -> tuple[bool, bool]:
    global _INTERNAL_AUDIT_RATE_BUCKET
    global _INTERNAL_AUDIT_RATE_GLOBAL_WRITES

    bucket = now // INTERNAL_GATEWAY_AUDIT_UNAUTH_WINDOW_SECONDS
    key = (client_ip_hash, event_type, outcome)
    with _INTERNAL_AUDIT_RATE_LOCK:
        if _INTERNAL_AUDIT_RATE_BUCKET != bucket:
            _INTERNAL_AUDIT_RATE_BUCKET = bucket
            _INTERNAL_AUDIT_RATE_GLOBAL_WRITES = 0
            _INTERNAL_AUDIT_RATE_CLIENT_WRITES.clear()
        client_writes = _INTERNAL_AUDIT_RATE_CLIENT_WRITES.get(key, 0)
        if (
            client_writes >= INTERNAL_GATEWAY_AUDIT_UNAUTH_PER_CLIENT_WRITES
            or _INTERNAL_AUDIT_RATE_GLOBAL_WRITES
            >= INTERNAL_GATEWAY_AUDIT_UNAUTH_GLOBAL_WRITES
        ):
            return False, True
        client_writes += 1
        _INTERNAL_AUDIT_RATE_CLIENT_WRITES[key] = client_writes
        _INTERNAL_AUDIT_RATE_GLOBAL_WRITES += 1
        limit_reached = (
            client_writes >= INTERNAL_GATEWAY_AUDIT_UNAUTH_PER_CLIENT_WRITES
            or _INTERNAL_AUDIT_RATE_GLOBAL_WRITES
            >= INTERNAL_GATEWAY_AUDIT_UNAUTH_GLOBAL_WRITES
        )
        return True, limit_reached


def _reset_internal_gateway_audit_rate_limit() -> None:
    global _INTERNAL_AUDIT_RATE_BUCKET
    global _INTERNAL_AUDIT_RATE_GLOBAL_WRITES

    with _INTERNAL_AUDIT_RATE_LOCK:
        _INTERNAL_AUDIT_RATE_BUCKET = None
        _INTERNAL_AUDIT_RATE_GLOBAL_WRITES = 0
        _INTERNAL_AUDIT_RATE_CLIENT_WRITES.clear()


def _ensure_schema() -> None:
    global _READY_DATABASE_PATH
    database_path = str(Path(ACCOUNT_DATABASE).resolve())
    if _READY_DATABASE_PATH == database_path:
        return
    with _SCHEMA_LOCK:
        if _READY_DATABASE_PATH == database_path:
            return
        path = Path(database_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path, timeout=10)
        try:
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS account_users (
                    id TEXT PRIMARY KEY,
                    email TEXT NOT NULL UNIQUE,
                    full_name TEXT,
                    password_hash BLOB NOT NULL,
                    password_salt BLOB NOT NULL,
                    password_iterations INTEGER NOT NULL,
                    password_enabled INTEGER NOT NULL DEFAULT 1,
                    email_verified_at INTEGER,
                    marketing_opt_in_at INTEGER,
                    marketing_opt_out_at INTEGER,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS account_sessions (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                    created_at INTEGER NOT NULL,
                    last_seen_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    absolute_expires_at INTEGER NOT NULL
                );
                CREATE INDEX IF NOT EXISTS account_sessions_user_idx
                    ON account_sessions(user_id);
                CREATE INDEX IF NOT EXISTS account_sessions_expiry_idx
                    ON account_sessions(expires_at);
                CREATE TABLE IF NOT EXISTS account_rate_limits (
                    limit_key TEXT NOT NULL,
                    bucket_start INTEGER NOT NULL,
                    request_count INTEGER NOT NULL,
                    PRIMARY KEY (limit_key, bucket_start)
                );
                CREATE TABLE IF NOT EXISTS account_email_tokens (
                    token_hash TEXT PRIMARY KEY,
                    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                    purpose TEXT NOT NULL CHECK(purpose IN ('verify_email', 'reset_password')),
                    return_to TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL,
                    consumed_at INTEGER,
                    attempt_count INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS account_email_tokens_user_idx
                    ON account_email_tokens(user_id, purpose, expires_at);
                CREATE TABLE IF NOT EXISTS account_oauth_identities (
                    provider TEXT NOT NULL,
                    provider_subject TEXT NOT NULL,
                    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    PRIMARY KEY (provider, provider_subject),
                    UNIQUE(provider, user_id)
                );
                CREATE TABLE IF NOT EXISTS account_oauth_states (
                    state_hash TEXT PRIMARY KEY,
                    return_to TEXT NOT NULL,
                    code_verifier TEXT NOT NULL,
                    created_at INTEGER NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS email_campaigns (
                    id TEXT PRIMARY KEY,
                    idempotency_key TEXT NOT NULL UNIQUE,
                    subject TEXT NOT NULL,
                    text_body TEXT NOT NULL,
                    html_body TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(
                        state IN ('queued', 'running', 'completed', 'completed_with_errors')
                    ),
                    requested_by TEXT NOT NULL,
                    recipient_count INTEGER NOT NULL DEFAULT 0,
                    sent_count INTEGER NOT NULL DEFAULT 0,
                    failed_count INTEGER NOT NULL DEFAULT 0,
                    skipped_count INTEGER NOT NULL DEFAULT 0,
                    created_at INTEGER NOT NULL,
                    started_at INTEGER,
                    completed_at INTEGER,
                    last_error TEXT
                );
                CREATE INDEX IF NOT EXISTS email_campaigns_created_idx
                    ON email_campaigns(created_at DESC);
                CREATE TABLE IF NOT EXISTS email_campaign_deliveries (
                    campaign_id TEXT NOT NULL REFERENCES email_campaigns(id) ON DELETE CASCADE,
                    user_id TEXT NOT NULL REFERENCES account_users(id) ON DELETE CASCADE,
                    email TEXT NOT NULL,
                    state TEXT NOT NULL CHECK(
                        state IN ('queued', 'sending', 'sent', 'failed', 'skipped')
                    ),
                    attempt_count INTEGER NOT NULL DEFAULT 0,
                    last_error TEXT,
                    created_at INTEGER NOT NULL,
                    updated_at INTEGER NOT NULL,
                    sent_at INTEGER,
                    PRIMARY KEY (campaign_id, user_id)
                );
                CREATE INDEX IF NOT EXISTS email_campaign_delivery_queue_idx
                    ON email_campaign_deliveries(state, updated_at, campaign_id);
                CREATE TABLE IF NOT EXISTS internal_gateway_audit (
                    id TEXT PRIMARY KEY,
                    occurred_at INTEGER NOT NULL,
                    client_ip_hash TEXT NOT NULL,
                    method TEXT NOT NULL,
                    path TEXT NOT NULL,
                    outcome TEXT NOT NULL CHECK(
                        outcome IN ('ip_denied', 'authentication_required', 'session_forwarded')
                    ),
                    account_user_id TEXT REFERENCES account_users(id) ON DELETE SET NULL,
                    event_type TEXT NOT NULL DEFAULT 'internal_access',
                    response_status INTEGER,
                    final_outcome TEXT,
                    rate_limited INTEGER NOT NULL DEFAULT 0
                );
                CREATE INDEX IF NOT EXISTS internal_gateway_audit_time_idx
                    ON internal_gateway_audit(occurred_at DESC);
                """
            )
            columns = {
                row[1] for row in connection.execute("PRAGMA table_info(account_users)")
            }
            if "password_enabled" not in columns:
                connection.execute(
                    "ALTER TABLE account_users ADD COLUMN password_enabled INTEGER NOT NULL DEFAULT 1"
                )
            if "email_verified_at" not in columns:
                connection.execute(
                    "ALTER TABLE account_users ADD COLUMN email_verified_at INTEGER"
                )
                connection.execute(
                    "UPDATE account_users SET email_verified_at = created_at WHERE email_verified_at IS NULL"
                )
            if "marketing_opt_in_at" not in columns:
                connection.execute(
                    "ALTER TABLE account_users ADD COLUMN marketing_opt_in_at INTEGER"
                )
            if "marketing_opt_out_at" not in columns:
                connection.execute(
                    "ALTER TABLE account_users ADD COLUMN marketing_opt_out_at INTEGER"
                )
            token_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(account_email_tokens)")
            }
            if "attempt_count" not in token_columns:
                connection.execute(
                    "ALTER TABLE account_email_tokens ADD COLUMN attempt_count INTEGER NOT NULL DEFAULT 0"
                )
            audit_columns = {
                row[1]
                for row in connection.execute(
                    "PRAGMA table_info(internal_gateway_audit)"
                )
            }
            if "event_type" not in audit_columns:
                connection.execute(
                    "ALTER TABLE internal_gateway_audit "
                    "ADD COLUMN event_type TEXT NOT NULL DEFAULT 'internal_access'"
                )
            if "response_status" not in audit_columns:
                connection.execute(
                    "ALTER TABLE internal_gateway_audit ADD COLUMN response_status INTEGER"
                )
            if "final_outcome" not in audit_columns:
                connection.execute(
                    "ALTER TABLE internal_gateway_audit ADD COLUMN final_outcome TEXT"
                )
            if "rate_limited" not in audit_columns:
                connection.execute(
                    "ALTER TABLE internal_gateway_audit "
                    "ADD COLUMN rate_limited INTEGER NOT NULL DEFAULT 0"
                )
            _prune_internal_gateway_audit(connection, int(time.time()))
            connection.commit()
        finally:
            connection.close()
        try:
            path.chmod(0o600)
        except OSError as error:
            raise RuntimeError("account database permissions could not be secured") from error
        _READY_DATABASE_PATH = database_path


def _marketing_unsubscribe_token(user_id: str) -> str:
    encoded = base64.urlsafe_b64encode(user_id.encode("utf-8")).decode("ascii").rstrip("=")
    signature = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        f"marketing-unsubscribe:{encoded}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    signed = base64.urlsafe_b64encode(signature).decode("ascii").rstrip("=")
    return f"{encoded}.{signed}"


def _marketing_unsubscribe_user(token: str) -> str:
    if not token or len(token) > 256 or token.count(".") != 1:
        raise AuthInputError("invalid_unsubscribe_token")
    encoded, signature = token.split(".", 1)
    expected = hmac.new(
        SESSION_SECRET.encode("utf-8"),
        f"marketing-unsubscribe:{encoded}".encode("utf-8"),
        hashlib.sha256,
    ).digest()
    expected_signature = base64.urlsafe_b64encode(expected).decode("ascii").rstrip("=")
    if not hmac.compare_digest(signature, expected_signature):
        raise AuthInputError("invalid_unsubscribe_token")
    try:
        padded = encoded + "=" * (-len(encoded) % 4)
        user_id = base64.b64decode(padded, altchars=b"-_", validate=True).decode("utf-8")
    except (ValueError, UnicodeDecodeError) as error:
        raise AuthInputError("invalid_unsubscribe_token") from error
    if not re.fullmatch(r"usr_[0-9a-f]{32}", user_id):
        raise AuthInputError("invalid_unsubscribe_token")
    now = int(time.time())
    with _database() as connection:
        cursor = connection.execute(
            "UPDATE account_users SET marketing_opt_out_at = ?, updated_at = ? WHERE id = ?",
            (now, now, user_id),
        )
        if cursor.rowcount != 1:
            raise AuthInputError("invalid_unsubscribe_token")
    return user_id


def _marketing_bodies(
    text_body: str, html_body: str, unsubscribe_url: str
) -> tuple[str, str]:
    safe_url = html.escape(unsubscribe_url, quote=True)
    text = (
        text_body.rstrip()
        + "\n\nYou are receiving this because you opted in to TMCRA updates. "
        + f"Unsubscribe: {unsubscribe_url}\n"
    )
    markup = (
        html_body.rstrip()
        + '<hr style="margin:32px 0 16px;border:0;border-top:1px solid #dfe2e0">'
        + '<p style="color:#68706c;font-size:12px;line-height:1.5">'
        + "You are receiving this because you opted in to TMCRA updates. "
        + f'<a href="{safe_url}">Unsubscribe</a>.</p>'
    )
    return text, markup


def _campaign_payload(row: sqlite3.Row) -> dict[str, object]:
    return {
        "id": row["id"],
        "idempotency_key": row["idempotency_key"],
        "state": row["state"],
        "recipient_count": int(row["recipient_count"]),
        "sent_count": int(row["sent_count"]),
        "failed_count": int(row["failed_count"]),
        "skipped_count": int(row["skipped_count"]),
        "created_at": int(row["created_at"]),
        "started_at": int(row["started_at"]) if row["started_at"] is not None else None,
        "completed_at": (
            int(row["completed_at"]) if row["completed_at"] is not None else None
        ),
    }


def _refresh_campaign_counters(connection: sqlite3.Connection, campaign_id: str) -> None:
    counts = {
        row["state"]: int(row["count"])
        for row in connection.execute(
            """SELECT state, COUNT(*) AS count
               FROM email_campaign_deliveries WHERE campaign_id = ? GROUP BY state""",
            (campaign_id,),
        )
    }
    queued = counts.get("queued", 0) + counts.get("sending", 0)
    sent = counts.get("sent", 0)
    failed = counts.get("failed", 0)
    skipped = counts.get("skipped", 0)
    now = int(time.time())
    state = "running" if queued else "completed_with_errors" if failed else "completed"
    completed_at = None if queued else now
    connection.execute(
        """UPDATE email_campaigns
           SET state = ?, sent_count = ?, failed_count = ?, skipped_count = ?,
               completed_at = ? WHERE id = ?""",
        (state, sent, failed, skipped, completed_at, campaign_id),
    )


def _recover_marketing_queue() -> None:
    now = int(time.time())
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE email_campaign_deliveries
               SET state = 'queued', updated_at = ?, last_error = 'worker_restarted'
               WHERE state = 'sending'""",
            (now,),
        )
        campaign_ids = [
            str(row[0])
            for row in connection.execute(
                """SELECT DISTINCT campaign_id FROM email_campaign_deliveries
                   WHERE state IN ('queued', 'sending')"""
            )
        ]
        for campaign_id in campaign_ids:
            connection.execute(
                "UPDATE email_campaigns SET state = 'queued', completed_at = NULL WHERE id = ?",
                (campaign_id,),
            )
        connection.commit()


def _process_marketing_delivery_once() -> bool:
    if not _marketing_mail_configured():
        return False
    now = int(time.time())
    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            """SELECT d.campaign_id, d.user_id, d.email, d.attempt_count,
                      c.subject, c.text_body, c.html_body
               FROM email_campaign_deliveries d
               JOIN email_campaigns c ON c.id = d.campaign_id
               WHERE d.state = 'queued' AND d.attempt_count < ?
               ORDER BY d.updated_at, d.campaign_id, d.user_id LIMIT 1""",
            (MARKETING_MAX_ATTEMPTS,),
        ).fetchone()
        if row is None:
            connection.rollback()
            return False
        campaign_id = str(row["campaign_id"])
        user_id = str(row["user_id"])
        connection.execute(
            """UPDATE email_campaign_deliveries
               SET state = 'sending', attempt_count = attempt_count + 1, updated_at = ?
               WHERE campaign_id = ? AND user_id = ? AND state = 'queued'""",
            (now, campaign_id, user_id),
        )
        connection.execute(
            """UPDATE email_campaigns
               SET state = 'running', started_at = COALESCE(started_at, ?)
               WHERE id = ?""",
            (now, campaign_id),
        )
        connection.commit()
        delivery = dict(row)

    with _database() as connection:
        user = connection.execute(
            """SELECT email, email_verified_at, marketing_opt_in_at, marketing_opt_out_at
               FROM account_users WHERE id = ?""",
            (user_id,),
        ).fetchone()
    eligible = bool(
        user
        and user["email_verified_at"] is not None
        and user["marketing_opt_in_at"] is not None
        and (
            user["marketing_opt_out_at"] is None
            or int(user["marketing_opt_in_at"]) > int(user["marketing_opt_out_at"])
        )
        and hmac.compare_digest(str(user["email"]), str(delivery["email"]))
    )
    final_state = "sent"
    last_error = None
    sent_at: int | None = int(time.time())
    if not eligible:
        final_state = "skipped"
        last_error = "recipient_not_eligible"
        sent_at = None
    else:
        unsubscribe_url = (
            f"{PUBLIC_BASE_URL}/email/unsubscribe?token="
            + urllib.parse.quote(_marketing_unsubscribe_token(user_id), safe="")
        )
        text_body, html_body = _marketing_bodies(
            str(delivery["text_body"]), str(delivery["html_body"]), unsubscribe_url
        )
        try:
            _send_marketing_email(
                str(delivery["email"]),
                str(delivery["subject"]),
                text_body,
                html_body,
                unsubscribe_url,
            )
        except EmailDeliveryError:
            attempts = int(delivery["attempt_count"]) + 1
            final_state = "queued" if attempts < MARKETING_MAX_ATTEMPTS else "failed"
            last_error = "mail_delivery_failed"
            sent_at = None

    with _database() as connection:
        connection.execute("BEGIN IMMEDIATE")
        connection.execute(
            """UPDATE email_campaign_deliveries
               SET state = ?, last_error = ?, updated_at = ?, sent_at = ?
               WHERE campaign_id = ? AND user_id = ? AND state = 'sending'""",
            (
                final_state,
                last_error,
                int(time.time()),
                sent_at,
                campaign_id,
                user_id,
            ),
        )
        _refresh_campaign_counters(connection, campaign_id)
        connection.commit()
    return True


def _marketing_worker() -> None:
    _recover_marketing_queue()
    while not _MARKETING_STOP_EVENT.is_set():
        try:
            processed = _process_marketing_delivery_once()
        except (sqlite3.Error, RuntimeError) as error:
            sys.stderr.write(f"marketing worker paused: {type(error).__name__}\n")
            sys.stderr.flush()
            processed = False
        if processed:
            _MARKETING_STOP_EVENT.wait(MARKETING_SEND_INTERVAL_SECONDS)
            continue
        _MARKETING_WAKE_EVENT.wait(2.0)
        _MARKETING_WAKE_EVENT.clear()


def _start_marketing_worker() -> None:
    global _MARKETING_WORKER
    if not _marketing_mail_configured() or (
        _MARKETING_WORKER is not None and _MARKETING_WORKER.is_alive()
    ):
        return
    _MARKETING_STOP_EVENT.clear()
    _MARKETING_WAKE_EVENT.clear()
    _MARKETING_WORKER = threading.Thread(
        target=_marketing_worker, name="tmcra-marketing-email", daemon=True
    )
    _MARKETING_WORKER.start()


def _stop_marketing_worker() -> None:
    global _MARKETING_WORKER
    _MARKETING_STOP_EVENT.set()
    _MARKETING_WAKE_EVENT.set()
    if _MARKETING_WORKER is not None:
        _MARKETING_WORKER.join(timeout=5)
    _MARKETING_WORKER = None


class TmcraProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "TMCRA-Gateway/2.0"

    def do_GET(self) -> None:  # noqa: N802
        self._serve()

    def do_HEAD(self) -> None:  # noqa: N802
        self._serve()

    def do_POST(self) -> None:  # noqa: N802
        self._serve()

    def do_PUT(self) -> None:  # noqa: N802
        self._serve()

    def do_PATCH(self) -> None:  # noqa: N802
        self._serve()

    def do_DELETE(self) -> None:  # noqa: N802
        self._serve()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._serve()

    def log_message(self, fmt: str, *args: object) -> None:
        sys.stdout.write(
            f'{self.log_date_time_string()} {self.client_address[0]} {fmt % args}\n'
        )
        sys.stdout.flush()

    def log_request(self, code: object = "-", size: object = "-") -> None:
        path = urllib.parse.urlsplit(self.path).path
        self.log_message(
            '"%s %s %s" %s %s', self.command, path, self.request_version, code, size
        )

    def _serve(self) -> None:
        self._pending_set_cookies = []
        try:
            self._handle()
        except AuthInputError as error:
            self.log_error("request rejected: %s", error)
            self._json_error(400, "invalid_request")
        except (sqlite3.Error, RuntimeError) as error:
            self.log_error("account store unavailable: %s", type(error).__name__)
            self._json_error(503, "account_service_unavailable")

    def _handle(self) -> None:
        parsed_path = urllib.parse.urlsplit(self.path)
        path = parsed_path.path
        if path == "/__deployment/health":
            self._health()
            return
        if self._public_host() is None:
            self._json_error(421, "untrusted_host")
            return
        if path == "/api/auth/v1/registrations":
            self._registrations_api()
            return
        if path == "/api/auth/v1/email-verifications":
            self._email_verification_api(confirm=False)
            return
        if path == "/api/auth/v1/email-verifications/confirm":
            self._email_verification_api(confirm=True)
            return
        if path == "/api/auth/v1/sessions":
            self._sessions_api()
            return
        if path == "/api/auth/v1/password-resets":
            self._password_resets_api(confirm=False)
            return
        if path == "/api/auth/v1/password-resets/confirm":
            self._password_resets_api(confirm=True)
            return
        if path == "/api/account/v1/email-preferences":
            self._email_preferences_api()
            return
        if path == "/email/unsubscribe":
            self._marketing_unsubscribe(parsed_path)
            return
        if path == "/internal/email/v1/campaigns" or path.startswith(
            "/internal/email/v1/campaigns/"
        ):
            client_ip = self._client_ip()
            if not self._internal_ip_allowed(client_ip):
                self._record_internal_gateway_access(
                    path,
                    client_ip,
                    "ip_denied",
                    event_type="marketing_campaign",
                    response_status=404,
                    final_outcome="ip_denied",
                )
                self._json_error(404, "not_found")
                return
            self._marketing_campaign_api(path, client_ip)
            return
        if path == DOWNLOAD_REQUEST_PATH:
            self._download()
            return
        mac_download = MAC_DOWNLOAD_ROUTES.get(path)
        if mac_download:
            relative_path, content_type = mac_download
            self._download(relative_path, content_type, DOWNLOAD_CACHE_CONTROL)
            return
        if path.startswith(DESKTOP_UPDATE_REQUEST_PREFIX):
            filename = path[len(DESKTOP_UPDATE_REQUEST_PREFIX) :]
            if filename == DESKTOP_UPDATE_METADATA_FILENAME:
                self._download(
                    f"{DESKTOP_UPDATE_STORAGE_PREFIX}/{filename}",
                    DESKTOP_UPDATE_METADATA_CONTENT_TYPE,
                    DOWNLOAD_CACHE_CONTROL,
                    attachment=False,
                )
            elif DESKTOP_UPDATE_FILENAME_PATTERN.fullmatch(filename):
                content_type = (
                    DESKTOP_UPDATE_BLOCKMAP_CONTENT_TYPE
                    if filename.endswith(".blockmap")
                    else DOWNLOAD_CONTENT_TYPE
                )
                self._download(
                    f"{DESKTOP_UPDATE_STORAGE_PREFIX}/{filename}",
                    content_type,
                    DESKTOP_UPDATE_IMMUTABLE_CACHE_CONTROL,
                )
            else:
                self._json_error(404, "download_not_found")
            return
        for route in MAC_DESKTOP_UPDATE_ROUTES:
            request_prefix = route["request_prefix"]
            if not path.startswith(request_prefix):
                continue
            filename = path[len(request_prefix) :]
            if filename == route["metadata"]:
                self._download(
                    f"{route['storage_prefix']}/{filename}",
                    DESKTOP_UPDATE_METADATA_CONTENT_TYPE,
                    DOWNLOAD_CACHE_CONTROL,
                    attachment=False,
                )
            elif route["artifact_pattern"].fullmatch(filename):
                if filename.endswith(".blockmap"):
                    content_type = DESKTOP_UPDATE_BLOCKMAP_CONTENT_TYPE
                elif filename.endswith(".dmg"):
                    content_type = MAC_DOWNLOAD_CONTENT_TYPE
                else:
                    content_type = MAC_ZIP_CONTENT_TYPE
                self._download(
                    f"{route['storage_prefix']}/{filename}",
                    content_type,
                    DESKTOP_UPDATE_IMMUTABLE_CACHE_CONTROL,
                )
            else:
                self._json_error(404, "download_not_found")
            return
        if path == "/signin-with-chatgpt":
            if self.command in ("GET", "HEAD"):
                return_to = self._return_to_from_query(
                    parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
                )
                self._redirect("/login?return_to=" + urllib.parse.quote(return_to, safe=""))
            else:
                self._sign_in(parsed_path, "login")
            return
        if path in ("/login", "/register"):
            self._sign_in(parsed_path, "register" if path == "/register" else "login")
            return
        if path in ("/logout", "/signout-with-chatgpt"):
            self._sign_out(parsed_path)
            return
        if path == "/resend-verification":
            self._resend_verification(parsed_path)
            return
        if path == "/verify-email":
            self._verify_email(parsed_path)
            return
        if path == "/forgot-password":
            self._forgot_password(parsed_path)
            return
        if path == "/reset-password":
            self._reset_password(parsed_path)
            return
        if path == "/oauth/google/start":
            self._google_start(parsed_path)
            return
        if path == "/oauth/google/callback":
            self._google_callback(parsed_path)
            return

        internal = self._matches_prefix(path, INTERNAL_PREFIXES)
        if internal:
            client_ip = self._client_ip()
            if not self._internal_ip_allowed(client_ip):
                self._record_internal_gateway_access(
                    path,
                    client_ip,
                    "ip_denied",
                    response_status=404,
                    final_outcome="ip_denied",
                )
                self._json_error(404, "not_found")
                return
            identity = self._session_identity()
            if identity is None:
                browser_redirect = (
                    path == "/internal" or path.startswith("/internal/")
                ) and self.command in ("GET", "HEAD")
                self._record_internal_gateway_access(
                    path,
                    client_ip,
                    "authentication_required",
                    response_status=303 if browser_redirect else 401,
                    final_outcome="authentication_required",
                )
                if browser_redirect:
                    self._redirect(
                        "/login?return_to=" + urllib.parse.quote(self.path, safe="")
                    )
                else:
                    self._json_error(401, "authentication_required")
                return
            self._proxy(
                identity=identity,
                internal_audit=(path, client_ip, str(identity["id"])),
            )
            return

        customer_web = self._matches_prefix(path, CUSTOMER_WEB_PREFIXES)
        customer_api = self._matches_prefix(path, CUSTOMER_API_PREFIXES)
        device_endpoint = self._matches_prefix(path, ("/api/device",))
        anonymous_device = (
            path in ANONYMOUS_DEVICE_ENDPOINTS and self.command in ("POST", "OPTIONS")
        )
        requires_account = customer_web or customer_api or (
            device_endpoint and not anonymous_device
        )

        identity = self._session_identity() if requires_account else None
        if requires_account and identity is None:
            if customer_web and self.command in ("GET", "HEAD"):
                destination = self._safe_return_to(self.path, "/")
                self._redirect(
                    "/login?return_to="
                    + urllib.parse.quote(destination, safe="")
                )
            else:
                self._json_error(401, "authentication_required")
            return

        self._proxy(identity=identity)

    def _download(
        self,
        relative_path: str = DOWNLOAD_FILENAME,
        content_type: str = DOWNLOAD_CONTENT_TYPE,
        cache_control: str = DOWNLOAD_CACHE_CONTROL,
        *,
        attachment: bool = True,
    ) -> None:
        if self.command not in ("GET", "HEAD"):
            self.send_response(405)
            self.send_header("Allow", "GET, HEAD")
            self._security_headers("text/plain; charset=utf-8")
            self._send_pending_cookies()
            self.send_header("Content-Length", "0")
            self.send_header("Connection", "close")
            self.end_headers()
            return

        file_path = Path(DOWNLOAD_ROOT) / relative_path
        open_flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
        open_flags |= getattr(os, "O_NOFOLLOW", 0)
        try:
            descriptor = os.open(file_path, open_flags)
        except OSError:
            self._json_error(404, "download_not_found")
            return

        with os.fdopen(descriptor, "rb") as download:
            metadata = os.fstat(download.fileno())
            if not stat.S_ISREG(metadata.st_mode):
                self._json_error(404, "download_not_found")
                return
            size = metadata.st_size
            etag = _download_etag(download, metadata)
            range_headers = self.headers.get_all("Range", [])
            if_range_headers = self.headers.get_all("If-Range", [])
            if range_headers and if_range_headers and if_range_headers != [etag]:
                range_headers = []
            start = 0
            end = size - 1
            partial = bool(range_headers)
            if len(range_headers) > 1:
                self._download_range_not_satisfiable(size, content_type, cache_control)
                return
            if partial:
                try:
                    start, end = _parse_single_byte_range(range_headers[0], size)
                except ValueError:
                    self._download_range_not_satisfiable(size, content_type, cache_control)
                    return

            content_length = end - start + 1 if partial else size
            self.send_response(206 if partial else 200)
            self._security_headers(
                content_type,
                cache_control=cache_control,
            )
            if attachment:
                self.send_header(
                    "Content-Disposition",
                    f'attachment; filename="{Path(relative_path).name}"',
                )
            self.send_header("Accept-Ranges", "bytes")
            self.send_header("ETag", etag)
            if partial:
                self.send_header("Content-Range", f"bytes {start}-{end}/{size}")
            self._send_pending_cookies()
            self.send_header("Content-Length", str(content_length))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command == "HEAD":
                return

            download.seek(start)
            remaining = content_length
            try:
                while remaining:
                    chunk = download.read(min(DOWNLOAD_BUFFER_BYTES, remaining))
                    if not chunk:
                        break
                    self.wfile.write(chunk)
                    remaining -= len(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return

    def _download_range_not_satisfiable(
        self,
        size: int,
        content_type: str = DOWNLOAD_CONTENT_TYPE,
        cache_control: str = DOWNLOAD_CACHE_CONTROL,
    ) -> None:
        self.send_response(416)
        self._security_headers(
            content_type,
            cache_control=cache_control,
        )
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Range", f"bytes */{size}")
        self._send_pending_cookies()
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    @staticmethod
    def _account_payload(account: sqlite3.Row | dict[str, object]) -> dict[str, object]:
        return {
            "id": str(account["id"]),
            "email": str(account["email"]),
            "fullName": account["full_name"],
        }

    def _registrations_api(self) -> None:
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        try:
            self._require_auth_api_client()
            payload = self._read_json(8 * 1024)
            self._require_json_fields(
                payload,
                {"email", "password", "fullName"},
                {"email", "password", "fullName"},
            )
        except AuthInputError:
            self._json_error(400, "invalid_request")
            return

        raw_email = payload["email"]
        raw_password = payload["password"]
        raw_full_name = payload["fullName"]
        if not isinstance(raw_email, str):
            self._json_error(400, "invalid_email")
            return
        if not isinstance(raw_password, str):
            self._json_error(400, "weak_password")
            return
        if not isinstance(raw_full_name, str):
            self._json_error(400, "invalid_request")
            return
        try:
            email = self._normalize_email(raw_email)
            full_name = self._normalize_full_name(raw_full_name)
            self._require_strong_password(raw_password, email)
        except AuthInputError as error:
            code = str(error)
            if code not in {"invalid_email", "weak_password"}:
                code = "invalid_request"
            self._json_error(400, code)
            return
        if not _mail_configured():
            self._json_error(503, "email_delivery_unavailable")
            return

        try:
            self._register(
                {"email": email, "password": raw_password, "full_name": full_name},
                DEFAULT_ACCOUNT_RETURN_TO,
            )
            with _database() as connection:
                account = connection.execute(
                    "SELECT id, email, full_name FROM account_users WHERE email = ?",
                    (email,),
                ).fetchone()
            if account is None:
                raise RuntimeError("registered account is missing")
            self._json_response(
                202,
                {
                    "ok": True,
                    "status": "verification_required",
                    "account": self._account_payload(account),
                },
            )
        except RateLimitError:
            self._json_error(429, "rate_limited")
        except EmailDeliveryError:
            self.log_error("registration email delivery failed")
            self._json_error(503, "email_delivery_unavailable")
        except sqlite3.IntegrityError:
            self._json_error(409, "account_conflict")
        except AuthInputError as error:
            code = str(error)
            if code == "account_conflict":
                self._json_error(409, code)
            elif code in {"invalid_email", "weak_password"}:
                self._json_error(400, code)
            else:
                self._json_error(400, "invalid_request")

    def _email_verification_api(self, *, confirm: bool) -> None:
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        try:
            self._require_auth_api_client()
            payload = self._read_json(8 * 1024)
            allowed = {"email", "code"} if confirm else {"email"}
            self._require_json_fields(payload, allowed, allowed)
        except AuthInputError:
            self._json_error(400, "invalid_request")
            return

        raw_email = payload["email"]
        if not isinstance(raw_email, str):
            self._json_error(400, "invalid_email")
            return
        try:
            email = self._normalize_email(raw_email)
        except AuthInputError:
            self._json_error(400, "invalid_email")
            return

        try:
            if confirm:
                raw_code = payload["code"]
                if not isinstance(raw_code, str):
                    raise AuthInputError("invalid_email_code")
                code = self._verification_code(raw_code)
                self._consume_auth_limits("verify_api", email, 60, 8, 600)
                user, _return_to = self._consume_email_code(email, code, "verify_email")
                self._create_session(str(user["id"]))
                self._json_response(
                    200,
                    {"ok": True, "account": self._account_payload(user)},
                )
                return

            if not _mail_configured():
                self._json_error(503, "email_delivery_unavailable")
                return
            self._consume_auth_limits("resend_api", email, 20, 3, 3600)
            with _database() as connection:
                user = connection.execute(
                    """SELECT id, email, full_name FROM account_users
                       WHERE email = ? AND email_verified_at IS NULL""",
                    (email,),
                ).fetchone()
            if user:
                code = self._issue_email_code(
                    str(user["id"]), "verify_email", DEFAULT_ACCOUNT_RETURN_TO
                )
                self._send_verification_email(
                    str(user["email"]), str(user["full_name"] or user["email"]), code
                )
            self._json_response(202, {"ok": True, "status": "accepted"})
        except RateLimitError:
            self._json_error(429, "rate_limited")
        except EmailDeliveryError:
            self.log_error("verification API email delivery failed")
            self._json_error(503, "email_delivery_unavailable")
        except AuthInputError:
            if confirm:
                self._json_error(400, "invalid_or_expired_code")
            else:
                self._json_error(400, "invalid_request")

    def _sessions_api(self) -> None:
        if self.command in ("GET", "HEAD"):
            identity = self._session_identity()
            if identity is None:
                self._json_error(401, "authentication_required")
                return
            self._json_response(
                200,
                {"ok": True, "account": self._account_payload(identity)},
            )
            return

        if self.command == "DELETE":
            try:
                self._require_auth_api_client()
            except AuthInputError:
                self._json_error(400, "invalid_request")
                return
            self._delete_current_session()
            self._pending_set_cookies.extend(
                [self._clear_cookie(SESSION_COOKIE), self._clear_cookie(CSRF_COOKIE)]
            )
            self._json_response(200, {"ok": True})
            return

        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        try:
            self._require_auth_api_client()
            payload = self._read_json(8 * 1024)
            self._require_json_fields(
                payload, {"email", "password"}, {"email", "password"}
            )
        except AuthInputError:
            self._json_error(400, "invalid_request")
            return
        raw_email = payload["email"]
        raw_password = payload["password"]
        if not isinstance(raw_email, str) or not isinstance(raw_password, str):
            self._json_error(401, "invalid_credentials")
            return
        try:
            user = self._authenticate({"email": raw_email, "password": raw_password})
            self._create_session(str(user["id"]))
            self._json_response(
                200,
                {"ok": True, "account": self._account_payload(user)},
            )
        except RateLimitError:
            self._json_error(429, "rate_limited")
        except UnverifiedAccountError:
            self._json_error(403, "unverified_account")
        except AuthInputError:
            self._json_error(401, "invalid_credentials")

    def _password_resets_api(self, *, confirm: bool) -> None:
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        try:
            self._require_auth_api_client()
            payload = self._read_json(8 * 1024)
            allowed = {"email", "code", "password"} if confirm else {"email"}
            self._require_json_fields(payload, allowed, allowed)
        except AuthInputError:
            self._json_error(400, "invalid_request")
            return

        raw_email = payload["email"]
        if not isinstance(raw_email, str):
            self._json_error(400, "invalid_email")
            return
        try:
            email = self._normalize_email(raw_email)
        except AuthInputError:
            self._json_error(400, "invalid_email")
            return

        if not confirm:
            if not _mail_configured():
                self._json_error(503, "email_delivery_unavailable")
                return
            try:
                self._consume_auth_limits("password_reset_api", email, 20, 3, 3600)
                with _database() as connection:
                    user = connection.execute(
                        """SELECT id, email, full_name FROM account_users
                           WHERE email = ? AND email_verified_at IS NOT NULL""",
                        (email,),
                    ).fetchone()
                if user:
                    code = self._issue_email_code(
                        str(user["id"]),
                        "reset_password",
                        DEFAULT_ACCOUNT_RETURN_TO,
                    )
                    try:
                        self._send_password_reset_email(
                            str(user["email"]),
                            str(user["full_name"] or user["email"]),
                            code,
                        )
                    except EmailDeliveryError:
                        # Keep the response indistinguishable from an unknown email.
                        self.log_error("password reset API email delivery failed")
                self._json_response(202, {"ok": True, "status": "accepted"})
            except RateLimitError:
                self._json_error(429, "rate_limited")
            return

        raw_code = payload["code"]
        raw_password = payload["password"]
        if not isinstance(raw_code, str):
            self._json_error(400, "invalid_or_expired_code")
            return
        if not isinstance(raw_password, str):
            self._json_error(400, "weak_password")
            return
        try:
            code = self._verification_code(raw_code)
        except AuthInputError:
            self._json_error(400, "invalid_or_expired_code")
            return
        try:
            self._require_strong_password(raw_password, email)
        except AuthInputError:
            self._json_error(400, "weak_password")
            return
        try:
            self._consume_auth_limits("reset_code_api", email, 60, 8, 600)
            user, _return_to = self._consume_password_reset_code(
                email, code, raw_password
            )
            self._create_session(str(user["id"]))
            self._json_response(
                200,
                {"ok": True, "account": self._account_payload(user)},
            )
        except RateLimitError:
            self._json_error(429, "rate_limited")
        except AuthInputError:
            self._json_error(400, "invalid_or_expired_code")

    def _email_preferences_api(self) -> None:
        identity = self._session_identity()
        if identity is None:
            self._json_error(401, "authentication_required")
            return
        if self.command not in ("GET", "HEAD", "PUT"):
            self._json_error(405, "method_not_allowed")
            return
        user_id = str(identity["id"])
        if self.command == "PUT":
            self._require_same_origin()
            payload = self._read_json(4 * 1024)
            self._require_json_fields(payload, {"marketing"}, {"marketing"})
            marketing = payload["marketing"]
            if not isinstance(marketing, bool):
                raise AuthInputError("invalid_marketing_preference")
            now = int(time.time())
            with _database() as connection:
                if marketing:
                    connection.execute(
                        """UPDATE account_users
                           SET marketing_opt_in_at = ?, updated_at = ? WHERE id = ?""",
                        (now, now, user_id),
                    )
                else:
                    connection.execute(
                        """UPDATE account_users
                           SET marketing_opt_out_at = ?, updated_at = ? WHERE id = ?""",
                        (now, now, user_id),
                    )
        with _database() as connection:
            row = connection.execute(
                """SELECT marketing_opt_in_at, marketing_opt_out_at
                   FROM account_users WHERE id = ?""",
                (user_id,),
            ).fetchone()
        if row is None:
            self._json_error(401, "authentication_required")
            return
        enabled = bool(
            row["marketing_opt_in_at"] is not None
            and (
                row["marketing_opt_out_at"] is None
                or int(row["marketing_opt_in_at"]) > int(row["marketing_opt_out_at"])
            )
        )
        self._json_response(
            200,
            {
                "ok": True,
                "preferences": {
                    "marketing": enabled,
                    "opted_in_at": row["marketing_opt_in_at"],
                    "opted_out_at": row["marketing_opt_out_at"],
                },
            },
        )

    def _marketing_unsubscribe(self, parsed_path: urllib.parse.SplitResult) -> None:
        query_token = self._single_query_value(parsed_path.query, "token", 256) or ""
        if self.command in ("GET", "HEAD"):
            if not query_token:
                self._html_response(
                    400,
                    self._page_shell(
                        "Unsubscribe / 退订",
                        '<main class="card narrow"><h1>Invalid unsubscribe link / 退订链接无效</h1></main>',
                    ),
                )
                return
            self._html_response(
                200,
                self._page_shell(
                    "Unsubscribe / 退订",
                    f"""<main class="card narrow"><p class="eyebrow">Email preferences</p>
                    <h1>Unsubscribe from TMCRA updates?</h1>
                    <p class="supporting">You will continue to receive security and account emails. / 您仍会收到安全与账户邮件。</p>
                    <form method="post" action="/email/unsubscribe">
                      <input type="hidden" name="token" value="{html.escape(query_token, quote=True)}">
                      <button type="submit">Unsubscribe / 确认退订</button>
                    </form></main>""",
                ),
            )
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        form = self._read_form()
        token = form.get("token", "") or query_token
        _marketing_unsubscribe_user(token)
        self._html_response(
            200,
            self._page_shell(
                "Unsubscribed / 已退订",
                '<main class="card narrow"><p class="eyebrow">Email preferences</p>'
                '<h1>Unsubscribed / 已退订</h1><p class="supporting">'
                "Promotional email has been disabled. Account and security email remains enabled. "
                "/ 推广邮件已关闭，账户与安全邮件不受影响。</p></main>",
            ),
        )

    def _marketing_campaign_api(
        self,
        path: str,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> None:
        def audit(
            status: int,
            final_outcome: str,
            outcome: str = "session_forwarded",
        ) -> None:
            self._record_internal_gateway_access(
                path,
                address,
                outcome,
                event_type="marketing_campaign",
                response_status=status,
                final_outcome=final_outcome,
            )

        def json_error(status: int, code: str, final_outcome: str) -> None:
            audit(status, final_outcome)
            self._json_error(status, code)

        def json_response(
            status: int, value: dict[str, object], final_outcome: str
        ) -> None:
            audit(status, final_outcome)
            self._json_response(status, value)

        if not _marketing_mail_configured():
            audit(503, "marketing_unavailable", "authentication_required")
            self._json_error(503, "marketing_email_unavailable")
            return
        if not self._marketing_authenticated():
            audit(
                401,
                "marketing_authentication_required",
                "authentication_required",
            )
            self._marketing_authentication_required()
            return

        try:
            prefix = "/internal/email/v1/campaigns"
            suffix = path[len(prefix) :]
            if suffix:
                if not suffix.startswith("/") or "/" in suffix[1:]:
                    json_error(404, "campaign_not_found", "marketing_not_found")
                    return
                campaign_id = suffix[1:]
                if not re.fullmatch(r"cmp_[0-9a-f]{32}", campaign_id):
                    json_error(404, "campaign_not_found", "marketing_not_found")
                    return
                if self.command not in ("GET", "HEAD"):
                    json_error(405, "method_not_allowed", "marketing_method_not_allowed")
                    return
                with _database() as connection:
                    campaign = connection.execute(
                        "SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)
                    ).fetchone()
                if campaign is None:
                    json_error(404, "campaign_not_found", "marketing_not_found")
                    return
                json_response(
                    200,
                    {"ok": True, "campaign": _campaign_payload(campaign)},
                    "marketing_read",
                )
                return

            if self.command in ("GET", "HEAD"):
                with _database() as connection:
                    campaigns = connection.execute(
                        "SELECT * FROM email_campaigns ORDER BY created_at DESC LIMIT 50"
                    ).fetchall()
                json_response(
                    200,
                    {
                        "ok": True,
                        "campaigns": [_campaign_payload(row) for row in campaigns],
                    },
                    "marketing_read",
                )
                return
            if self.command != "POST":
                json_error(405, "method_not_allowed", "marketing_method_not_allowed")
                return
            payload = self._read_json(MAXIMUM_EMAIL_API_BYTES)
            self._require_json_fields(
                payload,
                {"idempotency_key", "subject", "text_body", "html_body", "audience"},
                {"idempotency_key", "subject", "text_body", "html_body"},
            )
            if payload.get("audience", "all_opted_in") != "all_opted_in":
                raise AuthInputError("unsupported_campaign_audience")
            idempotency_key = self._marketing_idempotency_key(payload["idempotency_key"])
            subject = self._marketing_subject(payload["subject"])
            text_body = self._marketing_body(
                payload["text_body"], MAXIMUM_MARKETING_TEXT_CHARACTERS, "text"
            )
            html_body = self._marketing_body(
                payload["html_body"], MAXIMUM_MARKETING_HTML_CHARACTERS, "html"
            )
            if re.search(
                r"<(?:script|iframe|object|embed|form)(?:\s|>)", html_body, re.I
            ):
                raise AuthInputError("unsafe_marketing_html")

            now = int(time.time())
            with _database() as connection:
                connection.execute("BEGIN IMMEDIATE")
                existing = connection.execute(
                    "SELECT * FROM email_campaigns WHERE idempotency_key = ?",
                    (idempotency_key,),
                ).fetchone()
                if existing is not None:
                    same_request = all(
                        hmac.compare_digest(str(existing[field]), value)
                        for field, value in (
                            ("subject", subject),
                            ("text_body", text_body),
                            ("html_body", html_body),
                        )
                    )
                    connection.rollback()
                    if not same_request:
                        json_error(
                            409,
                            "idempotency_conflict",
                            "marketing_idempotency_conflict",
                        )
                        return
                    json_response(
                        200,
                        {"ok": True, "campaign": _campaign_payload(existing)},
                        "marketing_idempotent_replay",
                    )
                    return
                recipients = connection.execute(
                    """SELECT id, email FROM account_users
                       WHERE email_verified_at IS NOT NULL
                         AND marketing_opt_in_at IS NOT NULL
                         AND (marketing_opt_out_at IS NULL OR marketing_opt_in_at > marketing_opt_out_at)
                       ORDER BY id"""
                ).fetchall()
                campaign_id = "cmp_" + secrets.token_hex(16)
                initial_state = "queued" if recipients else "completed"
                completed_at = None if recipients else now
                connection.execute(
                    """INSERT INTO email_campaigns (
                           id, idempotency_key, subject, text_body, html_body, state,
                           requested_by, recipient_count, sent_count, failed_count,
                           skipped_count, created_at, started_at, completed_at, last_error
                       ) VALUES (?, ?, ?, ?, ?, ?, 'marketing-api', ?, 0, 0, 0, ?, NULL, ?, NULL)""",
                    (
                        campaign_id,
                        idempotency_key,
                        subject,
                        text_body,
                        html_body,
                        initial_state,
                        len(recipients),
                        now,
                        completed_at,
                    ),
                )
                connection.executemany(
                    """INSERT INTO email_campaign_deliveries (
                           campaign_id, user_id, email, state, attempt_count,
                           last_error, created_at, updated_at, sent_at
                       ) VALUES (?, ?, ?, 'queued', 0, NULL, ?, ?, NULL)""",
                    [
                        (campaign_id, str(row["id"]), str(row["email"]), now, now)
                        for row in recipients
                    ],
                )
                campaign = connection.execute(
                    "SELECT * FROM email_campaigns WHERE id = ?", (campaign_id,)
                ).fetchone()
                connection.commit()
            if recipients:
                _MARKETING_WAKE_EVENT.set()
            json_response(
                202,
                {"ok": True, "campaign": _campaign_payload(campaign)},
                "marketing_created",
            )
        except AuthInputError:
            audit(400, "marketing_invalid_request")
            raise
        except (sqlite3.Error, RuntimeError):
            audit(503, "marketing_store_unavailable")
            raise

    def _proxy(
        self,
        identity: dict[str, str | None] | None,
        internal_audit: tuple[
            str,
            ipaddress.IPv4Address | ipaddress.IPv6Address,
            str,
        ]
        | None = None,
    ) -> None:
        length_header = self.headers.get("Content-Length", "0")
        try:
            length = int(length_header)
        except ValueError:
            self._json_error(400, "invalid_content_length")
            return
        if length < 0 or length > MAXIMUM_REQUEST_BYTES:
            self._json_error(413, "payload_too_large")
            return
        body = self.rfile.read(length) if length else None

        upstream_headers: dict[str, str] = {}
        for name, value in self.headers.items():
            lowered = name.lower()
            if (
                lowered in HOP_BY_HOP_HEADERS
                or lowered in PROXY_CONTROL_HEADERS
                or lowered == "authorization"
                or lowered.startswith("oai-authenticated-")
            ):
                continue
            upstream_headers[name] = value

        origin_host = self.headers.get("Host", "")
        public_host = self._public_host() or origin_host
        upstream_headers["Host"] = public_host
        upstream_headers["Connection"] = "close"
        upstream_headers["X-Forwarded-Host"] = public_host
        upstream_headers["X-Forwarded-Proto"] = "https"
        client_ip = str(self._client_ip())
        upstream_headers["X-Forwarded-For"] = client_ip
        upstream_headers["CF-Connecting-IP"] = client_ip
        if identity:
            upstream_headers["oai-authenticated-user-email"] = str(identity["email"])
            full_name = identity.get("full_name")
            if full_name:
                upstream_headers["oai-authenticated-user-full-name"] = (
                    urllib.parse.quote(str(full_name), safe="")
                )
                upstream_headers[
                    "oai-authenticated-user-full-name-encoding"
                ] = "percent-encoded-utf-8"

        connection = http.client.HTTPConnection(
            UPSTREAM_HOST, UPSTREAM_PORT, timeout=90
        )
        try:
            connection.request(
                self.command,
                self.path,
                body=body,
                headers=upstream_headers,
            )
            response = connection.getresponse()
            payload = response.read()
            if internal_audit:
                audit_path, audit_address, account_user_id = internal_audit
                self._record_internal_gateway_access(
                    audit_path,
                    audit_address,
                    "session_forwarded",
                    account_user_id,
                    response_status=response.status,
                    final_outcome=self._upstream_authorization_outcome(
                        response.status
                    ),
                )
            self.send_response(response.status, response.reason)
            for name, value in response.getheaders():
                lowered = name.lower()
                if lowered in HOP_BY_HOP_HEADERS or lowered in {
                    "content-length",
                    "strict-transport-security",
                }:
                    continue
                self.send_header(name, value)
            self.send_header("Strict-Transport-Security", STRICT_TRANSPORT_SECURITY)
            self._send_pending_cookies()
            self.send_header("Content-Length", str(len(payload)))
            self.send_header("Connection", "close")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        except (ConnectionError, TimeoutError, http.client.HTTPException) as error:
            self.log_error("upstream failure: %s", error)
            if internal_audit:
                audit_path, audit_address, account_user_id = internal_audit
                self._record_internal_gateway_access(
                    audit_path,
                    audit_address,
                    "session_forwarded",
                    account_user_id,
                    response_status=502,
                    final_outcome="upstream_unavailable",
                )
            self._json_error(502, "upstream_unavailable")
        finally:
            connection.close()

    def _sign_in(self, parsed_path: urllib.parse.SplitResult, mode: str) -> None:
        return_to = self._return_to_from_query(
            parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
        )
        identity = self._session_identity()
        if self.command in ("GET", "HEAD"):
            if identity:
                self._redirect(return_to)
                return
            self._auth_page(return_to, mode=mode)
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        try:
            self._require_same_origin()
            form = self._read_form()
            self._require_csrf(form.get("csrf", ""))
            form_return_to = self._safe_return_to(
                form.get("return_to", ""), DEFAULT_ACCOUNT_RETURN_TO
            )
            action = form.get("action", mode)
            if action == "register":
                email = self._register(form, form_return_to)
                self._pending_set_cookies.append(self._clear_cookie(CSRF_COOKIE))
                self._verification_code_page(email, form_return_to)
                return
            if action != "sign_in":
                raise AuthInputError("invalid_authentication_request")
            user = self._authenticate(form)
            self._create_session(user["id"])
            self._pending_set_cookies.append(self._clear_cookie(CSRF_COOKIE))
            self._redirect(form_return_to)
        except RateLimitError:
            self._auth_page(
                return_to,
                mode=mode,
                error="Too many attempts. Please wait and try again. / 尝试次数过多，请稍后再试。",
                status=429,
            )
        except UnverifiedAccountError:
            self._auth_page(
                return_to,
                mode="login",
                error="Verify this email before signing in. / 请先完成邮箱验证。",
                status=403,
            )
        except EmailDeliveryError as error:
            self.log_error("transactional email unavailable: %s", error)
            self._auth_page(
                return_to,
                mode="register",
                error="Verification email is temporarily unavailable. / 验证邮件暂时无法发送。",
                status=503,
            )
        except AuthInputError as error:
            self.log_error("authentication request rejected: %s", error)
            reason = str(error)
            if reason in {"invalid_csrf", "expired_csrf"}:
                message = (
                    "This form has expired. Reload the page and submit again. / "
                    "此表单已过期，请刷新页面后重新提交。"
                )
            elif mode == "register" and reason == "weak_password":
                message = (
                    "Use at least 8 characters with at least one English letter "
                    "and one number. / 密码至少 8 位，并至少包含一个英文字母和一个数字。"
                )
            elif mode == "register" and reason == "invalid_name":
                message = (
                    "Enter a valid name using no more than 80 characters. / "
                    "请输入有效姓名，长度不要超过 80 个字符。"
                )
            elif mode == "register" and reason == "invalid_email":
                message = (
                    "Enter a valid email address. / 请输入有效的邮箱地址。"
                )
            elif mode == "register" and reason == "account_conflict":
                message = (
                    "This email is already registered. Sign in or use Forgot password. / "
                    "该邮箱已注册，请直接登录或使用忘记密码。"
                )
            elif mode != "register" and reason in {
                "invalid_credentials",
                "invalid_email",
            }:
                message = (
                    "The email or password is incorrect. / 邮箱或密码不正确。"
                )
            else:
                message = (
                    "We could not complete that request. Check your details and try again. / "
                    "无法完成请求，请检查信息后重试。"
                )
            self._auth_page(
                return_to,
                mode=mode,
                error=message,
                status=400,
            )
        except sqlite3.IntegrityError:
            self.log_error("authentication request rejected: account_conflict")
            self._auth_page(
                return_to,
                mode=mode,
                error=(
                    "This email is already registered. Sign in or use Forgot password. / "
                    "该邮箱已注册，请直接登录或使用忘记密码。"
                ),
                status=400,
            )

    def _sign_out(self, parsed_path: urllib.parse.SplitResult) -> None:
        return_to = self._return_to_from_query(parsed_path.query, "/")
        identity = self._session_identity()
        if self.command in ("GET", "HEAD"):
            if not identity:
                self._redirect(return_to)
                return
            csrf = self._new_csrf_token()
            self._pending_set_cookies.append(self._cookie(CSRF_COOKIE, csrf, CSRF_LIFETIME_SECONDS))
            body = self._page_shell(
                "Sign out / 退出登录",
                f"""
                <main class="card narrow">
                  <p class="eyebrow">TMCRA Account</p>
                  <h1>Sign out / 退出登录</h1>
                  <p>{html.escape(str(identity['email']))}</p>
                  <form method="post" action="/logout">
                    <input type="hidden" name="csrf" value="{html.escape(csrf)}">
                    <input type="hidden" name="return_to" value="{html.escape(return_to)}">
                    <button type="submit">Sign out / 退出</button>
                  </form>
                </main>
                """,
            )
            self._html_response(200, body)
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        try:
            self._require_same_origin()
            form = self._read_form()
            self._require_csrf(form.get("csrf", ""))
            return_to = self._safe_return_to(form.get("return_to", ""), "/")
        except AuthInputError:
            self._json_error(403, "invalid_request")
            return
        self._delete_current_session()
        self._pending_set_cookies.extend(
            [self._clear_cookie(SESSION_COOKIE), self._clear_cookie(CSRF_COOKIE)]
        )
        self._redirect(return_to)

    def _auth_page(
        self,
        return_to: str,
        *,
        mode: str = "login",
        error: str = "",
        status: int = 200,
    ) -> None:
        csrf = self._new_csrf_token()
        self._pending_set_cookies.append(self._cookie(CSRF_COOKIE, csrf, CSRF_LIFETIME_SECONDS))
        escaped_return = html.escape(return_to)
        escaped_csrf = html.escape(csrf)
        error_markup = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        google = ""
        if _google_configured():
            google_url = "/oauth/google/start?return_to=" + urllib.parse.quote(return_to, safe="")
            google = (
                f'<a class="oauth" href="{html.escape(google_url)}">'
                '<span aria-hidden="true">G</span> Continue with Google</a>'
                '<div class="divider"><span>or / 或</span></div>'
            )
        if mode == "register":
            if _mail_configured():
                form = f"""
                <form method="post" action="/register">
                  <input type="hidden" name="action" value="register">
                  <input type="hidden" name="csrf" value="{escaped_csrf}">
                  <input type="hidden" name="return_to" value="{escaped_return}">
                  <label>Name / 姓名<input name="full_name" autocomplete="name" required maxlength="80"></label>
                  <label>Email / 邮箱<input name="email" type="email" autocomplete="username" required maxlength="254"></label>
                  <label>Password / 密码<input name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label>
                  <p class="hint">At least 8 characters with an English letter and a number. Uppercase and symbols are optional. / 至少 8 位，包含英文字母和数字；无需大写字母或符号。</p>
                  <button type="submit">Create account / 创建账户</button>
                </form>
                """
            else:
                form = '<p class="error">Email registration is not enabled yet. / 邮箱注册通道尚未启用。</p>'
            heading = "Create your account"
            heading_zh = "创建账户"
            supporting = "Start with a verified email address. / 使用已验证的邮箱建立账户。"
        else:
            form = f"""
            <form method="post" action="/login">
              <input type="hidden" name="action" value="sign_in">
              <input type="hidden" name="csrf" value="{escaped_csrf}">
              <input type="hidden" name="return_to" value="{escaped_return}">
              <label>Email / 邮箱<input name="email" type="email" autocomplete="username" required maxlength="254"></label>
              <label>Password / 密码<input name="password" type="password" autocomplete="current-password" required maxlength="128"></label>
              <div class="form-meta"><a href="/forgot-password">Forgot password? / 忘记密码</a></div>
              <button type="submit">Sign in / 登录</button>
            </form>
            """
            heading = "Welcome back"
            heading_zh = "欢迎回来"
            supporting = "Continue to your TMCRA workspace. / 进入你的 TMCRA 工作空间。"
        encoded_return = urllib.parse.quote(return_to, safe="")
        login_current = ' aria-current="page"' if mode != "register" else ""
        register_current = ' aria-current="page"' if mode == "register" else ""
        body = self._page_shell(
            "TMCRA Account",
            f"""
            <main class="auth-panel">
              <nav class="auth-switch" aria-label="Account access / 账户访问">
                <a href="/login?return_to={encoded_return}"{login_current}>Sign in <span>登录</span></a>
                <a href="/register?return_to={encoded_return}"{register_current}>Register <span>注册</span></a>
              </nav>
              <div class="auth-heading">
                <p class="eyebrow">TMCRA ACCOUNT</p>
                <h1>{heading}<span>{heading_zh}</span></h1>
                <p class="supporting">{supporting}</p>
              </div>
              {error_markup}
              {google}
              {form}
              <div class="account-assurance" aria-label="Security status / 安全状态">
                <span aria-hidden="true"></span>
                <p>Protected session · Email verification<br><small>安全会话 · 邮箱验证</small></p>
              </div>
            </main>
            """,
        )
        self._html_response(status, body)

    def _register(self, form: dict[str, str], return_to: str) -> str:
        if not _mail_configured():
            raise EmailDeliveryError("mail_transport_unavailable")
        email = self._normalize_email(form.get("email", ""))
        password = form.get("password", "")
        full_name = self._normalize_full_name(form.get("full_name", ""))
        self._require_strong_password(password, email)
        self._consume_auth_limits("register", email, 20, 3, 3600)
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt, PASSWORD_ITERATIONS)
        now = int(time.time())
        user_id = "usr_" + secrets.token_hex(16)
        code = f"{secrets.randbelow(1_000_000):06d}"
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                "SELECT id, email_verified_at FROM account_users WHERE email = ?", (email,)
            ).fetchone()
            if existing and existing["email_verified_at"] is not None:
                connection.rollback()
                raise AuthInputError("account_conflict")
            if existing:
                user_id = str(existing["id"])
                connection.execute(
                    """UPDATE account_users
                       SET full_name = ?, password_hash = ?, password_salt = ?,
                           password_iterations = ?, password_enabled = 1, updated_at = ?
                       WHERE id = ?""",
                    (full_name, password_hash, salt, PASSWORD_ITERATIONS, now, user_id),
                )
            else:
                connection.execute(
                    """INSERT INTO account_users (
                           id, email, full_name, password_hash, password_salt,
                           password_iterations, password_enabled, email_verified_at,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 1, NULL, ?, ?)""",
                    (
                        user_id,
                        email,
                        full_name,
                        password_hash,
                        salt,
                        PASSWORD_ITERATIONS,
                        now,
                        now,
                    ),
                )
            connection.execute(
                "DELETE FROM account_email_tokens WHERE user_id = ? AND purpose = 'verify_email'",
                (user_id,),
            )
            token_hash = self._secret_hash("verify_email", f"{user_id}:{code}")
            connection.execute(
                """INSERT INTO account_email_tokens (
                       token_hash, user_id, purpose, return_to, created_at, expires_at,
                       consumed_at, attempt_count
                   ) VALUES (?, ?, 'verify_email', ?, ?, ?, NULL, 0)""",
                (token_hash, user_id, return_to, now, now + EMAIL_TOKEN_SECONDS),
            )
            connection.commit()
        self._send_verification_email(email, full_name, code)
        return email

    def _authenticate(self, form: dict[str, str]) -> sqlite3.Row:
        email = self._normalize_email(form.get("email", ""))
        password = form.get("password", "")
        if not password or len(password) > 128:
            raise AuthInputError("invalid_credentials")
        self._consume_auth_limits("login", email, 60, 6, 900)
        with _database() as connection:
            user = connection.execute(
                """SELECT id, email, full_name, password_hash, password_salt,
                          password_iterations, password_enabled, email_verified_at
                   FROM account_users WHERE email = ?""",
                (email,),
            ).fetchone()
        if user and int(user["password_enabled"]) == 1:
            actual_hash = self._password_hash(
                password,
                bytes(user["password_salt"]),
                int(user["password_iterations"]),
            )
            valid = hmac.compare_digest(actual_hash, bytes(user["password_hash"]))
        else:
            dummy_salt = hmac.new(
                SESSION_SECRET.encode("utf-8"), b"tmcra-dummy-password", hashlib.sha256
            ).digest()[:16]
            actual_hash = self._password_hash(password, dummy_salt, PASSWORD_ITERATIONS)
            expected = hmac.new(
                SESSION_SECRET.encode("utf-8"),
                b"tmcra-dummy-password-hash",
                hashlib.sha256,
            ).digest()
            valid = hmac.compare_digest(actual_hash, expected) and False
        if not valid:
            raise AuthInputError("invalid_credentials")
        if user["email_verified_at"] is None:
            raise UnverifiedAccountError()
        return user

    def _verification_code_page(
        self,
        email: str,
        return_to: str,
        error: str = "",
        status: int = 200,
    ) -> None:
        csrf = self._new_csrf_token()
        self._pending_set_cookies.append(self._cookie(CSRF_COOKIE, csrf, CSRF_LIFETIME_SECONDS))
        escaped_email = html.escape(email)
        escaped_return = html.escape(return_to)
        error_markup = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        body = self._page_shell(
            "Verify email / 验证邮箱",
            f"""
            <main class="auth-panel">
              <p class="panel-kicker">TMCRA ACCOUNT / 邮箱验证</p>
              <div class="auth-heading">
                <p class="eyebrow">EMAIL VERIFICATION</p>
                <h1>Enter the 6-digit code<span>输入 6 位验证码</span></h1>
              </div>
              <p class="supporting">The code was sent automatically to <strong>{escaped_email or 'your email'}</strong>.</p>
              {error_markup}
              <form method="post" action="/verify-email">
                <input type="hidden" name="csrf" value="{html.escape(csrf)}">
                <input type="hidden" name="return_to" value="{escaped_return}">
                <label>Email / 邮箱<input name="email" type="email" autocomplete="username" required maxlength="254" value="{escaped_email}"></label>
                <label>Verification code / 验证码<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6}}" minlength="6" maxlength="6" required></label>
                <button type="submit">Verify and continue / 验证并继续</button>
              </form>
              <nav class="alternate"><a href="/resend-verification?email={urllib.parse.quote(email, safe='')}&amp;return_to={urllib.parse.quote(return_to, safe='')}">Resend code / 重新发送</a></nav>
            </main>
            """,
        )
        self._html_response(status, body)

    def _verify_email(self, parsed_path: urllib.parse.SplitResult) -> None:
        query_email = self._single_query_value(parsed_path.query, "email", 254) or ""
        return_to = self._return_to_from_query(
            parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
        )
        if self.command in ("GET", "HEAD"):
            self._verification_code_page(query_email, return_to)
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        email = ""
        try:
            self._require_same_origin()
            form = self._read_form()
            self._require_csrf(form.get("csrf", ""))
            email = self._normalize_email(form.get("email", ""))
            code = self._verification_code(form.get("code", ""))
            return_to = self._safe_return_to(
                form.get("return_to", ""), DEFAULT_ACCOUNT_RETURN_TO
            )
            self._consume_auth_limits("verify", email, 60, 8, 600)
            user, stored_return_to = self._consume_email_code(email, code, "verify_email")
            self._create_session(str(user["id"]))
            self._pending_set_cookies.append(self._clear_cookie(CSRF_COOKIE))
            self._redirect(self._safe_return_to(stored_return_to, return_to))
        except RateLimitError:
            self._verification_code_page(
                email, return_to, "Too many attempts. / 尝试次数过多，请稍后重试。", 429
            )
        except AuthInputError as error:
            self.log_error("email verification rejected: %s", error)
            reason = str(error)
            if reason in {"invalid_csrf", "expired_csrf"}:
                message = (
                    "This form has expired. Reload the page and submit again. / "
                    "此表单已过期，请刷新页面后重新提交。"
                )
            elif reason == "invalid_email":
                message = "Enter a valid email address. / 请输入有效的邮箱地址。"
            else:
                message = (
                    "The verification code is incorrect or expired. / "
                    "验证码不正确或已过期。"
                )
            self._verification_code_page(
                email, return_to, message, 400
            )

    def _resend_verification(self, parsed_path: urllib.parse.SplitResult) -> None:
        query_email = self._single_query_value(parsed_path.query, "email", 254) or ""
        return_to = self._return_to_from_query(
            parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
        )
        if self.command in ("GET", "HEAD"):
            self._email_request_page(
                "Resend verification code / 重发验证码",
                "/resend-verification",
                query_email,
                return_to,
                "Send code / 发送验证码",
            )
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        email = ""
        try:
            self._require_same_origin()
            form = self._read_form()
            self._require_csrf(form.get("csrf", ""))
            email = self._normalize_email(form.get("email", ""))
            return_to = self._safe_return_to(
                form.get("return_to", ""), DEFAULT_ACCOUNT_RETURN_TO
            )
            self._consume_auth_limits("resend", email, 20, 3, 3600)
            with _database() as connection:
                user = connection.execute(
                    """SELECT id, email, full_name FROM account_users
                       WHERE email = ? AND email_verified_at IS NULL""",
                    (email,),
                ).fetchone()
            if user:
                code = self._issue_email_code(str(user["id"]), "verify_email", return_to)
                self._send_verification_email(email, str(user["full_name"] or email), code)
            self._verification_code_page(email, return_to)
        except RateLimitError:
            self._email_request_page(
                "Resend verification code / 重发验证码",
                "/resend-verification",
                email,
                return_to,
                "Send code / 发送验证码",
                "Too many requests. / 请求过于频繁。",
                429,
            )
        except EmailDeliveryError as error:
            self.log_error("verification email unavailable: %s", error)
            self._email_request_page(
                "Resend verification code / 重发验证码",
                "/resend-verification",
                email,
                return_to,
                "Send code / 发送验证码",
                "Email delivery is temporarily unavailable. / 邮件发送暂时不可用。",
                503,
            )
        except AuthInputError as error:
            reason = str(error)
            if reason in {"invalid_csrf", "expired_csrf"}:
                message = (
                    "This form has expired. Reload the page and submit again. / "
                    "此表单已过期，请刷新页面后重新提交。"
                )
            else:
                message = "Enter a valid email address. / 请输入有效的邮箱地址。"
            self._email_request_page(
                "Resend verification code / 重发验证码",
                "/resend-verification",
                email,
                return_to,
                "Send code / 发送验证码",
                message,
                400,
            )

    def _forgot_password(self, parsed_path: urllib.parse.SplitResult) -> None:
        return_to = self._return_to_from_query(
            parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
        )
        if self.command in ("GET", "HEAD"):
            self._email_request_page(
                "Reset password / 重置密码",
                "/forgot-password",
                "",
                return_to,
                "Send reset code / 发送重置码",
            )
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        email = ""
        try:
            self._require_same_origin()
            form = self._read_form()
            self._require_csrf(form.get("csrf", ""))
            email = self._normalize_email(form.get("email", ""))
            return_to = self._safe_return_to(
                form.get("return_to", ""), DEFAULT_ACCOUNT_RETURN_TO
            )
            self._consume_auth_limits("password_reset", email, 20, 3, 3600)
            with _database() as connection:
                user = connection.execute(
                    """SELECT id, email, full_name FROM account_users
                       WHERE email = ? AND email_verified_at IS NOT NULL""",
                    (email,),
                ).fetchone()
            if user:
                code = self._issue_email_code(str(user["id"]), "reset_password", return_to)
                self._send_password_reset_email(email, str(user["full_name"] or email), code)
            self._password_reset_page(email, return_to)
        except RateLimitError:
            self._email_request_page(
                "Reset password / 重置密码",
                "/forgot-password",
                email,
                return_to,
                "Send reset code / 发送重置码",
                "Too many requests. / 请求过于频繁。",
                429,
            )
        except EmailDeliveryError as error:
            self.log_error("password reset email unavailable: %s", error)
            self._email_request_page(
                "Reset password / 重置密码",
                "/forgot-password",
                email,
                return_to,
                "Send reset code / 发送重置码",
                "Email delivery is temporarily unavailable. / 邮件发送暂时不可用。",
                503,
            )
        except AuthInputError as error:
            reason = str(error)
            if reason in {"invalid_csrf", "expired_csrf"}:
                message = (
                    "This form has expired. Reload the page and submit again. / "
                    "此表单已过期，请刷新页面后重新提交。"
                )
            else:
                message = "Enter a valid email address. / 请输入有效的邮箱地址。"
            self._email_request_page(
                "Reset password / 重置密码",
                "/forgot-password",
                email,
                return_to,
                "Send reset code / 发送重置码",
                message,
                400,
            )

    def _reset_password(self, parsed_path: urllib.parse.SplitResult) -> None:
        query_email = self._single_query_value(parsed_path.query, "email", 254) or ""
        return_to = self._return_to_from_query(
            parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
        )
        if self.command in ("GET", "HEAD"):
            self._password_reset_page(query_email, return_to)
            return
        if self.command != "POST":
            self._json_error(405, "method_not_allowed")
            return
        email = ""
        try:
            self._require_same_origin()
            form = self._read_form()
            self._require_csrf(form.get("csrf", ""))
            email = self._normalize_email(form.get("email", ""))
            code = self._verification_code(form.get("code", ""))
            password = form.get("password", "")
            self._require_strong_password(password, email)
            return_to = self._safe_return_to(
                form.get("return_to", ""), DEFAULT_ACCOUNT_RETURN_TO
            )
            self._consume_auth_limits("reset_code", email, 60, 8, 600)
            user, stored_return_to = self._consume_password_reset_code(email, code, password)
            self._create_session(str(user["id"]))
            self._pending_set_cookies.append(self._clear_cookie(CSRF_COOKIE))
            self._redirect(self._safe_return_to(stored_return_to, return_to))
        except RateLimitError:
            self._password_reset_page(
                email, return_to, "Too many attempts. / 尝试次数过多，请稍后重试。", 429
            )
        except AuthInputError as error:
            self.log_error("password reset rejected: %s", error)
            reason = str(error)
            if reason in {"invalid_csrf", "expired_csrf"}:
                message = (
                    "This form has expired. Reload the page and submit again. / "
                    "此表单已过期，请刷新页面后重新提交。"
                )
            elif reason == "weak_password":
                message = (
                    "Use at least 8 characters with at least one English letter "
                    "and one number. / 密码至少 8 位，并至少包含一个英文字母和一个数字。"
                )
            elif reason == "invalid_email":
                message = "Enter a valid email address. / 请输入有效的邮箱地址。"
            else:
                message = (
                    "The reset code is incorrect or expired. / "
                    "重置码不正确或已过期。"
                )
            self._password_reset_page(
                email,
                return_to,
                message,
                400,
            )

    def _email_request_page(
        self,
        title: str,
        action: str,
        email: str,
        return_to: str,
        button_label: str,
        error: str = "",
        status: int = 200,
    ) -> None:
        csrf = self._new_csrf_token()
        self._pending_set_cookies.append(self._cookie(CSRF_COOKIE, csrf, CSRF_LIFETIME_SECONDS))
        error_markup = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        body = self._page_shell(
            title,
            f"""
            <main class="auth-panel narrow">
              <p class="panel-kicker">TMCRA ACCOUNT / 账户安全</p>
              <h1>{html.escape(title)}</h1>
              {error_markup}
              <form method="post" action="{html.escape(action)}">
                <input type="hidden" name="csrf" value="{html.escape(csrf)}">
                <input type="hidden" name="return_to" value="{html.escape(return_to)}">
                <label>Email / 邮箱<input name="email" type="email" autocomplete="username" required maxlength="254" value="{html.escape(email)}"></label>
                <button type="submit">{html.escape(button_label)}</button>
              </form>
              <nav class="alternate"><a href="/login">Back to sign in / 返回登录</a></nav>
            </main>
            """,
        )
        self._html_response(status, body)

    def _password_reset_page(
        self,
        email: str,
        return_to: str,
        error: str = "",
        status: int = 200,
    ) -> None:
        csrf = self._new_csrf_token()
        self._pending_set_cookies.append(self._cookie(CSRF_COOKIE, csrf, CSRF_LIFETIME_SECONDS))
        error_markup = f'<p class="error" role="alert">{html.escape(error)}</p>' if error else ""
        body = self._page_shell(
            "Choose a new password / 设置新密码",
            f"""
            <main class="auth-panel narrow">
              <p class="panel-kicker">TMCRA ACCOUNT / 账户安全</p>
              <h1>Choose a new password / 设置新密码</h1>
              {error_markup}
              <form method="post" action="/reset-password">
                <input type="hidden" name="csrf" value="{html.escape(csrf)}">
                <input type="hidden" name="return_to" value="{html.escape(return_to)}">
                <label>Email / 邮箱<input name="email" type="email" autocomplete="username" required maxlength="254" value="{html.escape(email)}"></label>
                <label>Reset code / 重置码<input name="code" inputmode="numeric" autocomplete="one-time-code" pattern="[0-9]{{6}}" minlength="6" maxlength="6" required></label>
                <label>New password / 新密码<input name="password" type="password" autocomplete="new-password" required minlength="8" maxlength="128"></label>
                <p class="hint">At least 8 characters with an English letter and a number. / 至少 8 位，包含英文字母和数字。</p>
                <button type="submit">Reset and sign in / 重置并登录</button>
              </form>
            </main>
            """,
        )
        self._html_response(status, body)

    def _issue_email_code(self, user_id: str, purpose: str, return_to: str) -> str:
        code = f"{secrets.randbelow(1_000_000):06d}"
        lifetime = EMAIL_TOKEN_SECONDS if purpose == "verify_email" else PASSWORD_RESET_SECONDS
        now = int(time.time())
        token_hash = self._secret_hash(purpose, f"{user_id}:{code}")
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM account_email_tokens WHERE user_id = ? AND purpose = ?",
                (user_id, purpose),
            )
            connection.execute(
                """INSERT INTO account_email_tokens (
                       token_hash, user_id, purpose, return_to, created_at, expires_at,
                       consumed_at, attempt_count
                   ) VALUES (?, ?, ?, ?, ?, ?, NULL, 0)""",
                (token_hash, user_id, purpose, return_to, now, now + lifetime),
            )
            connection.commit()
        return code

    def _consume_email_code(
        self, email: str, code: str, purpose: str
    ) -> tuple[sqlite3.Row, str]:
        now = int(time.time())
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT t.token_hash, t.return_to, t.expires_at, t.attempt_count,
                          u.id, u.email, u.full_name
                   FROM account_email_tokens t
                   JOIN account_users u ON u.id = t.user_id
                   WHERE u.email = ? AND t.purpose = ? AND t.consumed_at IS NULL
                   ORDER BY t.created_at DESC LIMIT 1""",
                (email, purpose),
            ).fetchone()
            if not row or int(row["expires_at"]) <= now or int(row["attempt_count"]) >= 6:
                if row:
                    connection.execute(
                        "UPDATE account_email_tokens SET consumed_at = ? WHERE token_hash = ?",
                        (now, row["token_hash"]),
                    )
                    connection.commit()
                else:
                    connection.rollback()
                self._secret_hash(purpose, f"missing:{code}")
                raise AuthInputError("invalid_email_code")
            expected = self._secret_hash(purpose, f"{row['id']}:{code}")
            if not hmac.compare_digest(expected, str(row["token_hash"])):
                attempts = int(row["attempt_count"]) + 1
                connection.execute(
                    """UPDATE account_email_tokens
                       SET attempt_count = ?, consumed_at = CASE WHEN ? >= 6 THEN ? ELSE NULL END
                       WHERE token_hash = ?""",
                    (attempts, attempts, now, row["token_hash"]),
                )
                connection.commit()
                raise AuthInputError("invalid_email_code")
            connection.execute(
                "UPDATE account_users SET email_verified_at = COALESCE(email_verified_at, ?), updated_at = ? WHERE id = ?",
                (now, now, row["id"]),
            )
            connection.execute(
                "UPDATE account_email_tokens SET consumed_at = ? WHERE user_id = ? AND purpose = ?",
                (now, row["id"], purpose),
            )
            connection.commit()
        return row, str(row["return_to"])

    def _consume_password_reset_code(
        self, email: str, code: str, password: str
    ) -> tuple[sqlite3.Row, str]:
        salt = secrets.token_bytes(16)
        password_hash = self._password_hash(password, salt, PASSWORD_ITERATIONS)
        now = int(time.time())
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            user = connection.execute(
                """SELECT t.token_hash, t.return_to, t.expires_at, t.attempt_count,
                          u.id, u.email, u.full_name
                   FROM account_email_tokens t
                   JOIN account_users u ON u.id = t.user_id
                   WHERE u.email = ? AND t.purpose = 'reset_password'
                         AND t.consumed_at IS NULL
                   ORDER BY t.created_at DESC LIMIT 1""",
                (email,),
            ).fetchone()
            if not user or int(user["expires_at"]) <= now or int(user["attempt_count"]) >= 6:
                if user:
                    connection.execute(
                        "UPDATE account_email_tokens SET consumed_at = ? WHERE token_hash = ?",
                        (now, user["token_hash"]),
                    )
                    connection.commit()
                else:
                    connection.rollback()
                self._secret_hash("reset_password", f"missing:{code}")
                raise AuthInputError("invalid_email_code")
            expected = self._secret_hash("reset_password", f"{user['id']}:{code}")
            if not hmac.compare_digest(expected, str(user["token_hash"])):
                attempts = int(user["attempt_count"]) + 1
                connection.execute(
                    """UPDATE account_email_tokens
                       SET attempt_count = ?, consumed_at = CASE WHEN ? >= 6 THEN ? ELSE NULL END
                       WHERE token_hash = ?""",
                    (attempts, attempts, now, user["token_hash"]),
                )
                connection.commit()
                raise AuthInputError("invalid_email_code")
            connection.execute(
                """UPDATE account_users
                   SET password_hash = ?, password_salt = ?, password_iterations = ?,
                       password_enabled = 1, updated_at = ? WHERE id = ?""",
                (password_hash, salt, PASSWORD_ITERATIONS, now, user["id"]),
            )
            connection.execute("DELETE FROM account_sessions WHERE user_id = ?", (user["id"],))
            connection.execute(
                "UPDATE account_email_tokens SET consumed_at = ? WHERE user_id = ? AND purpose = 'reset_password'",
                (now, user["id"]),
            )
            connection.commit()
        return user, str(user["return_to"])

    @staticmethod
    def _verification_code(raw: str) -> str:
        value = raw.strip()
        if len(value) != 6 or not all("0" <= character <= "9" for character in value):
            raise AuthInputError("invalid_email_code")
        return value

    def _send_verification_email(self, email: str, full_name: str, code: str) -> None:
        _send_transactional_email(
            email,
            "TMCRA verification code / TMCRA 邮箱验证码",
            f"Hello {full_name},\n\nYour TMCRA verification code is {code}. "
            f"It expires in {EMAIL_TOKEN_SECONDS // 60} minutes.\n\n"
            f"TMCRA 邮箱验证码：{code}。有效期 {EMAIL_TOKEN_SECONDS // 60} 分钟。\n\n"
            "TMCRA staff will never ask you for this code. If you did not request it, ignore this email.\n"
            "TMCRA 工作人员不会向你索要验证码。如非本人操作，请忽略此邮件。",
            _branded_code_email_html(
                recipient_name=full_name,
                preheader=f"Your TMCRA verification code is {code}.",
                eyebrow="TMCRA ACCOUNT SECURITY / 账户安全",
                title="Verify your email",
                title_zh="验证您的邮箱",
                explanation="Use the following code to finish creating your TMCRA account.",
                explanation_zh="请输入以下验证码，完成 TMCRA 账户创建。",
                code=code,
                expires_minutes=EMAIL_TOKEN_SECONDS // 60,
            ),
        )

    def _send_password_reset_email(self, email: str, full_name: str, code: str) -> None:
        _send_transactional_email(
            email,
            "TMCRA password reset code / TMCRA 密码重置码",
            f"Hello {full_name},\n\nYour TMCRA password reset code is {code}. "
            f"It expires in {PASSWORD_RESET_SECONDS // 60} minutes.\n\n"
            f"TMCRA 密码重置码：{code}。有效期 {PASSWORD_RESET_SECONDS // 60} 分钟。\n\n"
            "TMCRA staff will never ask you for this code. If you did not request it, ignore this email.\n"
            "TMCRA 工作人员不会向你索要验证码。如非本人操作，请忽略此邮件。",
            _branded_code_email_html(
                recipient_name=full_name,
                preheader=f"Your TMCRA password reset code is {code}.",
                eyebrow="TMCRA ACCOUNT SECURITY / 账户安全",
                title="Reset your password",
                title_zh="重置您的密码",
                explanation="Use the following code to choose a new TMCRA password.",
                explanation_zh="请输入以下重置码，为 TMCRA 账户设置新密码。",
                code=code,
                expires_minutes=PASSWORD_RESET_SECONDS // 60,
            ),
        )

    def _google_start(self, parsed_path: urllib.parse.SplitResult) -> None:
        if not _google_configured():
            self._json_error(404, "google_login_not_configured")
            return
        if self.command not in ("GET", "HEAD"):
            self._json_error(405, "method_not_allowed")
            return
        return_to = self._return_to_from_query(
            parsed_path.query, DEFAULT_ACCOUNT_RETURN_TO
        )
        try:
            self._consume_auth_limits("oauth_start", "google", 60, 20, 600)
        except RateLimitError:
            self._json_error(429, "too_many_requests")
            return
        state = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(48)
        challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).decode("ascii").rstrip("=")
        now = int(time.time())
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            connection.execute(
                "DELETE FROM account_oauth_states WHERE expires_at <= ?", (now,)
            )
            connection.execute(
                """INSERT INTO account_oauth_states (
                       state_hash, return_to, code_verifier, created_at, expires_at
                   ) VALUES (?, ?, ?, ?, ?)""",
                (self._secret_hash("oauth_state", state), return_to, verifier, now, now + OAUTH_STATE_SECONDS),
            )
            connection.commit()
        self._pending_set_cookies.append(
            self._cookie(OAUTH_STATE_COOKIE, state, OAUTH_STATE_SECONDS)
        )
        authorization_url = GOOGLE_AUTHORIZATION_ENDPOINT + "?" + urllib.parse.urlencode(
            {
                "client_id": GOOGLE_CLIENT_ID,
                "redirect_uri": GOOGLE_REDIRECT_URI,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        self._redirect(authorization_url)

    def _google_callback(self, parsed_path: urllib.parse.SplitResult) -> None:
        if not _google_configured():
            self._json_error(404, "google_login_not_configured")
            return
        if self.command not in ("GET", "HEAD"):
            self._json_error(405, "method_not_allowed")
            return
        try:
            state = self._single_query_value(parsed_path.query, "state", 256)
            code = self._single_query_value(parsed_path.query, "code", 4096)
            provider_error = self._single_query_value(parsed_path.query, "error", 256)
            if provider_error or not state or not code:
                raise OAuthProviderError("google_authorization_rejected")
            cookie_state = self._cookie_value(OAUTH_STATE_COOKIE)
            if not cookie_state or not hmac.compare_digest(cookie_state, state):
                raise OAuthProviderError("google_browser_state_invalid")
            self._pending_set_cookies.append(self._clear_cookie(OAUTH_STATE_COOKIE))
            now = int(time.time())
            with _database() as connection:
                connection.execute("BEGIN IMMEDIATE")
                oauth_state = connection.execute(
                    "SELECT return_to, code_verifier, expires_at FROM account_oauth_states WHERE state_hash = ?",
                    (self._secret_hash("oauth_state", state),),
                ).fetchone()
                connection.execute(
                    "DELETE FROM account_oauth_states WHERE state_hash = ?",
                    (self._secret_hash("oauth_state", state),),
                )
                connection.commit()
            if not oauth_state or int(oauth_state["expires_at"]) <= now:
                raise OAuthProviderError("google_state_invalid")
            profile = _google_identity_from_code(code, str(oauth_state["code_verifier"]))
            user = self._google_user(profile)
            self._create_session(str(user["id"]))
            self._redirect(
                self._safe_return_to(
                    str(oauth_state["return_to"]), DEFAULT_ACCOUNT_RETURN_TO
                )
            )
        except (OAuthProviderError, AuthInputError, sqlite3.IntegrityError) as error:
            self.log_error("Google login failed: %s", error)
            self._auth_page(
                DEFAULT_ACCOUNT_RETURN_TO,
                mode="login",
                error="Google sign-in could not be completed. / 无法完成 Google 登录。",
                status=400,
            )

    def _google_user(self, profile: dict[str, str]) -> sqlite3.Row:
        subject = profile["subject"]
        email = self._normalize_email(profile["email"])
        full_name = self._normalize_full_name(profile["full_name"])
        now = int(time.time())
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            identity = connection.execute(
                """SELECT u.id, u.email, u.full_name
                   FROM account_oauth_identities i
                   JOIN account_users u ON u.id = i.user_id
                   WHERE i.provider = 'google' AND i.provider_subject = ?""",
                (subject,),
            ).fetchone()
            if identity:
                connection.execute(
                    """UPDATE account_oauth_identities
                       SET email = ?, updated_at = ?
                       WHERE provider = 'google' AND provider_subject = ?""",
                    (email, now, subject),
                )
                connection.execute(
                    "UPDATE account_users SET full_name = ?, updated_at = ? WHERE id = ?",
                    (full_name, now, identity["id"]),
                )
                connection.commit()
                return connection.execute(
                    "SELECT id, email, full_name FROM account_users WHERE id = ?",
                    (identity["id"],),
                ).fetchone()
            user = connection.execute(
                "SELECT id, email, full_name FROM account_users WHERE email = ?", (email,)
            ).fetchone()
            if user:
                user_id = str(user["id"])
                connection.execute(
                    """UPDATE account_users
                       SET email_verified_at = COALESCE(email_verified_at, ?),
                           full_name = ?, updated_at = ? WHERE id = ?""",
                    (now, full_name, now, user_id),
                )
            else:
                user_id = "usr_" + secrets.token_hex(16)
                salt = secrets.token_bytes(16)
                disabled_hash = secrets.token_bytes(32)
                connection.execute(
                    """INSERT INTO account_users (
                           id, email, full_name, password_hash, password_salt,
                           password_iterations, password_enabled, email_verified_at,
                           created_at, updated_at
                       ) VALUES (?, ?, ?, ?, ?, ?, 0, ?, ?, ?)""",
                    (
                        user_id,
                        email,
                        full_name,
                        disabled_hash,
                        salt,
                        PASSWORD_ITERATIONS,
                        now,
                        now,
                        now,
                    ),
                )
            connection.execute(
                """INSERT INTO account_oauth_identities (
                       provider, provider_subject, user_id, email, created_at, updated_at
                   ) VALUES ('google', ?, ?, ?, ?, ?)""",
                (subject, user_id, email, now, now),
            )
            connection.commit()
            return connection.execute(
                "SELECT id, email, full_name FROM account_users WHERE id = ?", (user_id,)
            ).fetchone()

    def _session_identity(self) -> dict[str, str | None] | None:
        token = self._cookie_value(SESSION_COOKIE)
        if not token or len(token) > 128:
            return None
        token_hash = self._token_hash(token)
        now = int(time.time())
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                """SELECT s.token_hash, s.created_at, s.expires_at,
                          s.absolute_expires_at, u.id, u.email, u.full_name
                   FROM account_sessions s
                   JOIN account_users u ON u.id = s.user_id
                   WHERE s.token_hash = ?""",
                (token_hash,),
            ).fetchone()
            if not row:
                connection.commit()
                return None
            if int(row["expires_at"]) <= now or int(row["absolute_expires_at"]) <= now:
                connection.execute(
                    "DELETE FROM account_sessions WHERE token_hash = ?", (token_hash,)
                )
                connection.commit()
                self._pending_set_cookies.append(self._clear_cookie(SESSION_COOKIE))
                return None
            next_expiry = min(now + SESSION_IDLE_SECONDS, int(row["absolute_expires_at"]))
            if now - int(row["created_at"]) >= SESSION_ROTATE_SECONDS:
                replacement = secrets.token_urlsafe(32)
                replacement_hash = self._token_hash(replacement)
                connection.execute(
                    "DELETE FROM account_sessions WHERE token_hash = ?", (token_hash,)
                )
                connection.execute(
                    """INSERT INTO account_sessions (
                           token_hash, user_id, created_at, last_seen_at,
                           expires_at, absolute_expires_at
                       ) VALUES (?, ?, ?, ?, ?, ?)""",
                    (
                        replacement_hash,
                        row["id"],
                        now,
                        now,
                        next_expiry,
                        row["absolute_expires_at"],
                    ),
                )
                self._pending_set_cookies.append(
                    self._cookie(SESSION_COOKIE, replacement, max(0, next_expiry - now))
                )
            else:
                connection.execute(
                    """UPDATE account_sessions
                       SET last_seen_at = ?, expires_at = ? WHERE token_hash = ?""",
                    (now, next_expiry, token_hash),
                )
            connection.execute(
                "DELETE FROM account_sessions WHERE absolute_expires_at <= ?", (now,)
            )
            connection.commit()
        return {"id": row["id"], "email": row["email"], "full_name": row["full_name"]}

    def _create_session(self, user_id: str) -> None:
        token = secrets.token_urlsafe(32)
        token_hash = self._token_hash(token)
        now = int(time.time())
        absolute_expiry = now + SESSION_ABSOLUTE_SECONDS
        expiry = min(now + SESSION_IDLE_SECONDS, absolute_expiry)
        previous = self._cookie_value(SESSION_COOKIE)
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            if previous:
                connection.execute(
                    "DELETE FROM account_sessions WHERE token_hash = ?",
                    (self._token_hash(previous),),
                )
            connection.execute(
                """INSERT INTO account_sessions (
                       token_hash, user_id, created_at, last_seen_at,
                       expires_at, absolute_expires_at
                   ) VALUES (?, ?, ?, ?, ?, ?)""",
                (token_hash, user_id, now, now, expiry, absolute_expiry),
            )
            connection.commit()
        self._pending_set_cookies.append(
            self._cookie(SESSION_COOKIE, token, expiry - now)
        )

    def _delete_current_session(self) -> None:
        token = self._cookie_value(SESSION_COOKIE)
        if not token:
            return
        with _database() as connection:
            connection.execute(
                "DELETE FROM account_sessions WHERE token_hash = ?",
                (self._token_hash(token),),
            )

    def _consume_auth_limits(
        self,
        action: str,
        email: str,
        ip_limit: int,
        account_limit: int,
        window_seconds: int,
    ) -> None:
        bucket = int(time.time()) // window_seconds * window_seconds
        ip_key = self._rate_key(f"{action}:ip:{self._client_ip()}")
        account_key = self._rate_key(f"{action}:account:{email}")
        with _database() as connection:
            connection.execute("BEGIN IMMEDIATE")
            counts: list[int] = []
            for key in (ip_key, account_key):
                connection.execute(
                    """INSERT INTO account_rate_limits (limit_key, bucket_start, request_count)
                       VALUES (?, ?, 1)
                       ON CONFLICT(limit_key, bucket_start)
                       DO UPDATE SET request_count = request_count + 1""",
                    (key, bucket),
                )
                counts.append(
                    int(
                        connection.execute(
                            """SELECT request_count FROM account_rate_limits
                               WHERE limit_key = ? AND bucket_start = ?""",
                            (key, bucket),
                        ).fetchone()[0]
                    )
                )
            connection.execute(
                "DELETE FROM account_rate_limits WHERE bucket_start < ?",
                (bucket - max(window_seconds * 4, 86400),),
            )
            connection.commit()
        if counts[0] > ip_limit or counts[1] > account_limit:
            raise RateLimitError()

    def _require_auth_api_client(self) -> None:
        origin_headers = self.headers.get_all("Origin", [])
        fetch_site_headers = self.headers.get_all("Sec-Fetch-Site", [])
        if origin_headers or fetch_site_headers:
            self._require_same_origin()
            return
        desktop_headers = self.headers.get_all(DESKTOP_CLIENT_HEADER, [])
        if (
            len(desktop_headers) == 1
            and DESKTOP_CLIENT_PATTERN.fullmatch(desktop_headers[0].strip())
        ):
            return
        raise AuthInputError("origin_mismatch")

    def _require_same_origin(self) -> None:
        public_host = self._public_host()
        origin = self.headers.get("Origin", "")
        fetch_site = self.headers.get("Sec-Fetch-Site")
        expected_origin = f"https://{public_host}" if public_host else ""
        # Sandboxed desktop/webview documents serialize their otherwise same-site
        # origin as "null". Fetch Metadata remains browser-controlled in that case.
        opaque_same_origin = origin == "null" and fetch_site == "same-origin"
        if not public_host or (origin != expected_origin and not opaque_same_origin):
            origin_class = "missing" if not origin else "opaque" if origin == "null" else "mismatch"
            self.log_error(
                "authentication origin mismatch: received=%s expected=%r",
                origin_class,
                expected_origin if public_host else "<untrusted-host>",
            )
            raise AuthInputError("origin_mismatch")
        if fetch_site and fetch_site != "same-origin":
            raise AuthInputError("cross_site_request")

    def _require_csrf(self, submitted: str) -> None:
        cookie = self._cookie_value(CSRF_COOKIE)
        if not submitted or not cookie or not hmac.compare_digest(submitted, cookie):
            raise AuthInputError("invalid_csrf")
        pieces = submitted.split(".")
        if len(pieces) != 3:
            raise AuthInputError("invalid_csrf")
        timestamp, nonce, signature = pieces
        try:
            issued_at = int(timestamp)
        except ValueError as error:
            raise AuthInputError("invalid_csrf") from error
        now = int(time.time())
        if issued_at > now + 60 or now - issued_at > CSRF_LIFETIME_SECONDS:
            raise AuthInputError("expired_csrf")
        expected = self._sign(f"csrf:{timestamp}.{nonce}")
        if not hmac.compare_digest(signature, expected):
            raise AuthInputError("invalid_csrf")

    def _new_csrf_token(self) -> str:
        value = f"{int(time.time())}.{secrets.token_urlsafe(24)}"
        return f"{value}.{self._sign('csrf:' + value)}"

    def _read_form(self) -> dict[str, str]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/x-www-form-urlencoded":
            raise AuthInputError("unsupported_media_type")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise AuthInputError("invalid_content_length") from error
        if length <= 0 or length > MAXIMUM_AUTH_FORM_BYTES:
            raise AuthInputError("invalid_form_size")
        try:
            text = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise AuthInputError("invalid_form_encoding") from error
        try:
            parsed = urllib.parse.parse_qs(
                text, keep_blank_values=True, max_num_fields=12
            )
        except ValueError as error:
            raise AuthInputError("invalid_form") from error
        if any(len(values) != 1 for values in parsed.values()):
            raise AuthInputError("duplicate_form_field")
        return {key: values[0] for key, values in parsed.items()}

    def _read_json(self, maximum_bytes: int) -> dict[str, object]:
        content_type = self.headers.get("Content-Type", "").split(";", 1)[0].strip().lower()
        if content_type != "application/json":
            raise AuthInputError("unsupported_media_type")
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError as error:
            raise AuthInputError("invalid_content_length") from error
        if length <= 0 or length > maximum_bytes:
            raise AuthInputError("invalid_json_size")
        try:
            source = self.rfile.read(length).decode("utf-8")
        except UnicodeDecodeError as error:
            raise AuthInputError("invalid_json_encoding") from error

        def unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
            result: dict[str, object] = {}
            for key, value in pairs:
                if key in result:
                    raise AuthInputError("duplicate_json_field")
                result[key] = value
            return result

        try:
            payload = json.loads(source, object_pairs_hook=unique_object)
        except json.JSONDecodeError as error:
            raise AuthInputError("invalid_json") from error
        if not isinstance(payload, dict):
            raise AuthInputError("invalid_json_object")
        return payload

    @staticmethod
    def _require_json_fields(
        payload: dict[str, object], allowed: set[str], required: set[str]
    ) -> None:
        if not required.issubset(payload) or not set(payload).issubset(allowed):
            raise AuthInputError("invalid_json_fields")

    @staticmethod
    def _marketing_idempotency_key(raw: object) -> str:
        if not isinstance(raw, str) or not _CAMPAIGN_IDEMPOTENCY_PATTERN.fullmatch(raw):
            raise AuthInputError("invalid_idempotency_key")
        return raw

    @staticmethod
    def _marketing_subject(raw: object) -> str:
        if not isinstance(raw, str):
            raise AuthInputError("invalid_marketing_subject")
        value = " ".join(unicodedata.normalize("NFKC", raw).split())
        if (
            not value
            or len(value) > MAXIMUM_MARKETING_SUBJECT_CHARACTERS
            or any(unicodedata.category(character).startswith("C") for character in value)
        ):
            raise AuthInputError("invalid_marketing_subject")
        return value

    @staticmethod
    def _marketing_body(raw: object, maximum: int, kind: str) -> str:
        if not isinstance(raw, str):
            raise AuthInputError(f"invalid_marketing_{kind}")
        value = raw.strip()
        if (
            not value
            or len(value) > maximum
            or any(
                unicodedata.category(character).startswith("C")
                and character not in "\r\n\t"
                for character in value
            )
        ):
            raise AuthInputError(f"invalid_marketing_{kind}")
        return value

    @staticmethod
    def _normalize_email(raw: str) -> str:
        value = unicodedata.normalize("NFKC", raw).strip()
        if len(value) > 254 or value.count("@") != 1:
            raise AuthInputError("invalid_email")
        local, domain = value.rsplit("@", 1)
        if (
            not local
            or len(local) > 64
            or local.startswith(".")
            or local.endswith(".")
            or ".." in local
            or any(character not in EMAIL_LOCAL_CHARACTERS for character in local)
        ):
            raise AuthInputError("invalid_email")
        try:
            ascii_domain = domain.rstrip(".").encode("idna").decode("ascii").lower()
        except UnicodeError as error:
            raise AuthInputError("invalid_email") from error
        if (
            not ascii_domain
            or len(ascii_domain) > 253
            or "." not in ascii_domain
            or any(
                not label
                or len(label) > 63
                or label.startswith("-")
                or label.endswith("-")
                or not all(character.isalnum() or character == "-" for character in label)
                for label in ascii_domain.split(".")
            )
        ):
            raise AuthInputError("invalid_email")
        return f"{local.casefold()}@{ascii_domain}"

    @staticmethod
    def _normalize_full_name(raw: str) -> str:
        value = " ".join(unicodedata.normalize("NFKC", raw).split())
        if (
            not value
            or len(value) > 80
            or any(unicodedata.category(character).startswith("C") for character in value)
        ):
            raise AuthInputError("invalid_name")
        return value

    @staticmethod
    def _require_strong_password(password: str, email: str) -> None:
        del email
        if len(password) < 8 or len(password) > 128:
            raise AuthInputError("weak_password")
        has_english_letter = any(
            "a" <= character.casefold() <= "z" for character in password
        )
        has_number = any("0" <= character <= "9" for character in password)
        if not has_english_letter or not has_number:
            raise AuthInputError("weak_password")

    @staticmethod
    def _password_hash(password: str, salt: bytes, iterations: int) -> bytes:
        return hashlib.pbkdf2_hmac(
            "sha256", password.encode("utf-8"), salt, iterations, dklen=32
        )

    def _token_hash(self, token: str) -> str:
        return self._secret_hash("session", token)

    def _secret_hash(self, namespace: str, value: str) -> str:
        return hmac.new(
            SESSION_SECRET.encode("utf-8"),
            f"{namespace}:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _rate_key(self, value: str) -> str:
        return hmac.new(
            SESSION_SECRET.encode("utf-8"),
            f"rate:{value}".encode("utf-8"),
            hashlib.sha256,
        ).hexdigest()

    def _sign(self, value: str) -> str:
        digest = hmac.new(
            SESSION_SECRET.encode("utf-8"), value.encode("utf-8"), hashlib.sha256
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")

    @staticmethod
    def _normalized_ip(value: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        address = ipaddress.ip_address(value.split("%", 1)[0])
        if isinstance(address, ipaddress.IPv6Address) and address.ipv4_mapped:
            return address.ipv4_mapped
        return address

    def _client_ip(self) -> ipaddress.IPv4Address | ipaddress.IPv6Address:
        direct = self._normalized_ip(str(self.client_address[0]))
        if not any(direct in network for network in TRUSTED_PROXY_NETWORKS):
            return direct
        values = self.headers.get_all("X-TMCRA-Client-IP", [])
        if len(values) != 1 or "," in values[0] or len(values[0]) > 64:
            return direct
        try:
            return self._normalized_ip(values[0].strip())
        except ValueError:
            return direct

    @staticmethod
    def _internal_ip_allowed(
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
    ) -> bool:
        return any(address in network for network in INTERNAL_ALLOWED_NETWORKS)

    def _record_internal_gateway_access(
        self,
        path: str,
        address: ipaddress.IPv4Address | ipaddress.IPv6Address,
        outcome: str,
        account_user_id: str | None = None,
        *,
        event_type: str = "internal_access",
        response_status: int | None = None,
        final_outcome: str | None = None,
    ) -> bool:
        now = int(time.time())
        client_ip_hash = self._secret_hash("internal-client-ip", str(address))
        rate_limited = False
        if outcome in {"ip_denied", "authentication_required"}:
            allowed, rate_limited = _allow_unauthenticated_internal_audit_write(
                client_ip_hash, event_type, outcome, now
            )
            if not allowed:
                return False
        try:
            with _database() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """INSERT INTO internal_gateway_audit (
                           id, occurred_at, client_ip_hash, method, path, outcome,
                           account_user_id, event_type, response_status,
                           final_outcome, rate_limited
                       ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        secrets.token_hex(16),
                        now,
                        client_ip_hash,
                        self.command,
                        path[:512],
                        outcome,
                        account_user_id,
                        event_type[:64],
                        response_status,
                        final_outcome[:128] if final_outcome else None,
                        int(rate_limited),
                    ),
                )
                _prune_internal_gateway_audit(connection, now)
                connection.commit()
            return True
        except (sqlite3.Error, OSError, RuntimeError) as error:
            self.log_error("internal audit unavailable: %s", type(error).__name__)
            return False

    @staticmethod
    def _upstream_authorization_outcome(status: int) -> str:
        if 200 <= status < 400:
            return "authorized"
        if status == 401:
            return "upstream_authentication_required"
        if status == 403:
            return "upstream_forbidden"
        if status == 404:
            return "upstream_not_found"
        if 400 <= status < 500:
            return "upstream_rejected"
        return "upstream_error"

    def _marketing_authenticated(self) -> bool:
        authorization_headers = self.headers.get_all("Authorization", [])
        if len(authorization_headers) != 1 or not MARKETING_API_TOKEN:
            return False
        authorization = authorization_headers[0]
        if not authorization.startswith("Bearer "):
            return False
        token = authorization[7:]
        return len(token) <= 512 and hmac.compare_digest(token, MARKETING_API_TOKEN)

    def _public_host(self) -> str | None:
        request_host = self._trusted_public_host(self.headers.get("Host", ""))
        if request_host is None:
            return None
        for value in (
            self.headers.get("X-TMCRA-Public-Host", ""),
            self.headers.get("Tencent-Acceleration-Domain", ""),
            self.headers.get("X-Forwarded-Host", ""),
        ):
            trusted = self._trusted_public_host(value)
            if trusted:
                return trusted
        return request_host

    @staticmethod
    def _matches_prefix(path: str, prefixes: tuple[str, ...]) -> bool:
        return any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)

    @staticmethod
    def _trusted_public_host(value: str) -> str | None:
        authority = _normalized_authority(value)
        return authority if authority in PUBLIC_HOSTS else None

    @staticmethod
    def _safe_return_to(value: str, fallback: str) -> str:
        if (
            not value.startswith("/")
            or value.startswith("//")
            or len(value) > 2048
            or "\\" in value
            or any(ord(character) < 32 or ord(character) == 127 for character in value)
        ):
            return fallback
        try:
            parsed = urllib.parse.urlsplit(value)
        except ValueError:
            return fallback
        if parsed.scheme or parsed.netloc or parsed.path in {
            "/signin-with-chatgpt",
            "/signout-with-chatgpt",
            "/login",
            "/register",
            "/logout",
            "/verify-email",
            "/resend-verification",
            "/forgot-password",
            "/reset-password",
            "/oauth/google/start",
            "/oauth/google/callback",
            "/callback",
        }:
            return fallback
        return urllib.parse.urlunsplit(("", "", parsed.path or "/", parsed.query, parsed.fragment))

    def _return_to_from_query(self, query: str, fallback: str) -> str:
        values = urllib.parse.parse_qs(query, keep_blank_values=True).get("return_to", [])
        return self._safe_return_to(values[0], fallback) if len(values) == 1 else fallback

    @staticmethod
    def _single_query_value(query: str, name: str, maximum: int) -> str | None:
        try:
            values = urllib.parse.parse_qs(
                query, keep_blank_values=True, max_num_fields=12
            ).get(name, [])
        except ValueError as error:
            raise AuthInputError("invalid_query") from error
        if not values:
            return None
        if len(values) != 1 or len(values[0]) > maximum:
            raise AuthInputError("invalid_query")
        return values[0]

    def _cookie_value(self, name: str) -> str | None:
        raw = self.headers.get("Cookie", "")
        if not raw or len(raw) > 4096:
            return None
        cookie = SimpleCookie()
        try:
            cookie.load(raw)
        except Exception:
            return None
        morsel = cookie.get(name)
        return morsel.value if morsel else None

    @staticmethod
    def _cookie(name: str, value: str, max_age: int) -> str:
        return f"{name}={value}; Max-Age={max_age}; {COOKIE_FLAGS}"

    @staticmethod
    def _clear_cookie(name: str) -> str:
        return f"{name}=; Max-Age=0; Expires=Thu, 01 Jan 1970 00:00:00 GMT; {COOKIE_FLAGS}"

    def _health(self) -> None:
        upstream_status = 0
        account_store = False
        marketing_queued = 0
        try:
            connection = http.client.HTTPConnection(UPSTREAM_HOST, UPSTREAM_PORT, timeout=5)
            connection.request("HEAD", "/", headers={"Host": "localhost"})
            response = connection.getresponse()
            upstream_status = response.status
            response.read()
            connection.close()
        except (ConnectionError, TimeoutError, http.client.HTTPException):
            pass
        try:
            with _database() as database:
                database.execute("SELECT 1 FROM account_users LIMIT 1").fetchone()
                marketing_queued = int(
                    database.execute(
                        """SELECT COUNT(*) FROM email_campaign_deliveries
                           WHERE state IN ('queued', 'sending')"""
                    ).fetchone()[0]
                )
            account_store = True
        except sqlite3.Error:
            pass
        healthy = 200 <= upstream_status < 500 and account_store
        payload = json.dumps(
            {
                "ok": healthy,
                "service": "tmcra-official",
                "release": RELEASE_ID,
                "upstreamStatus": upstream_status,
                "accountStore": account_store,
                "registrationReady": _mail_configured(),
                "marketingReady": _marketing_mail_configured(),
                "marketingQueuedDeliveries": marketing_queued,
                "googleLogin": _google_configured(),
            },
            separators=(",", ":"),
        ).encode("utf-8")
        self._raw_response(200 if healthy else 503, payload, "application/json; charset=utf-8")

    def _authentication_required(self) -> None:
        payload = b"Authentication required."
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Basic realm="TMCRA Internal"')
        self._security_headers("text/plain; charset=utf-8")
        self._send_pending_cookies()
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _marketing_authentication_required(self) -> None:
        payload = json.dumps(
            {"ok": False, "error": {"code": "marketing_authentication_required"}},
            separators=(",", ":"),
        ).encode("utf-8")
        self.send_response(401)
        self.send_header("WWW-Authenticate", 'Bearer realm="TMCRA Marketing Email"')
        self._security_headers("application/json; charset=utf-8")
        self._send_pending_cookies()
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _json_response(self, status: int, value: dict[str, object]) -> None:
        payload = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode(
            "utf-8"
        )
        self._raw_response(status, payload, "application/json; charset=utf-8")

    def _json_error(self, status: int, code: str) -> None:
        payload = json.dumps(
            {"ok": False, "error": {"code": code}}, separators=(",", ":")
        ).encode("utf-8")
        self._raw_response(status, payload, "application/json; charset=utf-8")

    def _html_response(self, status: int, body: str) -> None:
        self._raw_response(status, body.encode("utf-8"), "text/html; charset=utf-8")

    def _raw_response(self, status: int, payload: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers(content_type)
        self._send_pending_cookies()
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(payload)

    def _security_headers(
        self,
        content_type: str,
        *,
        cache_control: str = "private, no-store, max-age=0",
    ) -> None:
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", cache_control)
        self.send_header(
            "Content-Security-Policy",
            "default-src 'none'; img-src 'self'; style-src 'unsafe-inline'; "
            "form-action 'self'; base-uri 'none'; frame-ancestors 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        self.send_header("Strict-Transport-Security", STRICT_TRANSPORT_SECURITY)

    def _send_pending_cookies(self) -> None:
        for cookie in getattr(self, "_pending_set_cookies", []):
            self.send_header("Set-Cookie", cookie)

    def _redirect(self, destination: str) -> None:
        self.send_response(303)
        self.send_header("Location", destination)
        self.send_header("Cache-Control", "private, no-store, max-age=0")
        self.send_header("Strict-Transport-Security", STRICT_TRANSPORT_SECURITY)
        self._send_pending_cookies()
        self.send_header("Content-Length", "0")
        self.send_header("Connection", "close")
        self.end_headers()

    @staticmethod
    def _page_shell(title: str, content: str) -> str:
        return f"""<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="color-scheme" content="light"><link rel="icon" href="/favicon.svg">
<title>{html.escape(title)}</title><style>
:root{{color-scheme:light;background:#e9eeec;color:#17201d;font:16px/1.5 Inter,ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif}}*{{box-sizing:border-box;letter-spacing:0}}html,body{{min-height:100%}}body{{margin:0;background:#e9eeec}}a{{color:#245f9c;text-underline-offset:3px}}button,input{{font:inherit}}.account-frame{{display:grid;min-height:100vh;grid-template-rows:auto 1fr auto}}.account-bar{{display:flex;min-height:72px;align-items:center;justify-content:space-between;gap:24px;padding:0 40px;border-bottom:1px solid #d2d9d5;background:#f8faf9}}.site-brand{{display:inline-flex;align-items:center;gap:11px;color:#17201d;text-decoration:none}}.site-brand img{{width:31px;height:31px;object-fit:contain}}.site-brand span{{display:grid;line-height:1.05}}.site-brand b{{font-size:15px}}.site-brand small{{margin-top:4px;color:#6a746f;font-size:9px;font-weight:750}}.account-bar nav{{display:flex;align-items:center;gap:20px}}.account-bar nav a{{color:#53605a;text-decoration:none;font-size:13px;font-weight:650}}.account-bar nav a:hover{{color:#17201d}}.account-stage{{display:grid;place-items:center;padding:48px 24px}}.auth-panel,.card{{width:min(460px,100%);padding:38px;border:1px solid #cfd7d3;border-radius:8px;background:#fff;box-shadow:0 22px 60px rgba(23,32,29,.10)}}.narrow{{max-width:460px}}.auth-switch{{display:grid;grid-template-columns:1fr 1fr;gap:4px;margin-bottom:34px;padding:4px;border:1px solid #dde3e0;border-radius:8px;background:#f3f6f4}}.auth-switch a{{display:flex;min-height:40px;align-items:center;justify-content:center;gap:6px;border:1px solid transparent;border-radius:6px;color:#66716b;text-decoration:none;font-size:13px;font-weight:700}}.auth-switch a span{{font-size:12px;font-weight:600}}.auth-switch a[aria-current="page"]{{border-color:#cbd4cf;color:#17201d;background:#fff;box-shadow:0 4px 12px rgba(23,32,29,.08)}}.auth-heading{{margin-bottom:24px}}.eyebrow,.panel-kicker{{margin:0 0 9px;color:#25715a;font-size:11px;font-weight:800}}.panel-kicker{{padding-bottom:16px;border-bottom:1px solid #e2e7e4}}h1{{margin:0 0 22px;font-size:28px;line-height:1.18}}.auth-heading h1{{margin-bottom:10px}}.auth-heading h1 span{{display:block;margin-top:7px;color:#5c6862;font-size:17px;font-weight:600}}h2{{margin:0 0 16px;font-size:18px}}.supporting{{margin:0 0 23px;color:#5e6964;font-size:14px;line-height:1.65}}.auth-heading .supporting{{margin:0}}form{{display:grid;gap:17px}}label{{display:grid;gap:7px;color:#39443f;font-size:13px;font-weight:700}}input{{width:100%;min-height:48px;padding:10px 13px;border:1px solid #aeb9b3;border-radius:6px;background:#fff;color:#17201d}}input:hover{{border-color:#84938b}}input:focus{{border-color:#1b7158;outline:3px solid #d8eee6}}button,.oauth{{display:flex;min-height:48px;align-items:center;justify-content:center;border-radius:6px;font-weight:750}}button{{border:1px solid #0d5e49;background:#176e57;color:#fff;padding:10px 16px;cursor:pointer}}button:hover{{background:#105f4b}}button:focus-visible,.oauth:focus-visible,.auth-switch a:focus-visible,.account-bar a:focus-visible{{outline:3px solid #cce2f5;outline-offset:2px}}.oauth{{gap:10px;border:1px solid #aeb9b3;color:#26302b;text-decoration:none;background:#fff}}.oauth:hover{{border-color:#77867e;background:#f8faf9}}.oauth span{{display:grid;width:25px;height:25px;place-items:center;border:1px solid #c9d0cc;border-radius:50%;font-size:13px;font-weight:800}}.divider{{display:flex;align-items:center;gap:12px;margin:21px 0;color:#79837e;font-size:11px}}.divider::before,.divider::after{{height:1px;flex:1;background:#dfe5e2;content:""}}.form-meta{{display:flex;justify-content:flex-end;margin-top:-7px;font-size:12px}}.hint{{margin:-8px 0 0;color:#6b7670;font-size:12px}}.error{{margin:0 0 20px;padding:12px 14px;border:1px solid #efc7cd;border-left:3px solid #bd4050;border-radius:6px;background:#fff3f5;color:#812733;font-size:13px}}.alternate{{margin-top:22px;padding-top:18px;border-top:1px solid #e2e7e4;text-align:center;font-size:13px}}.account-assurance{{display:flex;align-items:center;gap:10px;margin-top:24px;padding-top:18px;border-top:1px solid #e2e7e4;color:#58645e}}.account-assurance>span{{width:9px;height:9px;border-radius:50%;background:#30a36f;box-shadow:0 0 0 4px #e2f4eb}}.account-assurance p{{margin:0;font-size:11px;font-weight:650;line-height:1.35}}.account-assurance small{{color:#7a847f;font-size:10px;font-weight:550}}.account-footer{{display:flex;align-items:center;justify-content:space-between;gap:20px;padding:20px 40px;border-top:1px solid #d2d9d5;color:#77817c;font-size:11px}}.account-footer nav{{display:flex;gap:16px}}.account-footer a{{color:#65716b;text-decoration:none}}@media(max-width:680px){{.account-bar{{min-height:64px;padding:0 20px}}.account-bar nav a:first-child{{display:none}}.account-stage{{align-items:start;padding:18px 0 0}}.auth-panel,.card{{width:100%;min-height:calc(100vh - 130px);padding:30px 22px;border-right:0;border-left:0;border-radius:0;box-shadow:none}}.auth-switch{{margin-bottom:28px}}.account-footer{{padding:16px 20px}}.account-footer>span{{display:none}}}}@media(prefers-reduced-motion:reduce){{*{{scroll-behavior:auto!important;transition-duration:.01ms!important;animation-duration:.01ms!important;animation-iteration-count:1!important}}}}
</style></head><body><div class="account-frame"><header class="account-bar"><a class="site-brand" href="/" aria-label="TMCRA home"><img src="/brand/tmcra-mark.png" alt=""><span><b>TMCRA</b><small>ACCOUNT</small></span></a><nav aria-label="Account navigation"><a href="/">Home / 首页</a><a href="/docs">Docs / 文档</a></nav></header><div class="account-stage">{content}</div><footer class="account-footer"><span>TMCRA ACCOUNT</span><nav><a href="/developers">Developers / 开发者</a><a href="/docs">Documentation / 文档</a></nav></footer></div></body></html>"""


class AuthInputError(Exception):
    pass


class RateLimitError(Exception):
    pass


class UnverifiedAccountError(Exception):
    pass


class EmailDeliveryError(Exception):
    pass


class OAuthProviderError(Exception):
    pass


def main() -> None:
    os.umask(0o077)
    require_configuration()
    server = ThreadingHTTPServer(("0.0.0.0", PUBLIC_PORT), TmcraProxyHandler)
    server.daemon_threads = True
    _start_marketing_worker()
    print(
        f"TMCRA gateway listening on 0.0.0.0:{PUBLIC_PORT}; "
        f"upstream={UPSTREAM_HOST}:{UPSTREAM_PORT}",
        flush=True,
    )
    try:
        server.serve_forever(poll_interval=0.5)
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
        _stop_marketing_worker()


if __name__ == "__main__":
    main()
