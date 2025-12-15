# nodeseek-bot

面向 Debian 12 的 Nodeseek 新帖聚合 Telegram Bot：RSS 轮询 →（可选）抓全文 → AI 总结 → 规则评分 → 频道推送 + 私聊命令管理。

## Docker 部署（Alpine，本地构建镜像）
> 适合不想手装 Python/venv 的场景。该镜像基于 Alpine，**不包含 Playwright**（因此不会有浏览器兜底抓全文）。

1) 准备配置文件
```bash
cp .env.example .env
# 编辑 .env，至少填写 BOT_TOKEN / TARGET_CHAT_ID / ADMIN_USER_ID / AI_BASE_URL / AI_API_KEY / AI_MODEL
# 图片识别总结（可选）：IMAGE_SUMMARY_ENABLED=true（默认开启），并按需调整 IMAGE_MAX_COUNT/IMAGE_MAX_BYTES 等
# Markdown 富结构（可选，默认开启）：抓全文后会把正文 HTML 转为 Markdown-like 结构（标题/列表/代码块/表格/引用/链接/图片）再交给 AI，总结更准确；可用 RICH_TEXT_ENABLED=false 关闭
```

2) 构建并启动
```bash
docker compose up -d --build
```

3) 查看日志
```bash
docker compose logs -f
```

4) 因为权限问题会报错，需要修复
```bash
# 确保你在 docker-compose.yml 所在的目录
docker compose down
chmod -R 777 data logs rules
docker compose up -d
```
4) 指标/状态
- Prometheus 指标：`http://127.0.0.1:9108/metrics`
- 状态文件：容器内 `/app/data/status.json`（compose 默认已把 `./data` 挂载到该目录）

5) 重要说明
- Alpine 镜像 **不包含 Playwright**，因此 `ALLOW_BROWSER_FALLBACK` 在 compose 里默认设置为 `false`。
- 如需让 metrics 端口对外可访问：保持 `METRICS_BIND=0.0.0.0`（Dockerfile/compose 已默认设置）。
- 建议持久化目录：`./data`（sqlite + status）、`./logs`（日志）、`./rules`（规则与 overrides）。

---

## Debian 12 部署前置
你需要提前准备：
- 一台 Debian 12 VPS
- 一个 Telegram Bot Token（从 @BotFather 获取）
- 你的 TG 频道/群组 ID（作为推送目标）
- （可选但推荐）`NODESEEK_COOKIE`：从浏览器复制你自己的 Cookie 字符串，手动填入 `.env` 或 `/etc/nodeseek-bot.env`
  - 不填 Cookie：自动仅 RSS（不抓全文）
  - 填 Cookie：允许抓全文（仍受严格限速与风控降级策略影响）

注意：不要把真实 Cookie 提交到仓库（不要写进 README、不要提交 `.env`）。

## Debian 12 VPS 快速测试（不使用 systemd）
> 适合你先在 VPS 上验证功能/调参，确认没问题后再上 systemd。

1) 安装系统依赖
```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
```

2) 获取代码并创建 venv（示例 `/opt/nodeseek-bot`）
```bash
sudo mkdir -p /opt/nodeseek-bot
sudo chown -R $USER:$USER /opt/nodeseek-bot
cd /opt/nodeseek-bot

# 二选一：
# 方式 A：GitHub clone
git clone <YOUR_REPO_URL> .
# 方式 B：你自己上传代码到该目录

python3.11 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
```

3) 准备运行配置（放项目目录内）
```bash
cd /opt/nodeseek-bot
cp .env.example .env
chmod 600 .env
nano .env
```

你需要至少填写：
- `BOT_TOKEN`
- `TARGET_CHAT_ID`
- `ADMIN_USER_ID`
- `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL`
- （可选但推荐）`NODESEEK_COOKIE`：手动填入你自己的 Cookie；不填则自动仅 RSS

4) 启动（前台）
```bash
cd /opt/nodeseek-bot
./.venv/bin/python -m nodeseek_bot --env .env
```

