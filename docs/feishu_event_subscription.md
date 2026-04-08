# 飞书事件订阅与应用能力配置

本文档补充说明当前项目除权限外，还依赖哪些飞书开放平台配置项。

相关 JSON 文件：

- [feishu_app_permissions.json](./feishu_app_permissions.json)
- [feishu_events.json](./feishu_events.json)
- [feishu_callback.json](./feishu_callback.json)
- [feishu_open_platform_config.md](./feishu_open_platform_config.md)

先说结论：

- `docs/feishu_app_permissions.json` 可用于飞书开放平台“权限管理 -> 批量导入”
- `docs/feishu_events.json` 用于结构化列出当前项目必需事件
- `docs/feishu_callback.json` 用于结构化列出当前项目必需的回调/接收方式配置
- 机器人能力、事件订阅、长连接接收方式，当前我没有在官方资料中确认到可直接批量导入的 JSON 入口
- 因此这几项仍应由用户在飞书开放平台页面中手工配置

## 1. 当前项目依赖的开放平台配置项

除了权限，本项目还依赖以下配置：

1. 添加机器人能力
2. 事件订阅接收方式改为长连接（WebSocket）
3. 订阅消息接收事件 `im.message.receive_v1`
4. 订阅机器人进群事件 `im.chat.member.bot.added_v1`
5. 完成版本发布，使新配置生效

## 2. 机器人能力

当前实现会主动发送文本、图片、文件、告警消息和回复消息，因此必须启用机器人能力。

对应代码：

- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L181)
- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L223)
- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L324)

开放平台配置路径：

- `添加应用能力 -> 机器人 -> 添加`

## 3. 事件订阅接收方式

当前项目明确使用长连接方式接收飞书事件，不依赖公网回调地址。

对应代码：

- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L71)
- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L95)

开放平台配置路径：

- `事件订阅 -> 配置事件订阅方式`

应选择：

- `使用长连接接收事件（WebSocket）`

不需要配置：

- 公网 callback URL
- 加签校验回调地址

## 4. 必需事件列表

### 4.1 `im.message.receive_v1`

用途：

- 接收单聊消息
- 接收群聊中 `@` 机器人的消息
- 进入 slash 命令路由、会话编排、流式回复和审批文本协议处理

对应代码：

- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L82)
- [runtime.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/runtime.py#L151)

### 4.2 `im.chat.member.bot.added_v1`

用途：

- 机器人被拉入群时初始化该群对应的持久 thread

对应代码：

- [feishu_adapter.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/adapters/feishu_adapter.py#L85)
- [runtime.py](/home/hjy/code/codex-feishu-bot/src/feishu_codex_bot/runtime.py#L202)

飞书官网文章对这个事件给出的权限界面文案是：

- `订阅机器人进、出群事件`

## 5. 推荐配置步骤

在飞书开放平台中按以下顺序完成：

1. 创建企业自建应用
2. 添加机器人能力
3. 在“权限管理”中导入 [feishu_app_permissions.json](./feishu_app_permissions.json)
4. 在“事件订阅”中选择“使用长连接接收事件（WebSocket）”
5. 添加事件 `im.message.receive_v1`
6. 添加事件 `im.chat.member.bot.added_v1`
7. 创建版本并发布

## 6. 为什么提供了事件 JSON 和回调 JSON，但仍要求手工配置

这是基于官方资料后的保守结论：

- 我能明确找到“权限管理 -> 批量导入”的官方 JSON 示例
- 我能明确找到“事件订阅页面中选择长连接并手工添加事件”的官方说明
- 但没有找到飞书官方公开说明，证明事件订阅、机器人能力或长连接接收方式支持通过同类 JSON 直接导入

因此：

- `feishu_events.json` 和 `feishu_callback.json` 的作用是把“当前项目实际用到了什么配置”结构化列清楚
- 它们便于人工核对、未来脚本化处理或后续适配 manifest
- 但当前不应声称它们一定能直接导入飞书开放平台

## 7. 官方依据

以下资料支撑了上面的结论：

- 飞书 IM 文档入口：<https://open.feishu.cn/document/server-docs/im-v1/introduction>
- 飞书官网文章，展示权限批量导入 JSON，以及事件订阅页面中手工选择“使用长连接接收事件（WebSocket）”并添加 `im.message.receive_v1`：<https://www.feishu.cn/content/article/7602519239445974205>
- 飞书官网文章，展示“机器人进群”事件对应的界面权限文案“订阅机器人进、出群事件”：<https://www.feishu.cn/content/sj7drxsj>
- 机器人进群事件文档入口：<https://open.larkoffice.com/document/uAjLw4CM/ukTMukTMukTM/reference/im-v1/chat-member-bot/events/added>
