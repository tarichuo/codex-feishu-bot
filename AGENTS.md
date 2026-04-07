# AGENTS

## 项目约束

- 本仓库用于实现一个通过飞书连接 Codex 的机器人。
- 本仓库中的所有实现必须使用 Python。
- Codex 必须通过 app server 模式接入。
- app server 使用的协议是 JSON-RPC。
- `schemas/` 目录下的协议文件是 app server 接口的唯一事实来源。
- 飞书机器人必须通过长连接方式连接飞书后台。
- 飞书相关实现需要时，优先参考飞书开放平台 IM 文档：`https://open.feishu.cn/document/server-docs/im-v1/introduction`

## 协作约束

- 后续与用户的对话统一使用中文。
- 对于用户提出的任何实现任务，必须先提供方案。
- 在用户确认方案之前，不得开始编码。
- 开始实现后，设计与代码必须始终符合本文件中的约束。
