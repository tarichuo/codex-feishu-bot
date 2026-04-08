# 飞书开放平台配置总览

当前项目在飞书开放平台中实际依赖三类独立配置：

1. 权限配置
2. 事件配置
3. 回调/接收方式配置

对应 JSON 文件如下：

- [feishu_app_permissions.json](./feishu_app_permissions.json)
- [feishu_events.json](./feishu_events.json)
- [feishu_callback.json](./feishu_callback.json)

## 1. 权限配置

用途：

- 控制机器人是否有能力收发消息、更新消息、添加表情、上传下载图片与文件

对应文件：

- [feishu_app_permissions.json](./feishu_app_permissions.json)

当前结论：

- 这是三类配置里唯一已确认可用于飞书开放平台“权限管理 -> 批量导入”的 JSON

详细说明：

- [feishu_app_permissions.md](./feishu_app_permissions.md)

## 2. 事件配置

用途：

- 决定机器人实际会收到哪些飞书事件

对应文件：

- [feishu_events.json](./feishu_events.json)

当前项目只依赖两个事件：

- `im.message.receive_v1`
- `im.chat.member.bot.added_v1`

当前结论：

- 该文件用于结构化列出必需事件
- 当前未确认飞书开放平台支持通过同类 JSON 直接批量导入事件订阅
- 因此应按文件内容在开放平台页面手工勾选对应事件

## 3. 回调/接收方式配置

用途：

- 决定飞书如何把事件投递给机器人

对应文件：

- [feishu_callback.json](./feishu_callback.json)

当前项目的实际配置是：

- 使用长连接接收事件
- 不使用公网 HTTP callback URL
- 不使用卡片交互回调 URL

当前结论：

- 该文件用于结构化描述当前项目真实依赖的回调/接收方式
- 当前未确认飞书开放平台支持通过 JSON 直接导入这一类配置
- 因此应按文件内容在开放平台页面手工完成设置

## 4. 推荐配置顺序

1. 添加机器人能力
2. 导入 [feishu_app_permissions.json](./feishu_app_permissions.json)
3. 按 [feishu_callback.json](./feishu_callback.json) 配置长连接接收方式
4. 按 [feishu_events.json](./feishu_events.json) 勾选必需事件
5. 创建版本并发布

## 5. 说明

这里把三类配置拆成独立 JSON，是为了让使用者能清楚知道：

- 哪些是当前项目实际使用到的
- 哪些是可以直接导入的
- 哪些虽然需要配置，但当前只能手工设置
