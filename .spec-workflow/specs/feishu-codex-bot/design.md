# Feishu Codex Bot Design

## 1. 设计目标

本设计用于实现一个 Python 编写的飞书机器人服务端。服务端通过飞书长连接接收事件，通过本机 Codex app server 的 JSON-RPC 协议发起对话，并将 Codex 的增量输出、图片、文件和审批请求映射回飞书。

设计需要满足以下核心目标：

- 一个服务进程只承载一个飞书机器人实例，部署者通过多次启动服务运行多个机器人。
- 单聊和群聊的 thread 生命周期必须稳定、可持久化、可跨重启恢复。
- 所有用户消息都必须先经过用户白名单校验，只有合法用户才能驱动 Codex。
- 所有用户消息都必须经过幂等校验，保证同一条飞书消息最多只进入 Codex 一次。
- Codex 的一次回复在飞书侧始终更新同一条消息，直到该次回复结束。
- 飞书图片先下载到本地，再通过 Codex app server 的本地图片输入能力提交。
- 系统在运行时提供足够清晰的结构化日志，支持快速定位问题。

## 2. 已确认的协议与能力边界

### 2.1 Codex app server

基于本仓库 `schemas/` 目录，设计采用以下协议能力：

- 通过 `thread/start` 创建新 thread，通过 `thread/resume` 或本地持久化的 `threadId` 继续既有 thread。
- 通过 `turn/start` 向指定 `threadId` 发送输入。
- `turn/start.input` 支持多种 `UserInput`，其中至少包括：
  - `text`
  - `image`
  - `localImage`
- 服务端通知至少包含：
  - `item/agentMessage/delta`
  - `item/started`
  - `item/completed`
  - `turn/completed`
- 服务端反向请求至少包含：
  - `item/commandExecution/requestApproval`
  - `item/fileChange/requestApproval`
  - `item/permissions/requestApproval`
  - `item/tool/requestUserInput`

这意味着桥接层不需要伪造自己的“对话协议”，而应直接围绕 Codex 的 thread/turn 生命周期建模。

### 2.2 飞书接入

飞书侧采用长连接模式，优先使用飞书官方 Python SDK 实现事件订阅与服务端 API 调用；原因是官方 SDK已经覆盖鉴权、事件处理和服务端 API 封装，能显著降低接入复杂度。官方 SDK 的公开仓库说明其目标包括“调用飞书开放 API、处理订阅事件、处理卡片行为”。来源：

- 官方 SDK 仓库：<https://github.com/larksuite/oapi-sdk-python>
- 用户提供的飞书 IM 文档入口：<https://open.feishu.cn/document/server-docs/im-v1/introduction>

对飞书侧，本设计只依赖这些能力：

- 长连接接收消息事件和群聊相关事件
- 发送和更新文本消息
- 发送图片和文件消息
- 对消息添加和撤销表情回应
- 发送审批或交互消息

具体 API 名称与权限细节在实现阶段以飞书官方文档为准。

## 3. 总体架构

```text
Feishu Long Connection
    |
    v
Feishu Adapter
    |
    v
Inbound Event Pipeline
  - normalize
  - authorize
  - dedupe
  - resolve session
  - download media
  - enqueue by session
    |
    v
Conversation Orchestrator
    |
    +--> Session Store (SQLite)
    +--> Media Store (local files)
    +--> Idempotency Store (SQLite)
    +--> Reply State Store (SQLite)
    |
    v
Codex RPC Client
  - initialize
  - thread/start
  - turn/start
  - handle notifications
  - handle server requests
    |
    v
Outbound Renderer
  - create reply message
  - throttle updates
  - send images/files
  - send approval messages
  - finalize / clear reaction
```

系统按职责分成 8 个核心模块：

1. `config`
   - 读取环境变量
   - 组装飞书、Codex、SQLite、日志、临时文件目录、owner 和白名单等配置

