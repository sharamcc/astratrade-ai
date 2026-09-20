# AstraTrade AI

<p align="center">
  <img src="assets/astratrade-ai-logo.png" width="220" alt="AstraTrade AI logo">
</p>

AstraTrade AI 是一个面向 OKX Builder/Connect 场景的多用户交易 Agent 模拟控制台。

## 核心能力

- 邀请码注册与邮箱密码登录。
- 每个用户独立的模拟账户，默认余额为 10,000 USDT。
- BTC/USDT 现货保守定投 Agent，支持每日/每周执行。
- 单次模拟预算上限为 500 USDT，模拟手续费按 0.1% 计算。
- Agent 启停、模拟订单、风险决策和审计记录。
- OKX OAuth 测试闭环；正式 Builder 凭据通过服务器环境变量配置。
- 模拟模式不读取真实资产、不提交真实订单。

## 本地运行

需要 Python 3.12、Node.js 20 或更高版本：

```bash
python3 -m pip install -r requirements.txt
cd web && npm install && npm run build && cd ..

ASTRA_RUN_MODE=test \
ASTRA_DB_PATH=/tmp/astratrade-ai.sqlite3 \
ASTRA_CALLBACK_REDIRECT_URI=https://your-domain.example/oauth/callback \
ASTRA_SIM_PRICE=60000 \
python3 serve.py
```

启动后打开本地控制台。测试模式使用 stub OAuth 和模拟价格，不连接 OKX 私有接口。

第一次初始化管理员：

```bash
python3 bootstrap_admin.py --db /tmp/astratrade-ai.sqlite3 --email admin@example.com
```

管理员登录后可生成邀请码，用户凭邀请码注册。

## 测试与构建

```bash
python3 -m unittest discover -s tests -v
cd web && npm run build && npm audit --omit=dev --audit-level=high
```

GitHub Actions 会自动执行后端测试和前端构建。

## 部署

部署文件位于 [`deploy/`](deploy/)：

- `astratrade-ai.service`：HTTP API 与前端静态资源服务。
- `astratrade-ai-worker.service`：模拟 Agent 调度器。
- `nginx/`：HTTPS 反向代理配置示例。
- `deploy/README.md`：证书、域名、数据库备份和回滚步骤。

域名、回调地址和密钥只能通过部署环境配置，不应写入项目文档或代码仓库。公网入口应由 HTTPS 反向代理提供，应用进程只监听本机地址。

## 安全与免责声明

- 模拟控制台不构成投资建议，也不承诺收益。
- 浏览器账户使用 Argon2 密码哈希、服务端会话和邀请码注册。
- 私钥、账户密码和 OKX 生产凭据不得提交到 Git。
- 真实交易需要单独的凭据、权限、合规审查和人工风险确认。
