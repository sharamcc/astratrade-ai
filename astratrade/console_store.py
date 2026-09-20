"""SQLite persistence for the multi-user simulation console."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Any, Optional

from .passwords import hash_password


def now() -> datetime:
    return datetime.now(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def parse(value: Optional[str]) -> Optional[datetime]:
    return datetime.fromisoformat(value) if value else None


def digest(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


class ConsoleStore:
    """Application data adapter kept separate from the existing OAuth adapter."""

    def __init__(self, connection: sqlite3.Connection):
        self.connection = connection
        self.connection.row_factory = sqlite3.Row
        self.connection.execute("PRAGMA foreign_keys = ON")
        self.create_schema()

    def create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS console_users (
                user_id TEXT PRIMARY KEY,
                email TEXT NOT NULL UNIQUE,
                password_hash TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'user',
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS invite_codes (
                code_hash TEXT PRIMARY KEY,
                created_by TEXT NOT NULL,
                max_uses INTEGER NOT NULL,
                uses INTEGER NOT NULL DEFAULT 0,
                active INTEGER NOT NULL DEFAULT 1,
                expires_at TEXT,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sim_accounts (
                user_id TEXT PRIMARY KEY,
                cash_usdt TEXT NOT NULL,
                btc TEXT NOT NULL,
                initial_cash_usdt TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS agent_configs (
                user_id TEXT PRIMARY KEY,
                budget_usdt TEXT NOT NULL,
                frequency TEXT NOT NULL,
                status TEXT NOT NULL,
                next_run_at TEXT,
                last_run_at TEXT,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS sim_orders (
                order_id TEXT PRIMARY KEY,
                execution_key TEXT NOT NULL UNIQUE,
                user_id TEXT NOT NULL,
                instrument TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity TEXT NOT NULL,
                price TEXT NOT NULL,
                notional_usdt TEXT NOT NULL,
                fee_usdt TEXT NOT NULL,
                status TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS console_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                event_type TEXT NOT NULL,
                payload TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            """
        )
        self.connection.commit()

    def create_user(self, email: str, password: str, invite_code: str) -> dict[str, Any]:
        email = email.strip().lower()
        if not email or "@" not in email:
            raise ValueError("valid email is required")
        if self.connection.execute("SELECT 1 FROM console_users WHERE email = ?", (email,)).fetchone():
            raise ValueError("email is already registered")
        invite = self.connection.execute(
            "SELECT * FROM invite_codes WHERE code_hash = ? AND active = 1",
            (digest(invite_code.strip()),),
        ).fetchone()
        if invite is None or invite["uses"] >= invite["max_uses"]:
            raise ValueError("invite code is invalid or exhausted")
        expires_at = parse(invite["expires_at"])
        if expires_at and expires_at <= now():
            raise ValueError("invite code has expired")
        user_id = "user_" + secrets.token_urlsafe(12)
        created = iso(now())
        password_hash = hash_password(password)
        with self.connection:
            self.connection.execute(
                "INSERT INTO console_users(user_id,email,password_hash,role,created_at) VALUES (?,?,?,?,?)",
                (user_id, email, password_hash, "user", created),
            )
            self.connection.execute(
                "UPDATE invite_codes SET uses = uses + 1 WHERE code_hash = ?",
                (invite["code_hash"],),
            )
            self.connection.execute(
                "INSERT INTO sim_accounts VALUES (?,?,?,?,?)",
                (user_id, "10000.00", "0", "10000.00", created),
            )
            self.connection.execute(
                "INSERT INTO agent_configs VALUES (?,?,?,?,?,?,?)",
                (user_id, "100.00", "daily", "stopped", None, None, created),
            )
            self.audit(user_id, "user_registered", {"invite_created_by": invite["created_by"]})
        return self.get_user(user_id)  # type: ignore[return-value]

    def get_user(self, user_id: str) -> Optional[dict[str, Any]]:
        row = self.connection.execute("SELECT * FROM console_users WHERE user_id = ?", (user_id,)).fetchone()
        return dict(row) if row else None

    def get_user_by_email(self, email: str) -> Optional[dict[str, Any]]:
        row = self.connection.execute("SELECT * FROM console_users WHERE email = ?", (email.strip().lower(),)).fetchone()
        return dict(row) if row else None

    def create_admin(self, email: str, password: str) -> dict[str, Any]:
        email = email.strip().lower()
        existing = self.get_user_by_email(email)
        if existing:
            with self.connection:
                self.connection.execute("UPDATE console_users SET role='admin' WHERE user_id=?", (existing["user_id"],))
            return self.get_user(existing["user_id"])  # type: ignore[return-value]
        user_id = "admin_" + secrets.token_urlsafe(10)
        created = iso(now())
        with self.connection:
            self.connection.execute("INSERT INTO console_users VALUES (?,?,?,?,?)", (user_id, email, hash_password(password), "admin", created))
            self.connection.execute("INSERT INTO sim_accounts VALUES (?,?,?,?,?)", (user_id, "10000.00", "0", "10000.00", created))
            self.connection.execute("INSERT INTO agent_configs VALUES (?,?,?,?,?,?,?)", (user_id, "100.00", "daily", "stopped", None, None, created))
            self.audit(user_id, "admin_created", {})
        return self.get_user(user_id)  # type: ignore[return-value]

    def ensure_legacy_user(self, user_id: str, email: str) -> None:
        if self.get_user(user_id) is not None:
            return
        with self.connection:
            self.connection.execute(
                "INSERT OR IGNORE INTO console_users VALUES (?,?,?,?,?)",
                (user_id, email, hash_password(secrets.token_urlsafe(24) + "Aa1!"), "user", iso(now())),
            )
            self.connection.execute("INSERT OR IGNORE INTO sim_accounts VALUES (?,?,?,?,?)", (user_id, "10000.00", "0", "10000.00", iso(now())))
            self.connection.execute("INSERT OR IGNORE INTO agent_configs VALUES (?,?,?,?,?,?,?)", (user_id, "100.00", "daily", "stopped", None, None, iso(now())))

    def authenticate(self, email: str, password: str) -> Optional[dict[str, Any]]:
        user = self.get_user_by_email(email)
        if not user:
            return None
        from .passwords import verify_password
        return user if verify_password(user["password_hash"], password) else None

    def change_password(self, user_id: str, current_password: str, new_password: str) -> None:
        user = self.get_user(user_id)
        if not user:
            raise ValueError("user not found")
        from .passwords import verify_password
        if not verify_password(user["password_hash"], current_password):
            raise ValueError("current password is incorrect")
        encoded = hash_password(new_password)
        with self.connection:
            self.connection.execute("UPDATE console_users SET password_hash=? WHERE user_id=?", (encoded, user_id))
            self.audit(user_id, "password_changed", {})

    def create_invite(self, created_by: str, max_uses: int = 1, ttl_hours: int = 168) -> tuple[str, dict[str, Any]]:
        if max_uses < 1 or max_uses > 100:
            raise ValueError("max_uses must be between 1 and 100")
        code = "ASTRA-" + secrets.token_urlsafe(10).upper()
        created = now()
        with self.connection:
            self.connection.execute(
                "INSERT INTO invite_codes VALUES (?,?,?,?,?,?,?)",
                (digest(code), created_by, max_uses, 0, 1, iso(created + timedelta(hours=ttl_hours)), iso(created)),
            )
            self.audit(created_by, "invite_created", {"max_uses": max_uses})
        return code, self.invite_list()

    def invite_list(self) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT code_hash, created_by, max_uses, uses, active, expires_at, created_at FROM invite_codes ORDER BY created_at DESC").fetchall()
        return [{"id": r["code_hash"], "created_by": r["created_by"], "max_uses": r["max_uses"], "uses": r["uses"], "active": bool(r["active"]), "expires_at": r["expires_at"], "created_at": r["created_at"]} for r in rows]

    def deactivate_invite(self, code_hash: str) -> None:
        with self.connection:
            self.connection.execute("UPDATE invite_codes SET active = 0 WHERE code_hash = ?", (code_hash,))

    def agent(self, user_id: str) -> dict[str, Any]:
        row = self.connection.execute("SELECT * FROM agent_configs WHERE user_id = ?", (user_id,)).fetchone()
        if not row:
            raise ValueError("agent is not initialized")
        return dict(row)

    def save_agent(self, user_id: str, budget: Decimal, frequency: str) -> dict[str, Any]:
        if frequency not in {"daily", "weekly"}:
            raise ValueError("frequency must be daily or weekly")
        if budget <= 0 or budget > Decimal("500"):
            raise ValueError("budget must be between 0 and 500 USDT")
        with self.connection:
            self.connection.execute("UPDATE agent_configs SET budget_usdt=?, frequency=?, updated_at=? WHERE user_id=?", (str(budget), frequency, iso(now()), user_id))
        return self.agent(user_id)

    def set_agent_status(self, user_id: str, status: str, next_run_at: Optional[datetime]) -> dict[str, Any]:
        if status not in {"running", "stopped"}:
            raise ValueError("invalid agent status")
        with self.connection:
            self.connection.execute("UPDATE agent_configs SET status=?, next_run_at=?, updated_at=? WHERE user_id=?", (status, iso(next_run_at) if next_run_at else None, iso(now()), user_id))
        return self.agent(user_id)

    def due_agents(self, at: datetime) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT * FROM agent_configs WHERE status='running' AND next_run_at IS NOT NULL AND next_run_at <= ?", (iso(at),)).fetchall()
        return [dict(row) for row in rows]

    def run_simulation(self, user_id: str, price: Decimal, execution_key: str) -> dict[str, Any]:
        existing = self.connection.execute("SELECT * FROM sim_orders WHERE execution_key = ?", (execution_key,)).fetchone()
        if existing:
            return dict(existing)
        agent = self.agent(user_id)
        account = self.connection.execute("SELECT * FROM sim_accounts WHERE user_id = ?", (user_id,)).fetchone()
        budget = Decimal(agent["budget_usdt"])
        fee = (budget * Decimal("0.001")).quantize(Decimal("0.00000001"))
        available = Decimal(account["cash_usdt"])
        if price <= 0 or budget + fee > available:
            self.audit(user_id, "risk_blocked", {"reason": "insufficient_cash_or_invalid_price", "budget": str(budget), "price": str(price)})
            return {"status": "risk_blocked", "reason": "insufficient_cash_or_invalid_price"}
        quantity = (budget / price).quantize(Decimal("0.00000001"))
        order_id = "sim_" + secrets.token_urlsafe(10)
        created = iso(now())
        with self.connection:
            self.connection.execute("UPDATE sim_accounts SET cash_usdt=?, btc=?, updated_at=? WHERE user_id=?", (str(available - budget - fee), str(Decimal(account["btc"]) + quantity), created, user_id))
            self.connection.execute("INSERT INTO sim_orders VALUES (?,?,?,?,?,?,?,?,?,?,?)", (order_id, execution_key, user_id, "BTC-USDT", "buy", str(quantity), str(price), str(budget), str(fee), "filled", created))
            self.audit(user_id, "simulation_order_filled", {"order_id": order_id, "price": str(price), "quantity": str(quantity), "fee": str(fee)})
        return dict(self.connection.execute("SELECT * FROM sim_orders WHERE order_id=?", (order_id,)).fetchone())

    def dashboard(self, user_id: str, price: Decimal) -> dict[str, Any]:
        account = dict(self.connection.execute("SELECT * FROM sim_accounts WHERE user_id=?", (user_id,)).fetchone())
        orders = [dict(row) for row in self.connection.execute("SELECT * FROM sim_orders WHERE user_id=? ORDER BY created_at DESC LIMIT 5", (user_id,)).fetchall()]
        btc = Decimal(account["btc"])
        cash = Decimal(account["cash_usdt"])
        equity = cash + btc * price
        return {"mode": "simulation", "instrument": "BTC-USDT", "price": str(price), "cash_usdt": str(cash), "btc": str(btc), "equity_usdt": str(equity), "pnl_usdt": str(equity - Decimal(account["initial_cash_usdt"])), "agent": self.agent(user_id), "recent_orders": orders}

    def orders(self, user_id: str, limit: int = 50) -> list[dict[str, Any]]:
        return [dict(row) for row in self.connection.execute("SELECT * FROM sim_orders WHERE user_id=? ORDER BY created_at DESC LIMIT ?", (user_id, min(max(limit, 1), 100))).fetchall()]

    def audit_events(self, user_id: str, limit: int = 100) -> list[dict[str, Any]]:
        rows = self.connection.execute("SELECT event_type, payload, created_at FROM console_audit WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, min(max(limit, 1), 200))).fetchall()
        return [{"event_type": r["event_type"], "payload": json.loads(r["payload"]), "created_at": r["created_at"]} for r in rows]

    def audit(self, user_id: Optional[str], event_type: str, payload: dict[str, Any]) -> None:
        self.connection.execute("INSERT INTO console_audit(user_id,event_type,payload,created_at) VALUES (?,?,?,?)", (user_id, event_type, json.dumps(payload, ensure_ascii=False), iso(now())))