2. `feishu_adapter`
   - 建立飞书长连接
   - 接收事件并标准化为内部消息模型
   - 调用飞书消息、图片、文件、表情、审批相关 API

3. `codex_client`
   - 维护到本机 Codex app server 的持久 JSON-RPC 连接
   - 管理请求 ID、响应匹配、通知分发、服务端反向请求处理

4. `conversation_service`
   - 负责单聊/群聊会话路由
   - 负责 `/clear`、slash 命令判断
   - 负责将飞书消息转成 Codex `UserInput[]`

5. `security_service`
   - 校验发送者是否在白名单中
   - 拦截未授权消息
   - 向 owner 发送告警
   - 记录访问控制日志

6. `persistence`
   - 持久化 session、消息幂等记录、回复映射、待处理审批请求
   - 使用 SQLite 作为本地嵌入式数据库

7. `media_service`
   - 下载飞书图片到本地
   - 管理临时文件与输出文件上传

8. `logging`
   - 统一输出结构化日志
   - 生成每条消息的关联 ID

## 4. 关键设计决策

### 4.1 单进程单机器人

设计不在一个进程内承载多个机器人实例。这样做有三个直接好处：

- 每个进程只有一套飞书凭据和一个长连接，故障域更清晰
- 日志、数据库、媒体目录都天然按实例隔离
- 简化 Codex 连接和 thread 映射，不需要再引入 bot-instance 多路复用层

### 4.2 SQLite 作为会话与幂等存储

需求要求群聊 thread 跨重启不丢失，单聊在 1 小时有效期内跨重启可恢复，因此不能只用内存。

首版选型为 SQLite，原因：

- 单机场景足够稳定，不需要额外部署数据库
- 支持事务，适合做幂等记录和 session 更新
- 便于调试，必要时可直接查看数据
- 对本项目的吞吐量足够

开启 WAL 模式，并将数据库文件放在配置化的数据目录下。

### 4.3 每个会话串行执行

同一个单聊用户或群聊 chat 在任意时刻只允许一个活动 turn。实现方式是为每个会话 key 建立一个串行执行队列或 `asyncio.Lock`。

这样做的目的：

- 避免同一 thread 上并发 turn 造成上下文错乱
- 避免两条消息同时争用同一条飞书流式回复消息
- 简化 `/clear`、审批请求和表情状态管理

不同会话之间仍可并行处理。

### 4.4 飞书图片下载后使用 `localImage`

本地 schema 已明确 `turn/start.input` 支持 `localImage` 类型，因此实现不需要将本地图片路径再包装为普通文本。

设计规定：

- 飞书收到图片后先下载到本地媒体目录
- 转成 `{"type":"localImage","path":"..."}` 输入项提交给 Codex
- 多图和图文混排按原始顺序构造 `UserInput[]`

这比“只把本地路径当文本传给 Codex”更贴近协议原生能力。

### 4.5 `/clear` 的桥接语义

需求中同时存在两点：

- `/clear` 是受支持的 slash 命令，需透传给 Codex
- 群聊或单聊在用户主动 `/clear` 后要切换到新的 thread

为同时满足这两点，本设计采用以下解释：

- 桥接层把 `/clear` 视为“本地 thread 轮换触发器 + 透传命令”
- 收到 `/clear` 后先创建新 thread 并更新 session 映射
- 然后将原始 `/clear` 命令发送到新的 thread

这样做的结果是：后续对话不会继承旧上下文，同时 Codex 也能看到用户确实输入了 `/clear`。

### 4.6 结构化日志默认开启

日志默认输出 JSON Lines 到 stdout，并可选输出到滚动文件。原因：

- 便于 grep、采集和问题排查
- 能稳定携带结构化字段
- 对长连接重连、流式输出、审批请求这类问题更容易回放

### 4.7 白名单优先于会话和幂等链路

安全边界要求高于会话管理，因此消息处理顺序固定为：

1. 标准化飞书事件
2. 提取发送者用户 ID
3. 白名单校验
4. 对非白名单消息直接拦截并向 owner 告警
5. 仅对白名单消息继续进入幂等、session 路由和 Codex 调用链路

