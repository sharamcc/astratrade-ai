export function riskMessage(event: any) {
  const reason = event?.payload?.reason;
  return reason === "insufficient_cash_or_invalid_price" ? "可用余额不足，或行情价格无效，系统已跳过本次执行。" : reason || "风控规则拦截了本次执行。";
}

export function eventLabel(type: string) {
  return ({ agent_started: "Agent 已启动", agent_stopped: "Agent 已停止", agent_config_saved: "Agent 配置已保存", strategy_created: "策略已创建", strategy_started: "策略已启动", strategy_stopped: "策略已停止", strategy_copied: "策略已复制", strategy_deactivated: "策略已停用", strategy_config_saved: "策略配置已保存", strategy_order_filled: "策略模拟订单已成交", simulation_order_filled: "模拟订单已成交", simulation_snapshot_created: "模拟净值快照已生成", risk_blocked: "执行被风控拦截", market_price_stale: "行情暂不可用", strategy_paused_out_of_range: "策略因区间外行情暂停", data_export_completed: "数据导出已生成", notifications_marked_read: "通知已标记为已读", oauth_started: "OAuth 测试连接开始", oauth_completed: "OAuth 测试连接完成", oauth_revoked: "OAuth 测试连接已撤销", login_failed: "登录失败" } as Record<string, string>)[type] || "其他操作";
}
