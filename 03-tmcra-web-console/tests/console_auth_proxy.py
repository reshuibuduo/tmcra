#!/usr/bin/env python3
"""Loopback-only SIWC header proxy for local Console visual QA."""

from __future__ import annotations

import argparse
import http.server
import urllib.error
import urllib.parse
import urllib.request


HOP_BY_HOP_HEADERS = {
    "connection",
    "content-length",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
}


class ProxyHandler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream = "http://localhost:3001"
    identity_email = "visual-qa@tmcra.local"
    identity_name = "TMCRA Visual QA"

    def do_GET(self) -> None:  # noqa: N802
        self._proxy()

    def do_POST(self) -> None:  # noqa: N802
        self._proxy()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy()

    def log_message(self, format: str, *args: object) -> None:
        print(f"{self.address_string()} - {format % args}", flush=True)

    def _proxy(self) -> None:
        announced_length = int(self.headers.get("content-length", "0") or "0")
        body = self.rfile.read(announced_length) if announced_length else None
        headers = {
            name: value
            for name, value in self.headers.items()
            if name.lower() not in HOP_BY_HOP_HEADERS and name.lower() != "host"
        }
        headers.update(
            {
                "oai-authenticated-user-email": self.identity_email,
                "oai-authenticated-user-full-name": urllib.parse.quote(
                    self.identity_name, safe=""
                ),
                "oai-authenticated-user-full-name-encoding": "percent-encoded-utf-8",
            }
        )
        proxy_origin = self._proxy_origin()
        if "Origin" in headers:
            headers["Origin"] = self.upstream
        if "Referer" in headers:
            headers["Referer"] = headers["Referer"].replace(
                proxy_origin, self.upstream
            )
        request = urllib.request.Request(
            self.upstream + self.path,
            data=body,
            headers=headers,
            method=self.command,
        )
        try:
            response = urllib.request.urlopen(request, timeout=120)
        except urllib.error.HTTPError as error:
            response = error
        except Exception as error:
            payload = f"local preview proxy failed: {type(error).__name__}".encode()
            self.send_response(502)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        payload = response.read()
        self.send_response(response.status)
        for name, value in response.headers.items():
            if name.lower() in HOP_BY_HOP_HEADERS:
                continue
            if name.lower() == "location":
                value = value.replace(self.upstream, proxy_origin)
            self.send_header(name, value)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def _proxy_origin(self) -> str:
        host, port = self.server.server_address[:2]
        return f"http://{host}:{port}"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=3002)
    parser.add_argument("--upstream", default="http://localhost:3001")
    parser.add_argument("--email", default="visual-qa@tmcra.local")
    args = parser.parse_args()
    ProxyHandler.upstream = args.upstream.rstrip("/")
    ProxyHandler.identity_email = args.email
    server = http.server.ThreadingHTTPServer(("127.0.0.1", args.port), ProxyHandler)
    print(f"local Console QA proxy listening on http://127.0.0.1:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