这样做的目的：

- 未授权消息不会污染幂等表和会话状态
- 未授权消息不会创建任何 thread
- owner 能尽早收到安全告警

## 5. 数据模型

## 5.1 `sessions`

用于保存单聊和群聊的会话映射。

建议字段：

- `id`
- `scope_type`
  - `dm`
  - `group`
- `scope_key`
  - 单聊：`{bot_app_id}:{user_open_id}`
  - 群聊：`{bot_app_id}:{chat_id}`
- `bot_app_id`
- `user_open_id`
- `chat_id`
- `thread_id`
- `thread_generation`
- `last_message_at`
- `expires_at`
  - 群聊为空
  - 单聊为最近活跃时间 + 1h
- `status`
  - `active`
  - `archived`
- `created_at`
- `updated_at`

规则：

- 单聊按 `scope_key = bot + user` 路由
- 群聊按 `scope_key = bot + chat` 路由
- 群聊被移出再拉入时，创建新 session 或增加 `thread_generation`

## 5.2 `processed_messages`

用于消息幂等。

建议字段：

- `id`
- `bot_app_id`
- `feishu_event_id`
- `feishu_message_id`
- `chat_id`
- `sender_open_id`
- `session_scope_key`
- `turn_id`
- `status`
  - `accepted`
  - `completed`
  - `ignored_duplicate`
  - `failed`
- `created_at`

唯一索引优先级：

- `(bot_app_id, feishu_message_id)`
- 如事件类型允许，再补 `(bot_app_id, feishu_event_id)`

说明：

- 只有通过白名单校验的消息才写入该表

## 5.3 `reply_messages`

用于将“某条用户输入”映射到“飞书中的那一条流式回复消息”。

建议字段：

- `id`
- `bot_app_id`
- `feishu_message_id`
  - 用户原消息
- `reply_message_id`
  - 机器人用于流式更新的飞书消息 ID
- `thread_id`
- `turn_id`
- `agent_item_id`
- `status`
  - `streaming`
  - `completed`
  - `failed`
- `reaction_applied`
- `created_at`
- `updated_at`

## 5.4 `pending_actions`

用于保存 Codex 反向发起的审批或用户输入请求，便于跨重启恢复。

建议字段：

- `id`
- `request_id`
  - Codex JSON-RPC request id
- `action_type`
  - `command_approval`
  - `file_approval`
  - `permission_approval`
  - `request_user_input`
- `thread_id`
- `turn_id`
- `item_id`
- `session_scope_key`
- `feishu_message_id`
  - 飞书中承载审批卡片或交互消息的消息 ID
- `payload_json`
- `status`
  - `pending`
  - `approved`
  - `denied`
  - `answered`
  - `expired`
- `created_at`
- `updated_at`

## 5.5 `media_assets`

用于保存下载和上传相关的本地文件信息。

建议字段：

- `id`
- `bot_app_id`
- `source_type`
  - `feishu_input_image`
  - `codex_output_file`
  - `codex_output_image`
- `source_message_id`
- `local_path`
- `mime_type`
- `sha256`
- `size_bytes`
- `created_at`
- `expires_at`

## 5.6 `security_alerts`

用于记录非白名单访问尝试及 owner 告警发送结果。

建议字段：

- `id`
- `bot_app_id`
- `sender_open_id`
- `chat_id`
- `chat_type`
- `feishu_message_id`
- `feishu_event_id`
- `owner_open_id`
- `owner_alert_message_id`
- `status`
  - `blocked`
  - `alert_sent`
  - `alert_failed`
- `created_at`
- `updated_at`

## 6. 内部消息模型

为隔离飞书事件格式和 Codex 协议格式，桥接层定义统一的内部消息模型。

### 6.1 入站消息

