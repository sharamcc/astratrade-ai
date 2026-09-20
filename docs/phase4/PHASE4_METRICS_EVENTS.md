# AstraTrade AI Phase 4 指标与事件字典

## 1. 统一约定

### 1.1 事件字段

所有产品事件至少包含：

| 字段 | 类型 | 说明 |
| --- | --- | --- |
| event_id | string | 服务端生成的唯一事件 ID |
| event_type | string | 稳定的事件名称 |
| occurred_at | ISO-8601 | UTC 发生时间 |
| user_id | string/null | 用户 ID；聚合系统可脱敏 |
| request_id | string | 关联请求 ID |
| source | string | `web`、`worker`、`admin` 或 `system` |
| payload | object | 事件专属字段，不放密码和 Token |

同一业务动作必须使用幂等键，重复请求不能重复计数为成交、通知或导出。

### 1.2 金额与数量

- USDT 金额使用字符串或定点数，禁止二进制浮点作为持久化口径；
- BTC 数量至少保留 8 位小数；
- 手续费固定按模拟订单名义金额的 0.1% 计算；
- 所有报表标注“模拟数据”。

## 2. 产品指标

| 指标 | 定义 | 统计窗口 | 目标/用途 |
| --- | --- | --- | --- |
| activation_rate | 完成首次模拟成交的注册用户 / 注册用户 | 7 日 | 衡量首次使用门槛 |
| first_run_success_rate | 首次启动后成功成交的次数 / 首次启动次数 | 7 日 | 识别行情和配置问题 |
| order_detail_view_rate | 查看过订单详情的用户 / 有订单用户 | 7 日 | 衡量结果解释性 |
| risk_explanation_success | 测试中正确理解风控原因的用户比例 | 每轮测试 | 衡量可解释性 |
| agent_retention_7d | 7 天后仍有运行或再次启动记录的用户 / 启动用户 | 7 日 | 衡量持续使用 |
| export_success_rate | 成功生成导出的请求 / 导出请求 | 7 日 | 衡量数据可取性 |
| duplicate_order_count | 相同幂等键生成的成交数量 | 每日 | 必须为 0 |
| cross_user_access_count | 跨用户资源访问成功数量 | 每日 | 必须为 0 |
| notification_delivery_rate | 成功生成并可见的通知 / 应生成通知 | 每日 | 衡量通知可靠性 |
| stale_price_skip_rate | 因行情过期跳过的执行 / 到期执行 | 每日 | 观察行情适配器质量 |

## 3. 事件字典

### 3.1 用户与配置事件

| 事件 | 触发时机 | 必填 payload |
| --- | --- | --- |
| `user_registered` | 注册成功 | `invite_source` |
| `login_succeeded` | 登录成功 | `method` |
| `login_failed` | 登录失败 | `reason_category` |
| `agent_template_selected` | 选择策略模板 | `template_id` |
| `agent_config_saved` | 保存 Agent 配置 | `budget_usdt`, `frequency`, `template_id` |
| `agent_started` | Agent 启动成功 | `frequency`, `budget_usdt` |
| `agent_stopped` | Agent 停止成功 | `stop_source` |

### 3.2 执行与风险事件

| 事件 | 触发时机 | 必填 payload |
| --- | --- | --- |
| `simulation_execution_started` | Worker 或用户触发执行 | `execution_key`, `instrument` |
| `simulation_order_filled` | 模拟成交完成 | `order_id`, `price`, `quantity`, `fee_usdt` |
| `risk_blocked` | 服务端风控拦截 | `reason`, `budget_usdt`, `price` |
| `market_price_stale` | 行情超过有效期 | `instrument`, `age_seconds` |
| `simulation_execution_failed` | 执行异常结束 | `failure_category` |
| `simulation_snapshot_created` | 生成净值快照 | `equity_usdt`, `pnl_usdt` |

风控 reason 使用稳定枚举：

- `insufficient_cash_or_invalid_price`
- `stale_market_price`
- `duplicate_execution`
- `budget_limit_exceeded`
- `agent_not_running`

### 3.3 用户操作事件

| 事件 | 触发时机 | 必填 payload |
| --- | --- | --- |
| `order_detail_viewed` | 打开订单详情 | `order_id` |
| `notification_viewed` | 查看通知 | `notification_id` |
| `notifications_marked_read` | 批量标记已读 | `count` |
| `data_export_requested` | 请求导出 | `export_type`, `range` |
| `data_export_completed` | 导出完成 | `export_type`, `row_count` |
| `data_export_failed` | 导出失败 | `failure_category` |
| `oauth_revoked` | 撤销测试连接 | `provider` |

## 4. 数据质量规则

- 事件时间统一使用 UTC；
- 事件类型不得因页面文案变化而改变；
- 失败事件必须带分类原因，不记录原始异常堆栈给用户；
- 订单成交事件必须能关联唯一订单和执行幂等键；
- 净值快照不得覆盖历史记录；
- 导出数据必须与当前用户权限一致；
- 管理员聚合统计不得暴露单个用户的敏感信息。

## 5. 埋点验收

一次完整测试链路至少产生：注册、配置保存、Agent 启动、执行开始、成交或风控拦截、快照、通知、订单详情查看、Agent 停止等事件，并且每个事件可通过 `request_id` 或业务 ID 关联。
