export function riskMessage(event: any) {
  const reason = event?.payload?.reason;
  return reason === "insufficient_cash_or_invalid_price" ? "可用余额不足，或行情价格无效，系统已跳过本次执行。" : reason || "风控规则拦截了本次执行。";
}

export function eventLabel(type: string) {
  return ({ agent_started: "Agent 已启动", agent_stopped: "Agent 已停止", simulation_order_filled: "模拟订单已成交", risk_blocked: "执行被风控拦截", oauth_started: "OAuth 测试连接开始", oauth_completed: "OAuth 测试连接完成", oauth_revoked: "OAuth 测试连接已撤销", login_failed: "登录失败" } as Record<string, string>)[type] || type;
}
