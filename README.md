# Feishu Codex Bot

一个通过飞书连接本机 Codex app server 的机器人服务端。项目使用 Python 实现，飞书侧通过长连接接收事件，Codex 侧通过 app server 的 JSON-RPC 协议接入。

## 功能概览

- 支持单聊与群聊两种会话模式，群聊只接受at机器人的消息
- 单聊会话 1 小时过期，群聊会话长期复用同一个 thread
- 支持 `/clear`、`/model`、`/compact`、`/init` 四个 slash 命令透传
- 飞书侧通过 CardKit 卡片流式更新单条回复内容
- 支持图片与文件从本地上传后发回飞书
- 支持非白名单用户拦截与 owner 告警
- 支持 Codex 审批请求和补充输入的飞书文本协议桥接

主流程时序图见：[docs/main_sequence.md](docs/main_sequence.md)

## 前置条件
### CODEX
1. 需要在本地配置好codex的cli环境，能正常工作。当前适配版本为codex-cli 0.120.0

### 飞书机器人
1. 飞书开放平台上创建自己的机器人应用：https://open.feishu.cn/app
2. 飞书开放平台-权限管理-批量导入权限：[应用权限](docs/feishu_app_permissions.json)
3. 飞书开放平台-事件与回调-事件配置-订阅方式-使用长连接接收事件，添加事件[应用事件](docs/feishu_event_subscription.md)
4. 飞书开放平台-事件与回调-回调配置-订阅方式-使用长连接接收回调，添加事件[应用回调](docs/feishu_app_callback.md)
5. 版本发布

## 服务启动
### 启动codex
1. 启动codex app-server：`codex app-server --listen ws://127.0.0.1:9000`

### 启动codex-feishu-bot
#### 依赖的环境变量(参考env.example)
| 环境变量 | 示例值 | 说明 |
|--------|--------|------|
| FEISHU_CODEX_BOT_FEISHU_APP_ID | cli_xxxxxxxxxxxxx | 飞书应用的 App ID，由飞书开放平台创建应用后获得 |
| FEISHU_CODEX_BOT_FEISHU_APP_SECRET | xxxxxxxxxxxxxxxxxx | 飞书应用的 App Secret，由飞书开放平台创建应用后获得 |
| FEISHU_CODEX_BOT_CODEX_SERVER_URL | ws://127.0.0.1:9000 | 本机 Codex app server 的访问地址（JSON-RPC 连接入口） |
| FEISHU_CODEX_BOT_OWNER_USER_ID | ou_xxxxxxxxxxxxxxxxxxxxxxxxxx | owner 的飞书用户open_id，用于接收安全告警（需在白名单中）,获取方式参考https://open.feishu.cn/document/faq/trouble-shooting/how-to-obtain-openid |
| FEISHU_CODEX_BOT_ALLOWED_USER_IDS | ou_xxxxxxxxxxxxxxxxxxxxxxxx | 允许访问机器人的用户 ID 白名单（多个用逗号分隔） |
| FEISHU_CODEX_BOT_DATA_DIR | var | 数据目录（默认存放数据库、日志、媒体文件） |
| FEISHU_CODEX_BOT_SQLITE_PATH | var/app.db | SQLite 数据库路径（会覆盖默认 DATA_DIR 路径） |
| FEISHU_CODEX_BOT_MEDIA_DIR | var/media | 媒体文件目录（用于存储下载的图片等） |
| FEISHU_CODEX_BOT_LOGS_DIR | var/logs | 日志目录（存放 app.log 等日志文件） |
| FEISHU_CODEX_BOT_LOG_LEVEL | DEBUG | 日志级别（DEBUG / INFO / WARNING / ERROR / CRITICAL） |

#### 启动命令
1. 在release中获取最新的.whl，通过pip安装。（建议在venv中进行）
2. 运行app，默认当前目录就是workspace：`feishu-codex-bot`

## 信息安全
1. codex-feishu-bot会直接连接当前环境下的codex，具备和codex cli相同的权限，存在误操作的风险，请正确配置codex的权限
2. 为了避免未经授权的访问，务必保证
* 正确配置允许使用此功能的飞书用户open_id
* 群聊中建议只有用户（你自己）和机器人，不要拉其他人进群