5) 启动（后台，推荐 tmux）
```bash
sudo apt-get install -y tmux
cd /opt/nodeseek-bot

tmux new -s nodeseek-bot
./.venv/bin/python -m nodeseek_bot --env .env
# 退出 tmux：Ctrl+B 然后按 D
```

6) 查看指标/状态
- Prometheus 指标：`curl http://127.0.0.1:9108/metrics`
- 状态文件：`cat data/status.json`

7) 常见坑
- 不要用系统自带 `python -m nodeseek_bot`（可能不是 venv 环境）
- 一旦看到“反爬/验证码/异常跳转”告警，程序会自动停用全文抓取，仅 RSS 模式；更新 Cookie 后可重启进程恢复。

---

## Debian 12 VPS 部署（建议，上 systemd）
> 如果你确认要常驻运行，再按本节接入 systemd。

1) 安装系统依赖
```bash
sudo apt-get update
sudo apt-get install -y python3.11 python3.11-venv
```

2) 创建运行用户与目录
```bash
sudo useradd --system --no-create-home --shell /usr/sbin/nologin nodeseekbot || true
sudo mkdir -p /opt/nodeseek-bot
sudo chown -R $USER:$USER /opt/nodeseek-bot
```

3) 获取代码并创建 venv
```bash
cd /opt/nodeseek-bot
# 如果之前已 clone/上传，可以跳过 clone

python3.11 -m venv .venv
./.venv/bin/pip install -U pip
./.venv/bin/pip install -r requirements.txt
```

4) 配置环境变量文件（生产）
```bash
sudo cp /opt/nodeseek-bot/.env.example /etc/nodeseek-bot.env
sudo chmod 600 /etc/nodeseek-bot.env
sudo chown root:root /etc/nodeseek-bot.env
sudo nano /etc/nodeseek-bot.env
```

至少填写：
- `BOT_TOKEN`
- `TARGET_CHAT_ID`
- `ADMIN_USER_ID`
- `AI_BASE_URL` / `AI_API_KEY` / `AI_MODEL`
- （可选但推荐）`NODESEEK_COOKIE`：手动填入你自己的 Cookie；不填则自动仅 RSS

5)（可选）启用 Playwright 兜底
```bash
cd /opt/nodeseek-bot
./.venv/bin/pip install -r requirements-playwright.txt
./.venv/bin/python -m playwright install --with-deps chromium
```

6) 安装并启动 systemd 服务
```bash
sudo mkdir -p /opt/nodeseek-bot/data /opt/nodeseek-bot/logs
sudo chown -R nodeseekbot:nodeseekbot /opt/nodeseek-bot/data /opt/nodeseek-bot/logs
sudo cp /opt/nodeseek-bot/systemd/nodeseek-bot.service /etc/systemd/system/nodeseek-bot.service
sudo systemctl daemon-reload
sudo systemctl enable --now nodeseek-bot
```

7) 查看日志
```bash
sudo journalctl -u nodeseek-bot -f
```

## 规则调整
- 默认规则：`rules/rules.yaml`
- 命令写入覆盖：`rules/overrides.yaml`（由 `/whitelist_add`、`/blacklist_add`、`/set_threshold` 等命令修改）

## 常用管理命令（私聊 Bot，管理员可用）
- `/status`：当前运行状态、限速下一次允许时间、连续失败数
- `/pause` / `/resume`：暂停/恢复处理
- `/set_threshold <n>`：调整推送阈值
- `/whitelist_add <kw>` / `/blacklist_add <kw>`：添加关键词
- `/rules_reload`：重载规则
- `/last [n]`：查看最近 n 条处理记录
- `/reprocess <post_id>`：重跑某条（会清除该帖已推送记录）

## 风控提示
- 带 Cookie 抓取可能触发站点风控或与 ToS 冲突：本项目默认严格限速（HTML 1 次/5 分钟）、并发=1、退避重试。
- 若检测到 Cloudflare challenge/验证码/异常跳转，将停止全文抓取并切换仅 RSS 模式，同时 TG 告警。