```text
InboundMessage
- bot_app_id
- event_id
- message_id
- chat_id
- chat_type (p2p/group)
- sender_open_id
- sender_user_id
- is_mention_bot
- is_slash_command
- slash_command
- text_segments[]
- image_segments[]
- raw_text
- received_at
```

其中：

- `text_segments[]` 保留文本片段顺序
- `image_segments[]` 引用已下载的本地文件路径
- 图文混排统一在 `segments[]` 级别保序后再转 `UserInput[]`

### 6.2 出站渲染事件

```text
OutboundRenderEvent
- kind
  - create_reply
  - append_delta
  - finalize_reply
  - send_image
  - send_file
  - send_approval
  - send_error
- session_scope_key
- thread_id
- turn_id
- payload
```

## 7. 关键流程设计

### 7.1 单聊普通消息

1. 飞书长连接收到单聊消息事件
2. 事件标准化为 `InboundMessage`
3. 执行白名单校验
4. 若非白名单，则向 owner 发送告警并结束流程
5. 执行幂等检查
6. 根据 `bot_app_id + sender_open_id` 查找 session
7. 若 session 不存在或已过期，则创建新 thread 并更新 session
8. 下载图片并构造 `UserInput[]`
9. 给原消息加“敲键盘”表情
10. 创建一条新的飞书回复消息，作为该次 turn 的流式承载消息
11. 调用 `turn/start`
12. 消费 Codex 增量通知并持续更新该条飞书回复消息
13. 收到完成事件后移除“敲键盘”表情，完成收尾

### 7.2 群聊普通消息

1. 飞书长连接收到群聊消息事件
2. 仅当消息 `@机器人` 时进入处理
3. 执行白名单校验
4. 若非白名单，则向 owner 发送告警并结束流程
5. 根据 `bot_app_id + chat_id` 查找群 session
6. 若 session 不存在：
   - 优先视为首次入群或历史状态丢失，立即创建 thread
   - 记录告警日志，说明缺少预期的入群初始化状态
7. 群消息永不过期，直接复用 `thread_id`
8. 后续与单聊相同

### 7.3 群聊入群与移出再拉入

1. 收到“机器人加入群聊”事件时创建群 session 和新 thread
2. 收到“机器人移出群聊”事件时将该群 session 标记为 `archived`
3. 若后续再次收到同群的加入事件，则创建新的 thread，并增加 `thread_generation`

### 7.4 Slash 命令

命令识别规则：

- 单聊：消息去除前后空白后，以 `/` 开头即为 slash
- 群聊：去掉 `@机器人` 的 mention 文本后，若正文以 `/` 开头则为 slash

处理规则：

- 受支持命令：`/clear`、`/model`、`/compact`、`/init`
- 其他命令：直接在飞书回复错误消息，不进入 Codex

`/clear` 特殊流程：

1. 在本地先轮换到新 thread
2. 再把 `/clear` 作为普通文本命令透传到该新 thread
3. 该 turn 的回复仍走流式更新

### 7.5 图片与图文混排

飞书入站消息先被拆成顺序化的 segment 列表：

- `text`
- `image`

再按顺序映射为 Codex `UserInput[]`：

- 文本段 -> `{"type":"text","text":"..."}`
- 图片段 -> `{"type":"localImage","path":"..."}`

这样既满足“图片先下载到本地”的要求，也能保留多图和图文混排的顺序语义。

### 7.6 流式回复

对于每条用户消息，只创建一条承载该 turn 输出的飞书回复消息。

具体策略：

1. 首个可见 delta 到达前，先创建占位回复消息，例如空白或“正在思考…”
2. 收到 `item/agentMessage/delta` 时累积文本缓冲
3. 通过节流器按固定频率更新同一条飞书消息
   - 建议初始值：200ms 到 500ms 一次
4. 收到 `item/completed` 或 `turn/completed` 后做最终收尾更新

节流是必要的，否则高频 delta 可能造成飞书限流或消息更新过于频繁。

### 7.6A 白名单拦截与 owner 告警

访问控制规则：

