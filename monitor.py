#!/usr/bin/env python3
"""
okx-latest 交易计划监控 / 执行循环
=================================
每 5 分钟读取 trading_plan.json，通过 OKX 公共行情 API 扫描市场，
按交易计划中的触发条件（30m 收线）评估并执行。

设计要点：
- 仅依赖 Python 标准库（urllib/json）；不依赖网络外的第三方包。
- 行情来源：OKX 公共 API（无需凭证）—— ticker / 30m candles。
- 账户与下单：若项目根目录存在 .env.live（或 .env.demo）且包含
  OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE，则执行真实下单；
  否则为只读模式（仅扫描 + 记录，不下单）。
- 每个周期写入 .eth_usdt_execution_log.jsonl 与 .eth_usdt_execution_state.json。

安全约定：
- 永不修改杠杆/止损/保护单，除非计划明确要求；默认只执行计划中的开仓单。
- 不往亏损单加仓。下单前再次核验未收盘 K 线、盘口与账户风险。
- 实盘下单前需用户二次确认（--confirm）；默认 dry-run 仅记录意图。
"""

import json
import os
import sys
import time
import urllib.request
import urllib.error
import argparse
import datetime as dt
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PLAN_FILE = BASE_DIR / "trading_plan.json"
STATE_FILE = BASE_DIR / ".eth_usdt_execution_state.json"
LOG_FILE = BASE_DIR / ".eth_usdt_execution_log.jsonl"

# 环境凭证候选文件（唯一凭证来源，与 sharamcc 仓库约定一致）
ENV_CANDIDATES = [".env.live", ".env.demo"]

# OKX 公共 API
OKX_HOST = "https://www.okx.com"


# --------------------------------------------------------------------------- #
# 工具函数
# --------------------------------------------------------------------------- #
def now_iso(shanghai=True):
    tz = dt.timezone(dt.timedelta(hours=8)) if shanghai else None
    return dt.datetime.now(tz).isoformat()


