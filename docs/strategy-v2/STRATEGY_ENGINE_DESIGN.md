# AstraTrade AI Strategy V2 策略引擎架构设计

## 1. 设计原则

- 策略只负责产生信号，不直接修改余额或创建订单；
- 所有信号必须经过服务端风控、账本事务和幂等校验；
- 行情、策略计算、风控、执行和通知分层；
- 同一策略版本和执行上下文必须可重放、可审计；
- 多用户数据按 `user_id` 强制隔离；
- V2 仍以 SQLite 单机测试部署为目标，不引入生产级分布式依赖。

## 2. 逻辑架构

```text
Market Adapter
      |
      v
Execution Scheduler -> Strategy Runtime -> Signal
                                      |
                                      v
                              Risk Engine
                                      |
                         +------------+------------+
                         |                         |
                    Denied Decision          Allowed Decision
                         |                         |
                         v                         v
                    Audit/Notice          Ledger Transaction
                                                   |
                                           Simulated Order
                                                   |
                                           Snapshot/Notice
```

API 进程负责认证、配置读取、页面和查询；Worker 负责到期策略、行情读取、策略计算、风控和模拟成交。

## 3. 策略接口

逻辑接口：

```text
StrategyDefinition
  strategy_type: dca | moving_average | grid | portfolio_dca
  version: string
  validate_config(config) -> ValidationResult
  build_context(config, market_snapshot, account_snapshot) -> StrategyContext
  evaluate(context) -> SignalSet
```

信号字段：

| 字段 | 说明 |
| --- | --- |
| signal_id | 单次信号唯一标识 |
| user_id | 所属用户 |
| strategy_id | 策略实例 |
| strategy_version | 产生信号的版本 |
| instrument | 交易标的 |
| action | buy/sell/hold/skip |
| requested_notional | 请求金额 |
| reason | 可读触发原因 |
| market_snapshot_id | 使用的行情快照 |
| created_at | 产生时间 |

策略实现不得调用账本、订单仓储或外部私有交易 API。

## 4. 数据模型

建议新增或扩展：

- `strategy_instances`：用户、类型、当前版本、状态、配置 JSON；
- `strategy_versions`：不可变配置、创建人、创建时间和停用时间；
- `market_snapshots`：标的、价格、采集时间、来源和有效期；
- `strategy_signals`：信号、原因、行情快照和执行状态；
- `risk_decisions`：允许/拒绝、规则、限制值和实际值；
- `execution_runs`：执行键、策略版本、开始/结束时间和最终状态；
- `portfolio_allocations`：组合标的、比例/金额和排序。

现有 `sim_accounts`、`sim_orders`、`sim_snapshots`、`console_audit` 和通知表继续作为账本、订单和审计基础。所有新增表必须支持可重复迁移，并为 `user_id`、`execution_key`、`created_at` 建索引。

## 5. 执行流程

1. Worker 找到到期且处于运行状态的策略实例；
2. 获取公开行情并校验价格、时间戳和有效期；
3. 读取用户账户、持仓和当日风险状态；
4. Strategy Runtime 生成一个或多个信号；
5. Risk Engine 对每个信号执行预算、余额、持仓、频率、冷却和每日损失检查；
6. 生成不可变风险决策和审计事件；
7. 对允许的信号在同一数据库事务中执行幂等账本更新、手续费计算、订单写入和净值快照；
8. 写入通知并更新执行状态；
9. 计算下一次执行时间；
10. 失败时不扣款、不创建成交订单，并记录可重试原因。

## 6. 幂等与并发

执行键建议由以下字段组成：

```text
user_id:strategy_id:strategy_version:scheduled_at:signal_id
```

数据库对执行键建立唯一约束。Worker 重试时必须复用同一执行键；事务失败时整体回滚；策略停止后，已经开始的事务完成，后续调度不再创建新执行。

## 7. 风控边界

- 用户只能降低环境配置的上限，不能提高上限；
- 组合总预算必须在组合级和标的级分别校验；
- 网格区间外默认暂停新订单；
- 均线信号必须经过冷却和重复信号检查；
- 行情失效时跳过执行，不使用旧价格成交；
- 风控拒绝必须包含规则名、阈值、实际值和用户可读原因；
- 所有策略默认模拟模式，真实 Provider 由环境变量显式开启。

## 8. API 与前端边界

建议接口：

- `GET/POST /v1/strategies`
- `GET/PUT /v1/strategies/{id}`
- `POST /v1/strategies/{id}/start`
- `POST /v1/strategies/{id}/stop`
- `GET /v1/strategies/{id}/signals`
- `GET /v1/strategies/{id}/runs`
- `GET /v1/strategies/{id}/performance`

所有接口通过登录会话、CSRF、用户范围和服务端参数校验保护。前端只提交配置和操作意图，不提交执行键、成交价格或最终金额。

## 9. 迁移与部署

- 先创建新表和索引，再迁移现有定投配置为 `dca` 策略实例；
- 迁移必须可重复执行，旧账本和订单只读保留；
- 部署前备份 SQLite，部署后先执行迁移，再重启 API 和 Worker；
- Worker 启动后执行健康检查，但不自动启动已停止策略；
- 回滚使用上一发布目录和对应数据库备份。

## 10. 可观测性

每次执行至少记录：策略类型、版本、用户范围、行情快照、信号、风控决策、执行键、订单、手续费、快照、耗时和失败原因。日志禁止记录密码、会话 ID、OAuth Token 或私有凭据。
