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
                return Response(200, {"user_id": user_id, "email": user["email"], "role": user["role"]}, cookies=self._refresh_csrf_cookie(request))
            if method == "POST" and path == "/v1/auth/password":
                self.product.change_password(user_id, str(body.get("current_password", "")), str(body.get("new_password", "")))
                self.sessions.repository.revoke_user_sessions(user_id)
                self.product.audit(user_id, "sessions_revoked_after_password_change", {})
                self.product.connection.commit()
                return Response(204, {}, cookies=self._clear_cookies())
            if method == "GET" and path == "/v1/dashboard":
                return Response(200, self.product.dashboard(user_id, self._price()))
            if method == "GET" and path == "/v1/analytics":
                return Response(200, self.product.analytics(user_id, self._price()))
            if path == "/v1/agent" and method == "GET":
                return Response(200, self.product.agent(user_id))
            if path == "/v1/agent/templates" and method == "GET":
                return Response(200, {"items": self.product.strategy_templates()})
            if path == "/v1/agent" and method == "PUT":
                result = self.product.save_agent(user_id, Decimal(str(body.get("budget_usdt"))), str(body.get("frequency", "daily")))
                self.product.audit(user_id, "agent_config_saved", {"budget_usdt": result["budget_usdt"], "frequency": result["frequency"], "template_id": body.get("template_id")})
                self.product.connection.commit()
                return Response(200, result)
            if path == "/v1/agent/start" and method == "POST":
                agent = self.product.set_agent_status(user_id, "running", __import__("datetime").datetime.now(__import__("datetime").timezone.utc))
                self.product.audit(user_id, "agent_started", {"budget_usdt": agent["budget_usdt"], "frequency": agent["frequency"]})
                self.product.notify(user_id, "agent_started", "Agent 已启动", "模拟 Agent 已启动，并将立即尝试执行一次。", "agent", user_id, f"agent_started:{agent['updated_at']}")
                result = run_once(self.product, user_id, self._price())
                self.product.connection.commit()
                return Response(200, {"agent": result and self.product.agent(user_id), "run": result})
            if path == "/v1/agent/stop" and method == "POST":
                result = self.product.set_agent_status(user_id, "stopped", None)
                self.product.audit(user_id, "agent_stopped", {})
                self.product.notify(user_id, "agent_stopped", "Agent 已停止", "后续调度将暂停，现有模拟账户不会被修改。", "agent", user_id, f"agent_stopped:{result['updated_at']}")
                self.product.connection.commit()
                return Response(200, result)
            if method == "GET" and path.startswith("/v1/orders/"):
                order_id = path.rsplit("/", 1)[-1]
                detail = self.product.order_detail(user_id, order_id)
                if not detail:
                    return self._error(404, "order_not_found", "订单不存在或无权查看")
                self.product.audit(user_id, "order_detail_viewed", {"order_id": order_id})
                self.product.connection.commit()
                return Response(200, detail)
            if method == "GET" and path == "/v1/orders":
                return Response(200, {"items": self.product.orders(user_id)})
            if method == "GET" and path == "/v1/audit":
                return Response(200, {"items": self.product.audit_events(user_id)})
            if method == "GET" and path == "/v1/notifications":
                unread_only = str((request.query or {}).get("unread", "0")).lower() in {"1", "true", "yes"}
                return Response(200, {"items": self.product.notifications(user_id, unread_only), "unread_count": self.product.unread_notification_count(user_id)})
            if method == "POST" and path == "/v1/notifications/read":
                notification_id = str(body.get("notification_id", ""))
                if not notification_id:
                    return self._error(400, "notification_id_required", "通知 ID 不能为空")
                self.product.mark_notification_read(user_id, notification_id)
                self.product.audit(user_id, "notification_viewed", {"notification_id": notification_id})
                self.product.connection.commit()
                return Response(204, {})
            if method == "POST" and path == "/v1/notifications/read-all":
                count = self.product.mark_all_notifications_read(user_id)
                self.product.audit(user_id, "notifications_marked_read", {"count": count})
                self.product.connection.commit()
                return Response(200, {"count": count})
            if method == "GET" and path.startswith("/v1/export/"):
                export_type = path.rsplit("/", 1)[-1]
                filename, content = self.product.export_csv(user_id, export_type)
                self.product.audit(user_id, "data_export_completed", {"export_type": export_type, "row_count": max(0, content.count("\n") - 1)})
                self.product.connection.commit()
                return Response(200, {"filename": filename, "content_type": "text/csv; charset=utf-8", "content": content})
            if path == "/v1/admin/invites":
                return self._invites(user_id, method, body)
            if method == "GET" and path == "/v1/admin/overview":
                user = self.product.get_user(user_id)
                if not user or user["role"] != "admin":
                    return self._error(403, "admin_required", "需要管理员权限")
                return Response(200, self.product.admin_overview())
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
        return (self._cookie(session_token), self._new_csrf_cookie())

    def _new_csrf_cookie(self) -> str:
        secure = "; Secure" if self.cookie_secure else ""
        return f"astra_csrf={secrets.token_urlsafe(24)}; SameSite=Lax; Path=/; Max-Age=28800{secure}"

    def _refresh_csrf_cookie(self, request: Request) -> tuple[str, ...]:
        cookie = SimpleCookie()
        cookie.load(request.cookie or "")
        if "astra_session" not in cookie or not cookie["astra_session"].value:
            return ()
        if "astra_csrf" in cookie and cookie["astra_csrf"].value:
            return ()
        return (self._new_csrf_cookie(),)

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
