"""Minimal JSON HTTP transport for local integration testing.

Production deployment should place this application behind TLS termination,
trusted authentication middleware, rate limiting, and an appropriate process
supervisor. The server binds to loopback by default.
"""

from __future__ import annotations

import json
import mimetypes
from pathlib import Path
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
    static_dir: str | None = None,
) -> ThreadingHTTPServer:
    static_root = Path(static_dir).resolve() if static_dir else None

    class Handler(BaseHTTPRequestHandler):
        server_version = "AstraTradeHTTP/0.1"

        def _request(self, body: dict | None = None) -> Request:
            parsed = urlparse(self.path)
            query = {key: values[-1] for key, values in parse_qs(parsed.query).items() if values}
            user_id = resolve_user(self.headers.get("Authorization"))
            return Request(
                self.command,
                self.path,
                user_id=user_id,
                body=body,
                query=query,
                authorization=self.headers.get("Authorization"),
                cookie=self.headers.get("Cookie"),
                origin=self.headers.get("Origin"),
            )

        def _write_response(self, status: int, body: dict, headers: dict | None = None) -> None:
            encoded = json.dumps(body, ensure_ascii=False).encode("utf-8")
            if status == 204:
                encoded = b""
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(encoded)))
            for name, value in (headers or {}).items():
                self.send_header(name, value)
            self.end_headers()
            if status != 204:
                self.wfile.write(encoded)

        def do_GET(self) -> None:  # noqa: N802
            if static_root and self.path == "/":
                self._write_static(static_root / "index.html", cache=False)
                return
            if static_root and not self.path.startswith("/v1/") and self.path != "/health":
                candidate = (static_root / urlparse(self.path).path.lstrip("/")).resolve()
                if static_root in candidate.parents and candidate.is_file():
                    self._write_static(candidate, cache=candidate.name != "index.html")
                    return
                self._write_static(static_root / "index.html", cache=False)
                return
            response = api.handle(self._request())
            self._write_response(response.status, dict(response.body), dict(response.headers or {}))

        def _write_static(self, path: Path, cache: bool) -> None:
            if not path.is_file():
                self._write_response(404, {"code": "frontend_not_built", "message": "frontend is not built"})
                return
            content = path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mimetypes.guess_type(path.name)[0] or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.send_header("Cache-Control", "public, max-age=31536000, immutable" if cache else "no-store")
            self.end_headers()
            self.wfile.write(content)

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
            self._write_response(response.status, dict(response.body), dict(response.headers or {}))

        def do_PUT(self) -> None:  # noqa: N802
            self._handle_json_mutation()

        def do_DELETE(self) -> None:  # noqa: N802
            self._handle_json_mutation()

        def _handle_json_mutation(self) -> None:
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
            self._write_response(response.status, dict(response.body), dict(response.headers or {}))

        def log_message(self, format: str, *args: object) -> None:
            return

    return ThreadingHTTPServer((host, port), Handler)
