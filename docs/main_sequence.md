# 主流程时序图

本文只描述主流程，不包含异常处理、重试、审批分支和补充输入分支。

## 时序图

```mermaid
sequenceDiagram
    autonumber
    participant FS as Feishu Server
    participant BOT as Bot
    participant CODEX as Codex app-server

    Note over BOT,CODEX: 启动时建立 Codex JSON-RPC WebSocket\n地址: ws://127.0.0.1:9000
    BOT->>CODEX: WebSocket connect
    BOT->>CODEX: JSON-RPC request `initialize`
    CODEX-->>BOT: JSON-RPC response `initialize`

    Note over BOT,FS: 启动飞书长连接客户端\n接收事件: `im.message.receive_v1`
    BOT->>FS: 长连接订阅事件流
    FS-->>BOT: 长连接已建立

    Note over FS,BOT: 用户在飞书里给机器人发消息
    FS-->>BOT: 事件 `im.message.receive_v1`

    BOT->>BOT: normalize_message_event
    BOT->>BOT: 白名单校验 / 去重 / 生成 session_scope_key
    BOT->>BOT: 准备输入 items(text/image)

    alt 首次会话或需要新线程
        BOT->>CODEX: JSON-RPC request `thread/start`
        CODEX-->>BOT: JSON-RPC response `thread/start` -> `thread.id`
    else 复用已有线程
        BOT->>BOT: 从 session_repository 取已有 `thread_id`
    end

    BOT->>CODEX: JSON-RPC request `turn/start`\nparams: `threadId`, `input`, `cwd`
    CODEX-->>BOT: JSON-RPC response `turn/start` -> `turn.id`

    BOT->>BOT: reply_service.start_turn(...)
    BOT->>FS: OpenAPI `im.v1.message_reaction.create`\n给原消息加输入中表情

    Note over CODEX,BOT: Codex 开始流式输出
    loop agentMessage 流式增量
        CODEX-->>BOT: JSON-RPC notification `item/agentMessage/delta`
        BOT->>BOT: 聚合文本 `aggregated_text`

        alt 首次有可见输出
            BOT->>FS: OpenAPI `cardkit.v1.card.create`\n创建 streaming card
            BOT->>FS: OpenAPI `cardkit.v1.card.settings`\n开启 `streaming_mode=true`
            BOT->>FS: OpenAPI `im.v1.message.reply`\n`msg_type=interactive`, content={type:"card", data:{card_id}}
        else 后续增量刷新
            BOT->>FS: OpenAPI `cardkit.v1.card_element.content`\nelement_id=`content`
            BOT->>FS: OpenAPI `cardkit.v1.card_element.content`\nelement_id=`status`
        end
    end

    CODEX-->>BOT: JSON-RPC notification `item/completed`\nitem.type=`agentMessage`
    BOT->>BOT: 用完整 `text` 覆盖/校正最终正文

    CODEX-->>BOT: JSON-RPC notification `turn/completed`
    BOT->>FS: OpenAPI `cardkit.v1.card_element.content`\nelement_id=`content` 最终正文
    BOT->>FS: OpenAPI `cardkit.v1.card_element.content`\nelement_id=`status` -> `已完成`
    BOT->>FS: OpenAPI `cardkit.v1.card.settings`\n关闭 `streaming_mode=false`
    BOT->>FS: OpenAPI `im.v1.message_reaction.delete`\n移除输入中表情
```

## 关键地址与关键字

- Codex app-server 地址: `ws://127.0.0.1:9000`
- Codex JSON-RPC 主方法: `initialize` / `thread/start` / `thread/resume` / `turn/start`
- Codex 主通知: `item/agentMessage/delta` / `item/completed` / `turn/completed`
- 飞书长连接事件: `im.message.receive_v1`
- 飞书主接口关键字: `im.v1.message.reply` / `cardkit.v1.card.create` / `cardkit.v1.card.settings` / `cardkit.v1.card_element.content`

## 相关代码

- 运行时装配与事件分发: `src/feishu_codex_bot/runtime.py`
- 会话与 turn 派发: `src/feishu_codex_bot/services/conversation_service.py`
- Codex JSON-RPC 客户端: `src/feishu_codex_bot/adapters/codex_client.py`
- 飞书长连接与消息发送: `src/feishu_codex_bot/adapters/feishu_adapter.py`
- 流式回复聚合与卡片更新: `src/feishu_codex_bot/services/reply_service.py`
