# AstraTrade AI 发布前验收记录

## 1. 验收范围

| 项目 | 结果 |
| --- | --- |
| 验收分支 | `main` |
| 当前提交 | `6b35dd5` |
| 测试服运行版本 | `fc633bb` |
| 验收时间 | 2026-09-20（Asia/Shanghai） |
| 运行模式 | `test`，仅模拟交易 |

## 2. 已通过项目

- [x] `main` 工作区干净，与 `origin/main` 同步。
- [x] 后端单元/API 测试：60 项通过。
- [x] 前端测试：2 项通过。
- [x] 前端生产构建通过。
- [x] `npm audit --omit=dev --audit-level=high`：0 vulnerabilities。
- [x] 测试服 API 与 Worker 重启后均为 `active`。
- [x] 健康检查返回 `status=ok`，版本为 `fc633bb`。
- [x] HTTP 入口 301 跳转 HTTPS。
- [x] HTTPS 页面返回 200，证书域名、签发方和有效期可验证。
- [x] 未登录访问受保护 API 返回 401，并带 `X-Request-ID`。
- [x] 响应包含 `X-Content-Type-Options`、`X-Frame-Options`、`Referrer-Policy` 和 HSTS。
- [x] 当前数据库和最近验收备份通过 SQLite `integrity_check`。
- [x] 三个内部验收策略重启后仍为 `stopped`，未继续产生模拟执行。
- [x] 三名内部用户的策略、权限隔离、风控、组合行情缺失和浏览器验收详见 [内部用户验收报告](../strategy-v2/INTERNAL_USER_ACCEPTANCE_REPORT.md)。

## 3. 尚未作为本次发布门槛通过的项目

- [ ] 尚未执行“将备份恢复到独立临时数据库后，再启动应用读取”的完整恢复演练；当前仅验证备份文件可读且完整性检查通过。
- [ ] 未在本次走查中重新执行 OAuth 浏览器回调；OAuth 状态重放、撤销和 HTTPS 回调已有自动化测试覆盖，但不等同于现场回调验收。
- [ ] 未开启外部用户试用；当前仍处于内部测试阶段。

## 4. 发布结论

当前版本适合继续作为内部测试服版本运行，不建议据此直接开放外部用户注册。开放前必须补齐独立数据库恢复演练和一次现场 OAuth 回调验收，并将结果追加到本文件。

