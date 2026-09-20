# AstraTrade AI — OKX 策略监控与执行原型

<p align="center">
  <img src="assets/astratrade-ai-logo.png" width="220" alt="AstraTrade AI logo">
</p>

一个自包含的 Python 交易 Agent 原型：读取 `trading_plan.json`，每 5 分钟扫描 OKX 公共行情，
按交易计划中的触发条件（基于 30m 收线确认）评估并执行。默认以只读/确认模式运行，供策略验证和小额测试使用；不构成投资建议或收益承诺。

## 多用户模拟控制台

AstraTrade AI 现在包含一个同域 React 控制台，用于邀请码用户验证 BTC/USDT 保守定投 Agent：

```bash
python3 -m pip install -r requirements.txt
cd web && npm install && npm run build && cd ..
ASTRA_RUN_MODE=test \
ASTRA_DB_PATH=/tmp/astratrade-ai.sqlite3 \
ASTRA_CALLBACK_REDIRECT_URI=https://test.invalid/oauth/callback \
ASTRA_SIM_PRICE=60000 \
python3 serve.py
```

浏览器访问 `http://127.0.0.1:8080`。生产/测试服使用 `deploy/` 下的 systemd Worker 与 Caddy 配置；先用 `bootstrap_admin.py` 创建管理员，再由管理员生成邀请码。该控制台只产生模拟订单，不读取真实资产、不执行实盘交易。

## 运行

```bash
# 仅记录意图（dry-run，默认）—— 扫描+评估+写日志/状态，不下单
INTERVAL_MIN=5 bash run_monitor.sh

# 真实下单（需 .env.live 凭证，且建议人工确认）
INTERVAL_MIN=5 CONFIRM_ONLY=0 bash run_monitor.sh
```

- 默认 `CONFIRM_ONLY=1`：只记录下单意图，不碰真钱。
- 手动单周期：`python3 monitor.py --once --confirm-only`

## 设计

- **标准库即可运行**（urllib/json）；不依赖任何第三方包。
- **行情**：OKX 公共 API（`/api/v5/market/ticker`、`/api/v5/market/candles?bar=30m`），无需凭证。
- **触发逻辑**（从 `trading_plan.json` 语义提炼，仅依赖 30m 已收线 close）：
  - `short_breakdown`：最近已收 30m 收线 close < 破位阈值(`no_trade_zone.low` = 2470.48)，
    且当前价回踩进入 `entry_range`，未重新站上 SL → 触发做空。
  - `long_reclaim`：最近已收 30m 收线 close > 回补阈值(`no_trade_zone.high` = 2491.20)，
    且当前价回踩进入 `entry_range`，未跌破破位阈值 → 触发做多。
- **执行**：仅当存在 `.env.live`（或 `.env.demo`）且含
  `OKX_API_KEY / OKX_API_SECRET / OKX_API_PASSPHRASE` 时才下单；否则只读。

## 安全约定

- 永不修改杠杆/止损/保护单（除非计划显式要求）；默认只开仓。
- 不往亏损单加仓；下单前核验未收盘 K 线、盘口与账户风险。
- 实盘下单前需二次确认（`--confirm`）；默认 dry-run 仅记录意图。
- 同一时刻假设单一方向持仓。

## 产物

- `.eth_usdt_execution_log.jsonl` — 每次扫描的观察/决策记录。
- `.eth_usdt_execution_state.json` — 当前 working state（last_observed_at、selected_setup、order_intent、decision）。
- `monitor.out` — 守护脚本的运行日志。

## 备注

原环境通过「OKX MCP 工具」执行；本运行时未提供该 MCP，故改用直连 OKX REST 的自包含实现，
行为与原有日志/状态文件保持一致。
