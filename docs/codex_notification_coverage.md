# Codex Notification 覆盖清单

本文用于对齐 `schemas/codex_app_server_protocol.v2.schemas.json` 中定义的 notification method 与当前 bot 实现的接入状态。

说明：

- 这里只统计 Codex WebSocket JSON-RPC 的 `notification`。
- “传输层接收”指 [`CodexClient`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/codex_client.py) 会统一接收所有带 `method` 的 notification。
- “分类层识别”指 [`CodexOutputClassifier`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/codex_output_classifier.py) 会把该 notification 映射为内部事件模型。
- “业务层消费”指当前主流程里，notification 会进一步驱动飞书回复、审批桥接或日志输出，而不是仅落入 `CodexUnknownEvent`。

## 结论摘要

- schema 主 notification 枚举中显式列出 `47` 个 method。
- 当前实现不是只接入了 `item/agentMessage/delta`。
- 当前实现对所有 notification 都会在传输层接收。
- 当前实现显式分类识别的 schema method 有 `12` 个。
- 当前实现真正进入主回复流程、影响飞书展示的 method 主要有 `5` 个：
  - `item/agentMessage/delta`
  - `item/completed`
  - `turn/completed`
  - `item/commandExecution/terminalInteraction`
  - `item/mcpToolCall/progress`
- 当前代码还额外对 `rawResponseItem/completed` 做了兜底分类，但该 method 没有出现在 schema 主 notification 枚举中，属于“代码已预留、schema 枚举未列出”的特殊项。

## 当前接入分层

### 1. 传输层

