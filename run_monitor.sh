#!/usr/bin/env bash
# okx-latest 交易计划 5 分钟监控循环
# 读取 trading_plan.json -> 每 5 分钟扫描 OKX 公共行情 -> 按触发条件评估/执行
# monitor.py 默认进入内部循环（按 --interval-min 间隔），本脚本仅负责守护运行。
set -uo pipefail

DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$DIR"

INTERVAL_MIN="${INTERVAL_MIN:-5}"
CONFIRM_ONLY="${CONFIRM_ONLY:-1}"   # 1=仅记录意图；0=真实下单（需 .env.live 凭证）

export PYTHONUNBUFFERED=1
echo "[$(date '+%F %T')] 启动监控: interval=${INTERVAL_MIN}m confirm_only=${CONFIRM_ONLY}" | tee "$DIR/monitor.out"

while true; do
  if [ "$CONFIRM_ONLY" = "1" ]; then
    python3 "$DIR/monitor.py" --interval-min "$INTERVAL_MIN" --confirm-only >> "$DIR/monitor.out" 2>&1
  else
    python3 "$DIR/monitor.py" --interval-min "$INTERVAL_MIN" >> "$DIR/monitor.out" 2>&1
  fi
  echo "[$(date '+%F %T')] 周期结束，${INTERVAL_MIN} 分钟后重试..." | tee -a "$DIR/monitor.out"
  sleep 1
done
