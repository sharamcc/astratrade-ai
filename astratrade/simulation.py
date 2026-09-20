"""Safe, deterministic simulation runner for the test console."""

from __future__ import annotations

import os
from datetime import timedelta
from decimal import Decimal
from urllib.request import Request as URLRequest, urlopen
import json

from .console_store import ConsoleStore, now, parse, iso


def current_btc_price() -> Decimal:
    configured = os.environ.get("ASTRA_SIM_PRICE")
    if configured:
        return Decimal(configured)
    request = URLRequest("https://www.okx.com/api/v5/market/ticker?instId=BTC-USDT", headers={"User-Agent": "AstraTrade-AI/0.1"})
    with urlopen(request, timeout=5) as response:
        payload = json.loads(response.read().decode("utf-8"))
    return Decimal(payload["data"][0]["last"])


def run_once(store: ConsoleStore, user_id: str, price: Decimal | None = None) -> dict:
    agent = store.agent(user_id)
    price = price or current_btc_price()
    execution_key = f"{user_id}:{agent['next_run_at']}"
    result = store.run_simulation(user_id, price, execution_key)
    interval = timedelta(days=7 if agent["frequency"] == "weekly" else 1)
    store.connection.execute("UPDATE agent_configs SET last_run_at=?, next_run_at=?, updated_at=? WHERE user_id=?", (iso(now()), iso(now() + interval), iso(now()), user_id))
    store.connection.commit()
    return result


def run_due(store: ConsoleStore, price: Decimal | None = None) -> int:
    count = 0
    for agent in store.due_agents(now()):
        run_once(store, agent["user_id"], price)
        count += 1
    return count
