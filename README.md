# Feishu Codex Bot

一个通过飞书连接本机 Codex app server 的机器人服务端。项目使用 Python 实现，飞书侧通过长连接接收事件，Codex 侧通过 app server 的 JSON-RPC 协议接入。

## 功能概览

- 支持单聊与群聊两种会话模式
- 单聊会话 1 小时过期，群聊会话长期复用同一个 thread
- 支持 `/clear`、`/model`、`/compact`、`/init` 四个 slash 命令透传
- 飞书侧单条消息流式更新回复内容
- 回复期间给原消息添加“敲键盘”表情，结束后撤销
- 支持图片接收、下载到本地后转发给 Codex
- 支持图片与文件从本地上传后发回飞书
- 支持非白名单用户拦截与 owner 告警
- 支持 Codex 审批请求和补充输入的飞书文本协议桥接

## 安装依赖

```bash
python3 -m pip install -r requirements.txt
```

## 配置环境变量

1. 复制示例配置：

```bash
cp .env.example .env
```

2. 按需填写 `.env` 中的关键配置：

- `FEISHU_CODEX_BOT_FEISHU_APP_ID`
- `FEISHU_CODEX_BOT_FEISHU_APP_SECRET`
- `FEISHU_CODEX_BOT_CODEX_SERVER_URL`
- `FEISHU_CODEX_BOT_OWNER_USER_ID`
- `FEISHU_CODEX_BOT_ALLOWED_USER_IDS`

如果你使用 shell 导出环境变量，也可以不依赖 `.env` 文件，直接在当前环境中提供这些值。
其中 `FEISHU_CODEX_BOT_OWNER_USER_ID` 和 `FEISHU_CODEX_BOT_ALLOWED_USER_IDS` 当前应填写飞书 `open_id`，也就是通常形如 `ou_xxx` 的值，而不是 `user_id`。

## 启动方式

安装依赖并完成环境变量配置后，可直接启动：

```bash
PYTHONPATH=src python3 -m feishu_codex_bot.app
```

也可以使用安装后的脚本入口：

```bash
feishu-codex-bot
```

## 运行时数据位置

默认情况下，运行时文件会写入仓库根目录下的 `var/`：

- SQLite 数据库：`var/app.db`
- 媒体文件：`var/media/`
- 结构化日志：`var/logs/app.log`

你也可以通过 `.env.example` 中的这些变量覆盖默认位置：

- `FEISHU_CODEX_BOT_DATA_DIR`
- `FEISHU_CODEX_BOT_SQLITE_PATH`
- `FEISHU_CODEX_BOT_MEDIA_DIR`
- `FEISHU_CODEX_BOT_LOGS_DIR`

## 飞书开放平台配置

飞书机器人所需权限和事件订阅说明见：

- [docs/feishu_app_permissions.json](docs/feishu_app_permissions.json)
- [docs/feishu_events.json](docs/feishu_events.json)
- [docs/feishu_callback.json](docs/feishu_callback.json)
- [docs/feishu_app_permissions.md](docs/feishu_app_permissions.md)
- [docs/feishu_event_subscription.md](docs/feishu_event_subscription.md)
- [docs/feishu_open_platform_config.md](docs/feishu_open_platform_config.md)

其中：

- 权限 JSON 用于“权限管理 -> 批量导入”
- 事件 JSON 和回调 JSON 用于结构化列出当前项目实际依赖的配置项
- Markdown 文件说明三类配置各自的用途、平台设置方式和当前官方资料边界

## 测试

当前仓库提供离线单元测试，不依赖真实飞书服务和真实 Codex 服务：

```bash
PYTHONPATH=src python3 -m pytest tests/test_conversation_service.py tests/test_security_service.py
```

## 调试建议

- 首先把 `FEISHU_CODEX_BOT_LOG_LEVEL` 设为 `DEBUG`
- 重点查看 `var/logs/app.log`
- 如果消息没有进入 Codex，优先检查：
  - 用户是否在白名单中
  - 群消息是否 `@` 了机器人
  - 飞书开放平台是否已启用长连接事件和权限
- 如果图片没有被正确处理，优先检查：
  - `im:resource` 权限是否已授权
  - `var/media/` 下是否有下载文件
- 如果审批或补充输入没有闭环，优先检查：
  - Codex app server 是否发出了 server request
  - 飞书中回复的 `/approve` 或 `/input` 格式是否正确
