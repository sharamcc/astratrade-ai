# AstraTrade AI 测试服部署

1. 将部署域名的 DNS A/AAAA 记录解析到服务器。
2. 将 `deploy/astratrade-ai.test.env` 安装为 `/etc/astratrade-ai/astratrade-ai.env`，把 `ASTRA_PUBLIC_ORIGIN` 和 `ASTRA_CALLBACK_REDIRECT_URI` 配置为部署域名。
3. 将 `web/dist` 构建产物随发布包部署到 `/opt/astratrade-ai/current/web/dist`。
4. 安装并启用 `astratrade-ai.service`、`astratrade-ai-worker.service`，应用进程只监听 `127.0.0.1:8080`。
5. 安装 `nginx/` 或 `Caddyfile.example` 中的一种 HTTPS 反向代理配置，并先执行配置语法检查再重载。
6. 安装并启用 `astratrade-ai-db-backup.service` 与 `.timer`，创建 `/var/backups/astratrade-ai` 并限制为应用用户可读。
7. 使用 `python3 bootstrap_admin.py --email <管理员邮箱>` 创建首位管理员，再登录控制台生成邀请码。

发布前必须备份 `/var/lib/astratrade-ai/app.sqlite3`。验证 `/health`、登录、CSRF 拒绝、密码修改、邀请码注册、Agent 启停、模拟订单和 OAuth 测试回调后，才开放部署域名。