- 白名单以环境变量配置，值为飞书用户 ID 列表
- owner 用户 ID 也通过环境变量配置
- 所有入站消息在进入幂等和会话路由前先做白名单校验

拦截流程：

1. 提取发送者用户 ID
2. 判断是否在允许列表中
3. 若不在白名单：
   - 记录 `security.blocked_message`
   - 不写入 `processed_messages`
   - 不创建或推进 session/thread
   - 向 owner 发送一条单聊告警消息
   - 写入 `security_alerts`
   - 结束处理

告警消息建议包含：

- 机器人实例标识
- 触发时间
- 发送者用户 ID
- 会话类型
- 群聊 ID 或 `p2p`
- 飞书消息 ID
- 是否 `@机器人`
- 原始文本摘要

为避免日志或告警泄露敏感内容：

- 告警中的文本摘要应限长
- 图片内容不直接展开，只提示“包含 N 张图片”

### 7.7 审批与请求用户输入

Codex 可能通过 JSON-RPC 反向请求用户批准或补充信息。

桥接层处理方式：

- `item/commandExecution/requestApproval`
  - 发送飞书审批/交互消息，展示命令、cwd、原因、风险
  - 用户点击“批准/拒绝”后回传 JSON-RPC 响应

- `item/fileChange/requestApproval`
  - 展示文件变更说明
  - 用户批准/拒绝后回传 JSON-RPC 响应

- `item/permissions/requestApproval`
  - 展示新增文件系统或网络权限范围
  - 用户批准/拒绝后回传 JSON-RPC 响应

- `item/tool/requestUserInput`
  - 若是枚举选项，则用飞书卡片单选/多选组件承载
  - 若是普通文本输入，则发送卡片或提示用户回复指定格式
  - 若字段标记为 `isSecret` 且来源是群聊，则引导用户转到私聊完成输入，避免泄露

所有未完成交互都写入 `pending_actions`，支持跨重启恢复。

### 7.8 图片、文件、审批类输出

文本流式 delta 主要由 `item/agentMessage/delta` 驱动；而结构化输出则由 `item/completed`、`turn/completed`、`raw response item completed` 等通知共同驱动。

设计上引入 `CodexOutputClassifier`：

- 输入：Codex thread item / response item
- 输出：统一的内部输出事件

支持的输出类型：

- `text`
- `image`
- `file`
- `approval_request`
- `tool_user_input_request`
- `unsupported`

处理策略：

- `image`：上传到飞书后发送图片消息
- `file`：若拿到本地文件路径或文件内容引用，则上传到飞书并发送文件消息
- `approval_request`：转为飞书审批/交互消息
- `unsupported`：降级为可读文本说明，并记告警日志

## 8. Codex JSON-RPC 客户端设计

`CodexRpcClient` 负责维护单条持久全双工连接，并暴露三个层级接口：

### 8.1 请求接口

- `initialize()`
- `thread_start()`
- `thread_resume()`
- `turn_start()`
- `server_request_respond()`

### 8.2 通知分发接口

- 按 `method` 分发到具体 handler
- 再按 `threadId` / `turnId` / `itemId` 路由到等待中的会话执行器

### 8.3 故障恢复

- 连接断开后自动重连
- 重连后重新 `initialize`
- 已持久化的 session/thread 不丢失
- 对于断连期间中断的 in-flight turn：
  - 将 reply 标记为失败
  - 清理表情
  - 在飞书中补一条错误提示

首版不尝试恢复已经开始但未完成的 turn 流式过程，只恢复后续消息处理能力。

## 9. 飞书适配层设计

`FeishuAdapter` 提供四类接口：

### 9.1 事件输入

- `run_long_connection()`
- `handle_message_event()`
- `handle_bot_added_event()`
- `handle_bot_removed_event()`

### 9.2 消息输出

- `send_text_message()`
- `update_text_message()`
- `send_image_message()`
- `send_file_message()`
- `send_error_message()`
- `send_owner_alert_message()`

