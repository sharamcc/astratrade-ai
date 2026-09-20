"""Minimal JSON HTTP transport for local integration testing.

Production deployment should place this application behind TLS termination,
trusted authentication middleware, rate limiting, and an appropriate process
supervisor. The server binds to loopback by default.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
from urllib.parse import parse_qs, urlparse

from .api import ApplicationAPI, Request


AuthResolver = Callable[[Optional[str]], Optional[str]]


def create_server(
    api: ApplicationAPI,
    resolve_user: AuthResolver,
    host: str = "127.0.0.1",
    port: int = 8080,
) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        server_version = "AstraTradeHTTP/0.1"

        def _request(self, body: dict | None = None) -> Request:
            parsed = urlparse(self.path)
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
            user_id = resolve_user(self.headers.get("Authorization"))
            return Request(self.command, self.path, user_id=user_id, body=body, query=query)

        def _write_response(self, status: int, body: dict) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if status == 204:
                encoded = b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            self.end_headers()
            if status != 204:
                self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            response = api.handle(self._request())
            self._write_response(response.status, dict(response.body))

        def do_POST(self) -> None:  # noqa: N802
            try:
                length = int(self.headers.get("Content-Length", "0"))
                raw = self.rfile.read(length) if length else b"{}"
                body = json.loads(raw.decode("utf-8"))
                if not isinstance(body, dict):
                    raise ValueError("JSON body must be an object")
            except (ValueError, json.JSONDecodeError, UnicodeDecodeError):
                self._write_response(400, {"error": "invalid_json"})
                return
            response = api.handle(self._request(body))
            self._write_response(response.status, dict(response.body))

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
