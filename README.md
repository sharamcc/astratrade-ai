# AstraTrade AI

<p align="center">
  <img src="assets/astratrade-ai-logo.png" width="220" alt="AstraTrade AI logo">
</p>

AstraTrade AI 是一个面向 OKX Builder/Connect 场景的交易 Agent 原型。目前仓库包含两条边界清晰的运行路径：

1. **多用户模拟控制台**：邀请码注册、邮箱密码登录、Agent 配置、模拟账户、风险检查、订单和审计。首版只模拟 BTC/USDT 现货，不读取真实资产、不提交真实订单。
2. **旧版策略监控器**：`monitor.py` / `run_monitor.sh`，用于验证既有交易计划，默认 dry-run。它与多用户控制台相互独立。

## 多用户模拟控制台

### 本地运行

需要 Python 3.12、Node.js 20 或更高版本：

```bash
python3 -m pip install -r requirements.txt
cd web && npm install && npm run build && cd ..

ASTRA_RUN_MODE=test \
ASTRA_DB_PATH=/tmp/astratrade-ai.sqlite3 \
ASTRA_CALLBACK_REDIRECT_URI=https://test.invalid/oauth/callback \
ASTRA_SIM_PRICE=60000 \
python3 serve.py
```

然后访问 <http://127.0.0.1:8080>。

测试模式使用 stub OAuth 和模拟价格，不连接 OKX 私有接口。第一次初始化管理员：

```bash
python3 bootstrap_admin.py --db /tmp/astratrade-ai.sqlite3 --email admin@example.com
```

管理员登录后可生成邀请码，测试用户凭邀请码注册。

### 功能边界

- 每个用户拥有独立的模拟账户，默认余额为 10,000 USDT。
- Agent 使用保守定投策略，支持每日/每周执行。
- 单次模拟预算上限为 500 USDT，手续费按 0.1% 计算。
- 模拟订单由 Worker 调度并写入订单、风险决策和审计记录。
- OKX OAuth 页面保留测试闭环；正式 Builder 凭据必须通过服务器环境变量配置，不能提交到仓库。

## 测试与构建

```bash
python3 -m unittest discover -s tests -v
cd web && npm run build && npm audit --omit=dev --audit-level=high
```

GitHub Actions 会自动执行后端测试和前端构建。

## 测试服部署

部署文件位于 [`deploy/`](deploy/)：

- `astratrade-ai.service`：HTTP API 与前端静态资源服务。
- `astratrade-ai-worker.service`：模拟 Agent 调度器。
- `nginx/astratrade-ai-dev.zjy.xyz.conf`：HTTPS 反向代理配置。
- `deploy/README.md`：证书、域名、数据库备份和回滚步骤。

测试服当前入口为：<https://astratrade-ai-dev.zjy.xyz>。

公网只开放 Nginx 的 80/443，Python 服务仅监听本机 `127.0.0.1:8080`。测试服使用 HTTPS 安全 Cookie；不要把私钥、密码或 OKX 生产凭据提交到 Git。

## 旧版策略监控器

```bash
# 默认只记录观察、决策和下单意图，不提交订单
INTERVAL_MIN=5 bash run_monitor.sh

# 仅在明确配置凭证并确认风险后使用
INTERVAL_MIN=5 CONFIRM_ONLY=0 bash run_monitor.sh
```

旧版监控器读取 `trading_plan.json`，使用 OKX 公共行情接口评估策略。`CONFIRM_ONLY=1` 是默认值；真实下单路径不属于多用户控制台，也不应直接用于公开测试服。

## 安全与免责声明

- 模拟控制台不构成投资建议，也不承诺收益。
- 所有浏览器账户使用 Argon2 密码哈希、服务端会话和邀请码注册。
- 真实交易需要单独的凭据、权限、合规审查和人工风险确认。
