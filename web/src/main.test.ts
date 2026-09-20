import { describe, expect, it } from "vitest";
import { eventLabel, riskMessage } from "./presentation";

describe("console presentation helpers", () => {
  it("explains the main simulation risk decision", () => {
    expect(riskMessage({ payload: { reason: "insufficient_cash_or_invalid_price" } })).toContain("余额不足");
  });

  it("maps audit event names to user-facing Chinese labels", () => {
    expect(eventLabel("simulation_order_filled")).toBe("模拟订单已成交");
    expect(eventLabel("market_price_stale")).toBe("行情暂不可用");
    expect(eventLabel("unknown_event")).toBe("其他操作");
  });
});