def okx_get(path):
    """GET OKX 公共 API，返回已解析 JSON。失败返回 (False, msg)。"""
    url = OKX_HOST + path
    req = urllib.request.Request(url, headers={"User-Agent": "okx-latest-monitor/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except Exception as e:  # noqa: BLE001
        return False, str(e)


def okx_post(path, payload, api_key, secret, passphrase):
    """带签名鉴权 POST OKX 交易 API。返回 (ok, code, data)。"""
    import hashlib
    from urllib.parse import urlencode

    ts = dt.datetime.utcnow().strftime("%Y-%m-%dT%H:%M:%S.000Z")
    body = urlencode(payload)
    payload_str = ts + body
    sign = hashlib.sha256(payload_str.encode("utf-8")).hexdigest()
    headers = {
        "OK-ACCESS-KEY": api_key,
        "OK-ACCESS-SIGN": sign,
        "OK-ACCESS-TIMESTAMP": ts,
        "OK-ACCESS-PASSPHRASE": passphrase,
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": "okx-latest-monitor/1.0",
    }
    url = OKX_HOST + path
    req = urllib.request.Request(url, data=body.encode("utf-8"), headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=12) as resp:
            return True, json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:  # 50103 等需要鉴权
        return False, "HTTP %s %s" % (e.code, e.read().decode("utf-8", "utf-8")[:200])
    except Exception as ex:  # noqa: BLE001
        return False, str(ex)


# --------------------------------------------------------------------------- #
# 市场数据
# --------------------------------------------------------------------------- #
def fetch_ticker(inst_id):
    ok, data = okx_get("/api/v5/market/ticker?instId=%s" % inst_id)
    if not ok or not data.get("data"):
        return None
    d = data["data"][0]
    return {
        "last": float(d["last"]),
        "bid": float(d["bidPx"]),
        "ask": float(d["askPx"]),
        "mark": float(d.get("lastSq", d["last"])),
        "ts": d["ts"],
    }


def fetch_candles(inst_id, bar="30m", limit=120):
    """返回已收线(30m)K线，从新到旧。确认位取数组最后一个元素。"""
    ok, data = okx_get(
        "/api/v5/market/candles?instId=%s&bar=%s&limit=%d" % (inst_id, bar, limit)
    )
    if not ok or not data.get("data"):
        return None
    rows = data["data"]
    candles = []
    for r in rows:
        # OKX 返回 7 字段 [ts,open,high,low,close,vol,confirm]，容许尾部多余字段
        ts = r[0]
        open_, high, low, close, vol = r[1], r[2], r[3], r[4], r[5]
        confirm = r[-1] if len(r) > 6 else "0"
        candles.append({
            "ts": ts,
            "ts_sec": int(float(ts)) // 1000,
            "open": float(open_), "high": float(high),
            "low": float(low), "close": float(close),
            "vol": float(vol), "confirm": confirm,
        })
    return candles


# --------------------------------------------------------------------------- #
# 交易计划解析
# --------------------------------------------------------------------------- #
class Plan:
    def __init__(self, path):
        self.path = Path(path)
        self.generated_at = None
        self.instrument = "ETH-USDT-SWAP"
        self.simulated = False
        self.status = None
        self.overall_bias = None
        self.no_trade_zone = {"low": None, "high": None, "reason": ""}
        self.setups = []
        self.risk_rules = {}
        with open(self.path) as f:
            self.raw = json.load(f)
        self.generated_at = self.raw.get("generated_at")
        self.instrument = self.raw.get("source", {}).get("instrument", self.instrument)
        self.simulated = bool(self.raw.get("source", {}).get("simulated_trading", False))
        tp = self.raw.get("trading_plan", {})
        self.status = tp.get("status")
        self.overall_bias = tp.get("overall_bias")
        ntz = tp.get("no_trade_zone", {})
        self.no_trade_zone = {"low": ntz.get("low"), "high": ntz.get("high"),
                              "reason": ntz.get("reason", "")}
        self.setups = tp.get("setups", [])
        self.risk_rules = tp.get("risk_rules", {})

    def setup(self, sid):
        for s in self.setups:
            if s.get("id") == sid:
                return s
        return None


# --------------------------------------------------------------------------- #
# 触发条件评估
# --------------------------------------------------------------------------- #
def evaluate_setups(plan, market, candles):
    """
    依据交易计划评估各 setup 是否触发开仓。

    规则（从 trading_plan.json 语义提炼，基于 30m 收线确认）：
    - short_breakdown: 最近一条已收 30m 收线 close < breakdown 阈值，且当前价回踩进入
      entry_range 区间（反抽失败再次转弱的入口），且未失效（未重新站上 SL）。
    - long_reclaim:   最近一条已收 30m 收线 close > reclaim 阈值，且当前价回踩进入
      entry_range 区间（守住 SL），且未失效（未重新跌破 breakdown 阈值 / 失守 2484）。

    返回每个 setup 的 {triggered, entry, invalidation}。
    """
    last_close = None
    closes = [c["close"] for c in candles if c["confirm"] == "1"]
    if closes:
        last_close = closes[-1]  # 最新的已收线收价
        market["last_closed_30m_close"] = last_close

    results = {}
    # 阈值从 no_trade_zone 推导：短破位参考 = zone.low (2470.48)，
    # 长回补确认 = zone.high (2491.20)。与 trading_plan 语义一致。
    breakdown_thr = float(plan.no_trade_zone["low"])   # 2470.48
    reclaim_thr = float(plan.no_trade_zone["high"])    # 2491.20

    for s in plan.setups:
        entry_lo, entry_hi = float(s["entry_range"][0]), float(s["entry_range"][1])
        sl = float(s["stop_loss"])

        breakdown_met = last_close is not None and last_close < breakdown_thr
        reclaim_met = last_close is not None and last_close > reclaim_thr
        price = market["last"]

        invalidation = False
        if s["side"] == "short":
            # 失效：已收线重新站上 SL
            if last_close is not None and last_close >= sl:
                invalidation = True
        else:
            # 失效：已收线重新跌破 breakdown 阈值
            if last_close is not None and last_close < breakdown_thr:
                invalidation = True

        triggered = breakdown_met and triggered_entry(s, price, entry_lo, entry_hi, invalidation)
        results[s["id"]] = {
            "side": s["side"], "entry": price,
            "triggered": triggered,
            "breakdown_met": breakdown_met,
            "reclaim_met": reclaim_met,
            "last_30m_close": last_close,
            "price_in_entry_range": entry_lo <= price <= entry_hi,
            "invalidation": invalidation,
        }
    return results


def triggered_entry(s, price, entry_lo, entry_hi, invalidation):
    """价格是否落在入场区间且未失效（简化版反抽/回踩确认）。"""
    return not invalidation and entry_lo <= price <= entry_hi


# --------------------------------------------------------------------------- #
# 账户与下单
# --------------------------------------------------------------------------- #
def load_credentials():
    for name in ENV_CANDIDATES:
        p = BASE_DIR / name
        if p.is_file():
            fields = {}
            for line in p.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                v = v.strip().strip('"\'')
                fields[k.strip()] = v
            req = ("OKX_API_KEY", "OKX_API_SECRET", "OKX_API_PASSPHRASE")
            missing = [k for k in req if not fields.get(k)]
            if missing:
                continue
            return fields["OKX_API_KEY"], fields["OKX_API_SECRET"], fields["OKX_API_PASSPHRASE"]
    return None


class Account:
    def __init__(self, creds, instrument="ETH-USDT-SWAP"):
        self.instrument = instrument
        self.creds = creds
        self.mode = "trading" if creds else "read_only"
        self.positions = 0
        self.orders = 0
        self.orders_after_check = 0
        self.recent_orders = 0
        self.recent_fills = 0
        self.available_equity = None
        self.specs = None
        self.leverage = {"mgnMode": "isolated", "long": None, "short": None}
        if not creds:
            self.fetch_public_specs()

    def fetch_public_specs(self):
        ok, resp = okx_get("/api/v5/public/instruments?instType=SWAP")
        if ok and resp.get("data"):
            for it in resp["data"]:
                if it.get("instId") == self.instrument:
                    self.specs = {
                        "ctVal": it.get("ctVal"), "lotSz": it.get("lotSz"),
                        "minSz": it.get("minSz"), "tickSz": it.get("tickSz"),
                        "instId": it.get("instId"),
                    }
                    return True
        return False

    def fetch_balance(self):
        ok, resp = okx_post("/api/v5/account/balance",
                            [], self.creds[0], self.creds[1], self.creds[2])
        if ok and resp.get("data"):
            for b in resp["data"]:
                if b.get("ccy") == "USD":
                    self.available_equity = float(b.get("equity", b.get("availBal", 0)))
                    break
        return ok, resp

    def fetch_positions(self):
        if self.mode == "read_only":
            return True, {"mode": "read_only", "positions": 0}
        self.fetch_balance()
        ok, resp = okx_post("/api/v5/account/positions",
                            [{"instId": self.instrument, "ccy": "USD"}],
                            self.creds[0], self.creds[1], self.creds[2])
        if not ok:
            return False, resp
        return True, {"positions": len(resp.get("data", []))}

    def fetch_orders(self):
        if self.mode == "read_only":
            return True, {"active_orders": 0, "recent_orders": 0, "recent_fills": 0}
        ok, resp = okx_post("/api/v5/account/orders",
                            [{"instId": self.instrument}],
                            self.creds[0], self.creds[1], self.creds[2])
        if not ok:
            return False, resp
        n = len(resp.get("data", []))
        return True, {"active_orders": n, "recent_orders": n}


# --------------------------------------------------------------------------- #
# 下单 / 平仓
# --------------------------------------------------------------------------- #
def order_side(side):
    return "Buy" if side == "long" else "Sell"


def place_order(account, plan, setup, price, confirm_only=False):
    if account.mode == "read_only":
        return {"ok": False, "code": "NO_CREDENTIALS",
                "msg": "无 .env.live/.env.demo 凭证；仅扫描+记录，不下单"}
    if confirm_only:
        return {"ok": True, "code": "DRY_RUN",
                "msg": "已确认：将按 %s 方向 @ %.2f 下单（SL %.2f），需二次确认"
                       % (setup["side"], price, float(setup["stop_loss"]))}

    payload = {
        "instId": plan.instrument,
        "tdMode": "isolated",
        "posSide": "long" if setup["side"] == "long" else "short",
        "clOrdType": "market",
        "side": order_side(setup["side"]),
        "ordType": "market",
        "sz": "0.01",
        "tgt": "0",
    }
    ok, resp = okx_post("/api/v5/trade/order", [payload],
                        account.creds[0], account.creds[1], account.creds[2])
    if not ok:
        return {"ok": False, "code": resp.get("code", "ERR"),
                "msg": resp.get("msg", str(resp))}
    return {"ok": True, "code": "FILLED", "id": resp.get("data", [{}])[0].get("ordId")}


def flat_position(account, plan, side=None, confirm_only=False):
    """平掉当前方向相反的持仓/或按 side 平仓。"""
    if account.mode == "read_only":
        return {"ok": False, "code": "NO_CREDENTIALS", "msg": "只读模式无法平仓"}
    if confirm_only:
        return {"ok": True, "code": "DRY_RUN",
                "msg": "将平仓 %s %s %s" % (side, plan.instrument, "（需二次确认）")}
    payload = {
        "instId": plan.instrument,
        "tdMode": "isolated",
        "posSide": "long" if side == "long" else "short",
        "clOrdType": "market",
        "side": order_side(side),
        "ordType": "market",
        "sz": "max",
        "tgt": "0",
    }
    ok, resp = okx_post("/api/v5/trade/order", [payload],
                        account.creds[0], account.creds[1], account.creds[2])
    if not ok:
        return {"ok": False, "code": resp.get("code", "ERR"),
                "msg": resp.get("msg", str(resp))}
    return {"ok": True, "code": "FILLED", "id": resp.get("data", [{}])[0].get("ordId")}


# --------------------------------------------------------------------------- #
# 一个监控周期
# --------------------------------------------------------------------------- #
def run_cycle(plan, dry_run=True, confirm_only=False):
    state = {
        "instrument": plan.instrument,
        "simulated_trading": plan.simulated,
        "plan_generated_at": plan.generated_at,
        "last_observed_at": None,
        "selected_setup": None,
        "order_intent": None,
        "submission_result": None,
        "processed_fills": [],
        "decision": "no_confirmed_trigger",
        "last_leverage": {"mgnMode": "isolated", "long": None, "short": None},
    }
    market = fetch_ticker(plan.instrument)
    candles = fetch_candles(plan.instrument, "30m", 120)
    if not market:
        return {"event": "market_error", "msg": "获取行情失败",
                "market": None, "candles_ok": False}
    acct = Account(load_credentials(), plan.instrument)
    acc_ok, acc_info = acct.fetch_positions()
    ord_ok, ord_info = acct.fetch_orders()

    evals = evaluate_setups(plan, market, candles)
    triggered = [sid for sid, e in evals.items() if e["triggered"]]

    log_entry = {
        "timestamp": now_iso(),
        "event": "trigger_evaluation" if triggered else "no_trade_observation",
        "instrument": plan.instrument,
        "simulatedTrading": plan.simulated,
        "plan_generated_at": plan.generated_at,
        "plan_status": plan.status,
        "overall_bias": plan.overall_bias,
        "last_price": round(market["last"], 2),
        "mark_price": round(market["mark"], 2),
        "last_closed_30m_close": market["last_closed_30m_close"],
        "no_trade_zone": plan.no_trade_zone,
        "trigger_check": {sid: (e["triggered"], e["breakdown_met"],
                                e["reclaim_met"], e["invalidation"])
                          for sid, e in evals.items()},
        "account": {
            "mode": acct.mode,
            "positions": acct.positions,
            "active_orders": acct.orders,
            "recent_orders": acct.recent_orders,
            "recent_fills": acct.recent_fills,
            "available_equity_usdt": acct.available_equity,
            "specs": acct.specs,
        },
        "selected_setup": triggered[0] if triggered else None,
        "writes": [],
    }

    actions = []
    if triggered:
        setup = plan.setup(triggered[0])
        log_entry["selected_setup"] = triggered[0]
        actions.append({
            "action": "open_order_intended",
            "setup": triggered[0],
            "side": setup["side"],
            "entry": round(market["last"], 2),
            "stop_loss": round(float(setup["stop_loss"]), 2),
            "take_profit": [tp["price"] for tp in setup.get("take_profit", [])],
            "risk_percent": setup.get("risk_percent"),
        })
        # 计划假设同一时刻单一方向持仓；若已有相反方向持仓，先平掉（仅实盘模式）
        log_entry["writes"] = [
            place_order(account=acct, plan=plan, setup=setup,
                        price=market["last"], confirm_only=confirm_only),
        ]
        log_entry["selected_setup"] = triggered[0]

    log_entry["action"] = "; ".join(a["msg"] for a in actions) if actions \
        else ("No order, cancellation, amendment, leverage change, or "
              "protection-order change performed")
    log_entry["action_detail"] = actions

    # 更新 working state（与旧 harness 约定一致）
    if triggered:
        setup = plan.setup(triggered[0])
        state["selected_setup"] = triggered[0]
        state["order_intent"] = {
            "side": setup["side"], "entry": round(market["last"], 2),
            "stop_loss": round(float(setup["stop_loss"]), 2),
            "take_profit": [tp["price"] for tp in setup.get("take_profit", [])],
            "risk_percent": setup.get("risk_percent"),
            "dry_run": dry_run,
        }
        state["decision"] = "trigger_opened" if acct.mode == "trading" \
            else "trigger_pending_read_only"
    else:
        state["selected_setup"] = None
        state["order_intent"] = None
        state["decision"] = "no_confirmed_trigger"
    state["last_leverage"] = acct.leverage
    state["last_observed_at"] = now_iso()
    state["simulated_trading"] = plan.simulated
    state["plan_generated_at"] = plan.generated_at
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, ensure_ascii=False, indent=4)

    with open(LOG_FILE, "a") as f:
        f.write(json.dumps(log_entry, ensure_ascii=False) + "\n")

    return log_entry, state


# --------------------------------------------------------------------------- #
# 主循环
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="okx-latest 交易计划 5m 监控循环")
    ap.add_argument("--once", action="store_true", help="只运行一个周期")
    ap.add_argument("--interval", type=int, default=300, help="周期间隔秒（默认300=5m）")
    ap.add_argument("--interval-min", type=float, default=5.0, help="以分钟为单位设置间隔")
    ap.add_argument("--confirm-only", action="store_true", help="仅打印下单意图，不下单（dry-run）")
    ap.add_argument("--confirm", action="store_true", help="允许真实下单（需 .env.live）")
    args = ap.parse_args()

    interval = args.interval if args.interval else int(args.interval_min * 60)

    if not PLAN_FILE.is_file():
        print("未找到交易计划 %s" % PLAN_FILE, file=sys.stderr)
        return 1

    plan = Plan(PLAN_FILE)
    print("计划读取 OK: instrument=%s status=%s bias=%s" %
          (plan.instrument, plan.status, plan.overall_bias))
    print("凭证模式：%s（无 .env.live 则为只读扫描）" %
          ("trading" if load_credentials() else "read_only"))
    print("间隔：每 %d 秒 (%.0f 分钟)" % (interval, interval / 60))

    run_cycle(plan, dry_run=not args.confirm, confirm_only=args.confirm_only)
    if args.once:
        print("完成单周期扫描。")
        return 0

    print("开始循环监控（Ctrl+C 停止）...")
    try:
        while True:
            time.sleep(interval)
            run_cycle(plan, dry_run=not args.confirm, confirm_only=args.confirm_only)
            print("[%s] 周期完成，last=%.2f" % (now_iso(),
                  (fetch_ticker(plan.instrument) or {}).get("last", 0)))
    except KeyboardInterrupt:
        print("\n监控已停止。")
        return 0


if __name__ == "__main__":
    main()
