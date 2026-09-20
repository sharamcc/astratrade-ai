"""Browser-facing API for the multi-user simulation console."""

from __future__ import annotations

import os
import secrets
import hmac
from datetime import timedelta
from decimal import Decimal, InvalidOperation
from http.cookies import SimpleCookie
from typing import Any, Optional

from .api import ApplicationAPI, Request, Response
from .auth import SessionAuth
from .console_store import ConsoleStore
from .domain import User
from .rate_limit import RateLimiter
from .simulation import current_btc_price, run_once


class ConsoleAPI:
    def __init__(self, product: ConsoleStore, oauth_api: ApplicationAPI, sessions: SessionAuth):
        self.product = product
        self.oauth_api = oauth_api
        self.sessions = sessions
        self.cookie_secure = os.environ.get("ASTRA_COOKIE_SECURE", "0") == "1"
        self.public_origin = os.environ.get("ASTRA_PUBLIC_ORIGIN", "")
        self.rate_limiter = RateLimiter()

    def handle(self, request: Request) -> Response:
        from urllib.parse import urlparse
        path = urlparse(request.path).path
        method = request.method.upper()
        body = dict(request.body or {})
        try:
            if method == "GET" and path == "/health":
                return self.oauth_api.handle(request)
            if method == "POST" and path == "/v1/auth/register":
                if not self.rate_limiter.allow(f"register:{request.remote_addr or 'unknown'}", 10, 3600):
                    return self._error(429, "rate_limited", "注册请求过于频繁")
                return self._register(body)
            if method == "POST" and path == "/v1/auth/login":
                email = str(body.get("email", "")).strip().lower()
                key = f"login:{request.remote_addr or 'unknown'}:{email}"
                if not self.rate_limiter.allow(key, 5, 900):
                    return self._error(429, "rate_limited", "登录失败次数过多，请稍后重试")
                return self._login(body, request.remote_addr)
            if method == "POST" and path == "/v1/auth/logout":
                if not self._csrf_valid(request):
                    return self._error(403, "csrf_failed", "安全校验失败，请刷新页面后重试")
                return self._logout(request)
            if path == "/v1/oauth/callback":
                return self.oauth_api.handle(request)

            user_id = self._user_id(request)
            if not user_id:
                return self._error(401, "authentication_required", "请先登录")
            if method != "GET" and not self._csrf_valid(request):
                return self._error(403, "csrf_failed", "安全校验失败，请刷新页面后重试")
            if method != "GET" and self.public_origin and request.origin and request.origin != self.public_origin:
                return self._error(403, "invalid_origin", "请求来源不受信任")

            if path.startswith("/v1/oauth/") or path == "/v1/connection":
                return self.oauth_api.handle(Request(method, request.path, user_id=user_id, body=body, query=request.query))
            if method == "GET" and path == "/v1/auth/me":
                user = self.product.get_user(user_id)
                return Response(200, {"user_id": user_id, "email": user["email"], "role": user["role"]})
            if method == "POST" and path == "/v1/auth/password":
                self.product.change_password(user_id, str(body.get("current_password", "")), str(body.get("new_password", "")))
                self.sessions.repository.revoke_user_sessions(user_id)
                self.product.audit(user_id, "sessions_revoked_after_password_change", {})
                self.product.connection.commit()
                return Response(204, {}, cookies=self._clear_cookies())
            if method == "GET" and path == "/v1/dashboard":
                return Response(200, self.product.dashboard(user_id, self._price()))
            if path == "/v1/agent" and method == "GET":
                return Response(200, self.product.agent(user_id))
            if path == "/v1/agent" and method == "PUT":
                return Response(200, self.product.save_agent(user_id, Decimal(str(body.get("budget_usdt"))), str(body.get("frequency", "daily"))))
            if path == "/v1/agent/start" and method == "POST":
                agent = self.product.set_agent_status(user_id, "running", __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
                result = run_once(self.product, user_id, self._price())
                return Response(200, {"agent": result and self.product.agent(user_id), "run": result})
            if path == "/v1/agent/stop" and method == "POST":
                return Response(200, self.product.set_agent_status(user_id, "stopped", None))
            if method == "GET" and path == "/v1/orders":
                return Response(200, {"items": self.product.orders(user_id)})
            if method == "GET" and path == "/v1/audit":
                return Response(200, {"items": self.product.audit_events(user_id)})
            if path == "/v1/admin/invites":
                return self._invites(user_id, method, body)
        except (ValueError, InvalidOperation) as error:
            return self._error(400, "invalid_request", str(error))
        except Exception as error:
            return self._error(500, "internal_error", str(error))
        return self._error(404, "route_not_found", "接口不存在")

    def _register(self, body: dict[str, Any]) -> Response:
        user = self.product.create_user(str(body.get("email", "")), str(body.get("password", "")), str(body.get("invite_code", "")))
        self.oauth_api.oauth.repository.save_user(User(user["user_id"], user["email"], risk_confirmed=True))
        token = self.sessions.issue(user["user_id"])
        return Response(201, {"user_id": user["user_id"], "email": user["email"]}, cookies=self._auth_cookies(token))

    def _login(self, body: dict[str, Any], remote_addr: Optional[str]) -> Response:
        user = self.product.authenticate(str(body.get("email", "")), str(body.get("password", "")))
        if not user:
            self.product.audit(None, "login_failed", {"email": str(body.get("email", "")).strip().lower(), "remote_addr": remote_addr})
            self.product.connection.commit()
            return self._error(401, "invalid_credentials", "邮箱或密码错误")
        token = self.sessions.issue(user["user_id"])
        return Response(200, {"user_id": user["user_id"], "email": user["email"], "role": user["role"]}, cookies=self._auth_cookies(token))

    def _logout(self, request: Request) -> Response:
        token = self._token(request)
        if token:
            self.sessions.revoke(token)
        return Response(204, {}, cookies=self._clear_cookies())

    def _invites(self, user_id: str, method: str, body: dict[str, Any]) -> Response:
        user = self.product.get_user(user_id)
        if not user or user["role"] != "admin":
            return self._error(403, "admin_required", "需要管理员权限")
        if method == "GET":
            return Response(200, {"items": self.product.invite_list()})
        if method == "POST":
            code, items = self.product.create_invite(user_id, int(body.get("max_uses", 1)))
            return Response(201, {"code": code, "items": items})
        if method == "DELETE":
            code_hash = str(body.get("id", ""))
            if not code_hash:
                return self._error(400, "invite_id_required", "邀请码 ID 不能为空")
            self.product.deactivate_invite(code_hash)
            return Response(204, {})
        return self._error(405, "method_not_allowed", "不支持该方法")

    def _price(self) -> Decimal:
        return current_btc_price()

    def _token(self, request: Request) -> Optional[str]:
        if request.authorization:
            scheme, _, token = request.authorization.partition(" ")
            if scheme.lower() == "bearer" and token:
                return token
        cookie = SimpleCookie()
        cookie.load(request.cookie or "")
        return cookie["astra_session"].value if "astra_session" in cookie else None

    def _user_id(self, request: Request) -> Optional[str]:
        token = self._token(request)
        if not token:
            return None
        return self.sessions.resolve("Bearer " + token)

    def _cookie(self, token: str, max_age: int = 28800) -> str:
        secure = "; Secure" if self.cookie_secure else ""
        return f"astra_session={token}; HttpOnly; SameSite=Lax; Path=/; Max-Age={max_age}{secure}"

    def _auth_cookies(self, session_token: str) -> tuple[str, ...]:
        csrf = secrets.token_urlsafe(24)
        secure = "; Secure" if self.cookie_secure else ""
        return (self._cookie(session_token), f"astra_csrf={csrf}; SameSite=Lax; Path=/; Max-Age=28800{secure}")

    def _clear_cookies(self) -> tuple[str, ...]:
        secure = "; Secure" if self.cookie_secure else ""
        return (self._cookie("", max_age=0), f"astra_csrf=; SameSite=Lax; Path=/; Max-Age=0{secure}")

    def _csrf_valid(self, request: Request) -> bool:
        cookie = SimpleCookie()
        cookie.load(request.cookie or "")
        session_cookie = cookie["astra_session"].value if "astra_session" in cookie else ""
        if not session_cookie:
            return True
        csrf_cookie = cookie["astra_csrf"].value if "astra_csrf" in cookie else ""
        return bool(csrf_cookie and request.csrf_token and hmac.compare_digest(csrf_cookie, request.csrf_token))

    @staticmethod
    def _error(status: int, code: str, message: str) -> Response:
        return Response(status, {"code": code, "message": message, "error": code})
