# 飞书权限配置说明

本文档对应当前仓库里已经实现的飞书能力，目标是提供一份“最小可用”的权限清单，便于在飞书开放平台中完成机器人接入。

## 1. 可直接导入的权限 JSON

可直接复制 [feishu_app_permissions.json](./feishu_app_permissions.json) 的内容，在飞书开放平台的“权限管理 -> 批量导入”中导入。

当前给出的 JSON 只包含本项目实际使用到的租户权限：

| 权限范围 | 当前用途 | 代码位置 |
| --- | --- | --- |
| `im:message` | 接收和发送消息的基础权限 | `src/feishu_codex_bot/adapters/feishu_adapter.py` |
| `im:message.p2p_msg:readonly` | 接收机器人单聊消息 | `src/feishu_codex_bot/adapters/feishu_adapter.py:82` |
| `im:message.group_at_msg:readonly` | 接收群聊中 `@` 机器人的消息 | `src/feishu_codex_bot/adapters/feishu_adapter.py:82` |
| `im:message:send_as_bot` | 机器人发送文本、图片、文件、单聊告警和回复消息 | `src/feishu_codex_bot/adapters/feishu_adapter.py:181`, `src/feishu_codex_bot/adapters/feishu_adapter.py:223`, `src/feishu_codex_bot/adapters/feishu_adapter.py:324` |
| `im:message:update` | 将同一条飞书回复消息持续更新为流式输出 | `src/feishu_codex_bot/adapters/feishu_adapter.py:265` |
| `im:message.reactions:write_only` | 给原消息添加并撤销“敲键盘”表情 | `src/feishu_codex_bot/adapters/feishu_adapter.py:287`, `src/feishu_codex_bot/adapters/feishu_adapter.py:310` |
| `im:resource` | 上传/下载图片和文件，支持图片接收、本地落盘、再发送图片/文件 | `src/feishu_codex_bot/services/media_service.py:67`, `src/feishu_codex_bot/services/media_service.py:86`, `src/feishu_codex_bot/services/media_service.py:105`, `src/feishu_codex_bot/services/media_service.py:130` |

## 2. 事件订阅配置

更完整的三类开放平台配置说明见：

- [feishu_open_platform_config.md](./feishu_open_platform_config.md)
- [feishu_event_subscription.md](./feishu_event_subscription.md)
- [feishu_events.json](./feishu_events.json)
- [feishu_callback.json](./feishu_callback.json)

当前实现使用长连接，不使用公网回调地址。请在飞书开放平台“事件订阅”页面配置：

- 接收方式：选择“使用长连接接收事件（WebSocket）”
- 事件一：`im.message.receive_v1`
- 事件二：`im.chat.member.bot.added_v1`

它们分别对应当前代码中的两条主流程：

- 接收消息事件会进入消息编排、slash 命令路由、流式回复和审批文本协议处理：`src/feishu_codex_bot/adapters/feishu_adapter.py:82`, `src/feishu_codex_bot/runtime.py:151`
- 机器人进群事件会初始化群聊 thread：`src/feishu_codex_bot/adapters/feishu_adapter.py:85`, `src/feishu_codex_bot/runtime.py:202`

## 3. 事件相关权限说明

对于 `im.chat.member.bot.added_v1`，飞书开放平台界面中通常会显示为“订阅机器人进、出群事件”这一类权限文案。当前仓库只用到了“机器人进群”事件，但为了让该事件能投递，仍需在事件订阅页面完成该事件的启用。

这里没有把它写进 `feishu_app_permissions.json`，原因是当前官方可直接访问的资料更明确地给出了：

- 权限批量导入 JSON 的 `scopes` 结构
- 事件订阅页面中的事件名与界面文案

但没有在可直接访问的资料中稳定展示出“机器人进、出群事件”对应的可批量导入 scope 标识。因此这一项建议按事件订阅页面提示开启，而不是在 JSON 中硬写一个未经官方页面确认的 scope ID。

## 4. 明确不需要的权限

以下权限当前实现没有使用，因此没有放进导入 JSON：

- `im:message.group_msg`
  - 当前群聊只处理 `@` 机器人的消息，不读取群内全部消息。
- `im:message:readonly`
  - 当前不通过 API 拉取历史消息，入站消息内容直接来自事件体。
- `im:message:recall`
  - 当前没有撤回消息功能。
- `im:message.reactions:read`
  - 当前只创建和删除表情，不读取表情列表。
- 卡片交互、审批卡片回调相关权限
  - 当前审批/补充输入走文本协议 `/approve` 和 `/input`，没有实现交互卡片回调。
- 群成员、群信息查询相关权限
  - 当前不调用群成员列表或群详情接口。

## 5. 推断说明

下面这一条属于“基于官方资料和当前代码的保守推断”：

- `im:message.reactions:write_only`
  - 飞书官网文章给出的批量导入 JSON 示例中包含该权限。
  - 当前代码明确调用了消息表情新增和删除接口。
  - 因此这里将其纳入最小权限集。

## 6. 官方依据

以下链接为本次文档整理所依据的官方资料：

- 飞书 IM 文档入口：<https://open.feishu.cn/document/server-docs/im-v1/introduction>
- 飞书官网文章，给出“权限管理 -> 批量导入”的 JSON 结构、基础权限说明，以及长连接事件订阅示例：<https://www.feishu.cn/content/article/7602519239445974205>
- 飞书官网文章，给出“机器人进群”事件及其界面权限文案“订阅机器人进、出群事件”：<https://www.feishu.cn/content/sj7drxsj>
- 机器人进群事件详细文档入口：<https://open.larkoffice.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/chat-member-bot/events/added>