### 9.3 表情与状态

- `add_reaction()`
- `remove_reaction()`

### 9.4 交互与审批

- `send_approval_message()`
- `update_approval_message()`
- `parse_interaction_callback()`

交互消息统一带上内部关联字段：

- `session_scope_key`
- `thread_id`
- `turn_id`
- `request_id`
- `item_id`

## 10. 日志设计

日志字段建议最少包含：

- `ts`
- `level`
- `event`
- `bot_app_id`
- `session_scope_key`
- `chat_id`
- `user_open_id`
- `feishu_message_id`
- `feishu_event_id`
- `thread_id`
- `turn_id`
- `item_id`
- `status`
- `duration_ms`
- `error_code`
- `access_decision`

关键日志事件建议：

- `feishu.connection.started`
- `feishu.connection.disconnected`
- `feishu.event.received`
- `security.blocked_message`
- `security.owner_alert.sent`
- `security.owner_alert.failed`
- `message.dedup.accepted`
- `message.dedup.duplicate`
- `session.loaded`
- `session.thread.created`
- `session.thread.rotated`
- `media.download.started`
- `media.download.completed`
- `codex.request.sent`
- `codex.notification.received`
- `reply.message.created`
- `reply.message.updated`
- `approval.request.sent`
- `approval.response.received`
- `turn.completed`
- `turn.failed`

敏感信息处理规则：

- 不记录 app secret、tenant access token、user access token
- 不记录图片二进制
- 不完整记录大文本内容，默认截断到安全长度

## 11. 目录结构建议

```text
src/feishu_codex_bot/
  __init__.py
  app.py
  config.py
  bootstrap.py
  logging.py
  models/
    inbound.py
    session.py
    actions.py
  persistence/
    db.py
    session_repo.py
    dedupe_repo.py
    reply_repo.py
    action_repo.py
    media_repo.py
    security_repo.py
  services/
    conversation_service.py
    media_service.py
    reply_service.py
    approval_service.py
    security_service.py
  adapters/
    feishu_adapter.py
    codex_client.py
    codex_output_classifier.py
  workers/
    session_executor.py
```

数据与临时目录建议：

```text
var/
  app.db
  media/
    inbound/
    outbound/
  logs/
```

## 12. 风险与缓解

### 12.1 飞书消息更新频率限制

风险：

- Codex delta 过快，导致飞书更新 API 限流

缓解：

- 引入节流更新器
- 失败时退避重试
- 必要时降级为较低频率更新

### 12.2 结构化输出类型不稳定

风险：

- Codex 的非文本输出可能分布在不同 item 类型或通知中

缓解：

- 通过 `CodexOutputClassifier` 统一吸收差异
- 解析器严格以 schema 为依据实现
- 对未知类型统一降级到文本说明

### 12.3 群聊事件缺失

风险：

- 入群事件可能因配置问题漏收

缓解：

- 群消息路径支持懒创建 thread 作为兜底
- 同时打印高优先级告警，提示配置异常

### 12.4 本地文件堆积

风险：

- 图片下载和文件输出可能累积大量临时文件

缓解：

- 媒体表记录 `expires_at`
- 后台定期清理过期文件

## 13. 设计结论

本设计的核心思想是：

- 以 Feishu 长连接作为事件入口
- 以白名单访问控制作为所有消息处理的第一道安全闸门
- 以 Codex thread/turn 模型作为唯一会话语义来源
- 以 SQLite 保证 session、去重和审批状态的可靠恢复
- 以单会话串行执行保证上下文一致性
- 以统一的输出分类器完成文本、图片、文件、审批请求向飞书的映射

在该设计下，后续任务拆分可以自然围绕以下主题展开：

- 配置与进程启动
- SQLite 持久化层
- Feishu 长连接与消息 API 适配
- Codex JSON-RPC 客户端
- 单聊/群聊/`/clear` 会话路由
- 流式回复渲染
- 图片、文件、审批消息映射
- 结构化日志与清理任务