[`CodexClient._handle_notification()`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/codex_client.py#L248) 会接收所有 JSON-RPC notification，并统一交给运行时注册的 `*` handler。

对应入口：

- [`ApplicationRuntime._register_codex_handlers()`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/runtime.py#L103)
- [`ApplicationRuntime._handle_codex_notification()`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/runtime.py#L432)

因此，当前不是“只接 `AgentMessageDeltaNotification`”，而是“所有 notification 都收到了，但大部分还没进入明确的分类和业务逻辑”。

### 2. 分类层

当前 [`CodexOutputClassifier`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/codex_output_classifier.py) 显式识别这些 schema method：

- `item/agentMessage/delta`
- `item/plan/delta`
- `item/reasoning/summaryTextDelta`
- `item/reasoning/textDelta`
- `command/exec/outputDelta`
- `item/commandExecution/outputDelta`
- `item/commandExecution/terminalInteraction`
- `item/fileChange/outputDelta`
- `item/mcpToolCall/progress`
- `turn/started`
- `turn/completed`
- `item/completed`

此外，代码还额外处理了：

- `rawResponseItem/completed`

### 3. 业务层

当前主流程里真正被消费的内部事件，主要集中在 [`ReplyService._handle_output_event()`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/services/reply_service.py#L263)：

- `agentMessage` 增量正文 -> 更新飞书卡片正文
- `agentMessage` 完整正文 -> 覆盖最终正文
- `turn/completed` -> 关闭卡片 streaming / 写完成状态

## 覆盖矩阵

状态说明：

- `已分类已消费`：有明确分类，且当前主流程有后续处理。
- `已分类仅日志/旁路`：有明确分类，但当前不驱动主飞书回复。
- `仅传输层接收`：会被 `CodexClient` 收到，但当前没有明确分类逻辑，最终落入 unknown 或被忽略。

| Method | Schema 中定义 | 当前状态 | 说明 |
|---|---|---|---|
| `error` | 是 | 已分类仅日志/旁路 | 已分类为 `CodexTurnErrorEvent`，用于失败回复兜底 |
| `thread/started` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/status/changed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/archived` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/unarchived` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/closed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `skills/changed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/name/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/tokenUsage/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `turn/started` | 是 | 已分类仅日志/旁路 | 分类为 `CodexTurnLifecycleEvent(phase=started)`，当前主回复不依赖它 |
| `hook/started` | 是 | 仅传输层接收 | 当前无专门分类 |
| `turn/completed` | 是 | 已分类已消费 | 驱动回复收尾和卡片完成状态 |
| `hook/completed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `turn/diff/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `turn/plan/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/started` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/autoApprovalReview/started` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/autoApprovalReview/completed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/completed` | 是 | 已分类已消费 | 内部再按 item type 分发 |
| `item/agentMessage/delta` | 是 | 已分类已消费 | 主回复正文的核心增量来源 |
| `item/plan/delta` | 是 | 已分类仅日志/旁路 | 已分类为 `plan` 文本，当前主回复不展示 |
| `command/exec/outputDelta` | 是 | 已分类仅日志/旁路 | 已分类为命令输出，当前主回复不展示 |
| `item/commandExecution/outputDelta` | 是 | 已分类仅日志/旁路 | 已分类为命令输出，当前主回复不展示 |
| `item/commandExecution/terminalInteraction` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/fileChange/outputDelta` | 是 | 已分类仅日志/旁路 | 已分类为文件变更增量，当前主回复不展示 |
| `serverRequest/resolved` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/mcpToolCall/progress` | 是 | 仅传输层接收 | 当前无专门分类 |
| `mcpServer/oauthLogin/completed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `account/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `account/rateLimits/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `app/list/updated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/reasoning/summaryTextDelta` | 是 | 已分类仅日志/旁路 | 已分类为 reasoning summary 文本，当前主回复不展示 |
| `item/reasoning/summaryPartAdded` | 是 | 仅传输层接收 | 当前无专门分类 |
| `item/reasoning/textDelta` | 是 | 已分类仅日志/旁路 | 已分类为 reasoning 文本，当前主回复不展示 |
| `thread/compacted` | 是 | 仅传输层接收 | 当前无专门分类 |
| `model/rerouted` | 是 | 仅传输层接收 | 当前无专门分类 |
| `deprecationNotice` | 是 | 仅传输层接收 | 当前无专门分类 |
| `configWarning` | 是 | 仅传输层接收 | 当前无专门分类 |
| `fuzzyFileSearch/sessionUpdated` | 是 | 仅传输层接收 | 当前无专门分类 |
| `fuzzyFileSearch/sessionCompleted` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/realtime/started` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/realtime/itemAdded` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/realtime/outputAudio/delta` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/realtime/error` | 是 | 仅传输层接收 | 当前无专门分类 |
| `thread/realtime/closed` | 是 | 仅传输层接收 | 当前无专门分类 |
| `windows/worldWritableWarning` | 是 | 仅传输层接收 | 当前无专门分类 |
| `windowsSandbox/setupCompleted` | 是 | 仅传输层接收 | 当前无专门分类 |
| `account/login/completed` | 是 | 仅传输层接收 | 当前无专门分类 |

## `item/completed` 的二级覆盖

`item/completed` 已接入，但它内部按 `item.type` 再分流。当前已识别的 item type 如下：

- `agentMessage`
- `plan`
- `reasoning`
- `commandExecution`
- `fileChange`
- `dynamicToolCall`
- `imageView`
- `imageGeneration`

其中当前主飞书回复真正消费的主要是：

- `agentMessage`

当前只做日志或旁路处理的主要是：

- `dynamicToolCall`
- `plan`
- `reasoning`
- `commandExecution`
- `fileChange`

## 建议接入优先级

建议下一批优先接入这些 notification：

1. 与当前用户可见进度最相关

- `item/started`
- `turn/plan/updated`
- `turn/diff/updated`

2. 与排障和会话状态最相关

- `thread/tokenUsage/updated`
- `model/rerouted`
- `configWarning`
- `error`
- `serverRequest/resolved`

3. 可先只落日志，不急于展示

- `thread/status/changed`
- `thread/name/updated`
- `thread/compacted`
- `account/rateLimits/updated`
- `deprecationNotice`

## 相关代码

- 通知接收入口：[`src/feishu_codex_bot/adapters/codex_client.py`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/codex_client.py)
- 通知分类器：[`src/feishu_codex_bot/adapters/codex_output_classifier.py`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/codex_output_classifier.py)
- 主回复消费：[`src/feishu_codex_bot/services/reply_service.py`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/services/reply_service.py)
- 运行时总入口：[`src/feishu_codex_bot/runtime.py`](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/runtime.py)
