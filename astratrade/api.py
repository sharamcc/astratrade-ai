"""Framework-neutral application API for the first Web integration slice.

The transport layer (FastAPI, WSGI, or another server) should translate its
requests into ``Request`` objects. ``user_id`` must come from trusted
authentication middleware, never from a request body or query parameter.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, FrozenSet, Mapping, Optional
from urllib.parse import parse_qs, urlparse

from .oauth import OAuthService


@dataclass(frozen=True)
class Request:
    method: str
    path: str
    user_id: Optional[str] = None
    body: Mapping[str, Any] | None = None
    query: Mapping[str, str] | None = None


@dataclass(frozen=True)
class Response:
    status: int
    body: Mapping[str, Any]


class ApplicationAPI:
    """Small adapter exposing only safe OAuth/account operations."""

    def __init__(
        self,
        oauth: OAuthService,
        callback_redirect_uri: str,
        allowed_redirect_uris: FrozenSet[str] | None = None,
        version: str = "dev",
    ):
        self.oauth = oauth
        self.version = version
        self.callback_redirect_uri = callback_redirect_uri
        self.allowed_redirect_uris = allowed_redirect_uris or frozenset(
            {callback_redirect_uri}
        )

    def handle(self, request: Request) -> Response:
        method = request.method.upper()
        path = urlparse(request.path).path
        query = dict(request.query or {})
        if "?" in request.path:
            query.update(
                {
                    key: values[-1]
                    for key, values in parse_qs(urlparse(request.path).query).items()
                    if values
                }
            )
        body = dict(request.body or {})

        try:
            if method == "GET" and path == "/health":
                return Response(
                    200,
                    {"status": "ok", "service": "astratrade-ai", "version": self.version},
                )
            if method == "POST" and path == "/v1/oauth/start":
                return self._start(request.user_id, body)
            if method == "GET" and path == "/v1/oauth/callback":
                return self._callback(query)
            if method == "POST" and path == "/v1/oauth/revoke":
                return self._revoke(request.user_id)
            if method == "GET" and path == "/v1/connection":
                return self._connection(request.user_id)
        except ValueError as error:
            return Response(400, {"error": str(error)})

        return Response(404, {"error": "route_not_found"})

    def _require_user(self, user_id: Optional[str]) -> str:
        if not user_id:
            raise ValueError("authenticated user is required")
        return user_id

    def _start(self, user_id: Optional[str], body: Mapping[str, Any]) -> Response:
        user = self._require_user(user_id)
        redirect_uri = body.get("redirect_uri")
        scopes = body.get("scopes", [])
        if not isinstance(redirect_uri, str) or not redirect_uri:
            raise ValueError("redirect_uri is required")
        if redirect_uri not in self.allowed_redirect_uris:
            raise ValueError("redirect_uri is not registered")
        if not isinstance(scopes, list) or not all(isinstance(item, str) for item in scopes):
            raise ValueError("scopes must be a list of strings")
        start = self.oauth.begin(user, redirect_uri, set(scopes))
        return Response(
            200,
            {
                "state": start.state,
                "authorization_url": start.authorization_url,
                "expires_at": start.expires_at.isoformat(),
            },
        )

    def _callback(self, query: Mapping[str, str]) -> Response:
        state = query.get("state")
        code = query.get("code")
        if not state or not code:
            raise ValueError("state and code are required")
        connection = self.oauth.complete(state, code, self.callback_redirect_uri)
        return Response(
            200,
            {
                "connection_id": connection.connection_id,
                "provider": connection.provider,
                "status": connection.status.value,
                "expires_at": connection.expires_at.isoformat(),
                "scopes": sorted(connection.scopes),
            },
        )

    def _revoke(self, user_id: Optional[str]) -> Response:
        user = self._require_user(user_id)
        self.oauth.revoke(user)
        return Response(204, {})

    def _connection(self, user_id: Optional[str]) -> Response:
        user = self._require_user(user_id)
        connection = self.oauth.repository.get_exchange_connection(user)
        if connection is None:
            return Response(200, {"connected": False})
        usable = self.oauth.has_valid_connection(user)
        return Response(
            200,
            {
                "connected": usable,
                "connection_id": connection.connection_id,
                "provider": connection.provider,
                "status": connection.status.value,
                "expires_at": connection.expires_at.isoformat(),
                "scopes": sorted(connection.scopes),
            },
        )
