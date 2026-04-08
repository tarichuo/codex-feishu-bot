# Feishu Codex Bot Tasks

## 1. 任务原则

- 所有任务均以 Python 实现为前提。
- 每个任务应尽量限制在 1 到 3 个文件范围内。
- 每个任务完成后才进入下一个任务。
- 在开始任何编码前，仍需由用户确认要执行的任务范围。

## 2. 任务列表

- [x] 1. 初始化项目骨架与运行入口
  - Files: `pyproject.toml`, `requirements.txt`, `src/feishu_codex_bot/app.py`, `src/feishu_codex_bot/__init__.py`
  - Requirements: FR-1, FR-10, FR-14
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Python application bootstrap engineer.
    Task: 初始化 Python 项目结构、依赖入口、`requirements.txt`、`.venv` 约定和可执行启动模块，为后续模块接入提供统一启动点。
    Restrictions: 不要实现业务逻辑；不要接入飞书或 Codex 网络调用；不要引入与需求无关的框架；`requirements.txt` 只放当前确定需要的第三方库；`.venv` 必须被版本控制忽略。
    _Leverage: `AGENTS.md`, `.spec-workflow/specs/feishu-codex-bot/requirements.md`, `.spec-workflow/specs/feishu-codex-bot/design.md`
    _Requirements: FR-1, FR-10, FR-14
    Success: 可以通过统一入口启动应用骨架；项目结构和依赖声明清晰；仓库包含可用于安装第三方依赖的 `requirements.txt`；仓库根目录采用 `.venv` 作为虚拟环境并被忽略；不包含业务占位之外的多余代码。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 2. 实现环境变量配置模型
  - Files: `src/feishu_codex_bot/config.py`, `src/feishu_codex_bot/bootstrap.py`
  - Requirements: FR-10, FR-14, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Python configuration engineer.
    Task: 定义应用配置对象，覆盖飞书配置、Codex app server 配置、SQLite 路径、媒体目录、owner 用户 ID、用户白名单和日志配置，并在启动阶段完成加载与校验。
    Restrictions: 不要实现数据库和网络逻辑；不要把默认密钥写死到代码；不要忽略白名单和 owner 配置校验。
    _Leverage: `src/feishu_codex_bot/app.py`, `AGENTS.md`
    _Requirements: FR-10, FR-14, FR-15
    Success: 配置可以从环境变量稳定加载；缺失关键项时能明确报错；白名单可被解析成结构化集合。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 3. 建立结构化日志基础设施
  - Files: `src/feishu_codex_bot/logging.py`, `src/feishu_codex_bot/bootstrap.py`
  - Requirements: FR-14, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Observability engineer.
    Task: 实现 JSON Lines 风格日志初始化、上下文字段注入和敏感信息脱敏策略，并在应用启动时接入。
    Restrictions: 不要接入第三方日志平台；不要记录 token、secret 或图片二进制；不要把日志实现耦合到飞书 SDK。
    _Leverage: `src/feishu_codex_bot/config.py`, `.spec-workflow/specs/feishu-codex-bot/design.md`
    _Requirements: FR-14, FR-15
    Success: 日志初始化独立可复用；支持输出 event、thread_id、message_id 等结构化字段；敏感字段被脱敏。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 4. 实现 SQLite 基础连接与 schema 初始化
  - Files: `src/feishu_codex_bot/persistence/db.py`, `src/feishu_codex_bot/persistence/__init__.py`
  - Requirements: FR-11, FR-13, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: SQLite persistence engineer.
    Task: 创建 SQLite 连接管理、WAL 模式初始化和表结构初始化入口，为 session、去重、回复映射、安全告警等仓储提供基础设施。
    Restrictions: 不要在本任务里实现具体 repository 业务方法；不要引入 ORM；不要跳过事务与唯一索引设计。
    _Leverage: `.spec-workflow/specs/feishu-codex-bot/design.md`
    _Requirements: FR-11, FR-13, FR-15
    Success: 数据库能够自动初始化；关键表和唯一索引就绪；连接生命周期清晰。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 5. 实现会话与去重仓储
  - Files: `src/feishu_codex_bot/persistence/session_repo.py`, `src/feishu_codex_bot/persistence/dedupe_repo.py`, `src/feishu_codex_bot/persistence/reply_repo.py`
  - Requirements: FR-4, FR-5, FR-7, FR-11, FR-13
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Repository engineer.
    Task: 实现 session、消息幂等和流式回复映射的仓储接口，支持单聊过期判断、群聊持久化、重复消息判定和回复消息状态持久化。
    Restrictions: 不要实现飞书或 Codex 适配；不要把业务流程塞进 repository；不要破坏事务边界。
    _Leverage: `src/feishu_codex_bot/persistence/db.py`, `.spec-workflow/specs/feishu-codex-bot/design.md`
    _Requirements: FR-4, FR-5, FR-7, FR-11, FR-13
    Success: 仓储接口覆盖核心读写场景；群聊和单聊 session 行为可区分；幂等键冲突能稳定返回重复结果。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 6. 实现安全告警仓储与白名单校验服务
  - Files: `src/feishu_codex_bot/persistence/security_repo.py`, `src/feishu_codex_bot/services/security_service.py`
  - Requirements: FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Security control engineer.
    Task: 实现白名单判定服务和安全告警持久化，提供“允许继续处理”与“拦截并告警”所需的统一接口。
    Restrictions: 不要直接调用飞书 API；不要跳过 owner 缺失场景处理；不要把日志逻辑硬编码到 repository 中。
    _Leverage: `src/feishu_codex_bot/config.py`, `src/feishu_codex_bot/persistence/db.py`
    _Requirements: FR-15
    Success: 能稳定判断用户是否在白名单中；能记录拦截与告警结果；接口适合上层消息流程直接调用。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 7. 实现飞书消息与媒体适配接口
  - Files: `src/feishu_codex_bot/adapters/feishu_adapter.py`, `src/feishu_codex_bot/services/media_service.py`, `src/feishu_codex_bot/models/inbound.py`
  - Requirements: FR-2, FR-8, FR-9, FR-10, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Feishu integration engineer.
    Task: 接入飞书官方 Python SDK，定义入站消息标准化模型，封装文本消息、消息更新、图片/文件消息、表情回应、owner 告警消息，以及图片下载到本地的能力。
    Restrictions: 不要在本任务中实现完整会话编排；不要把 Codex JSON-RPC 逻辑混入飞书适配层；不要忽略图文混排和多图输入。
    _Leverage: `https://open.feishu.cn/document/server-docs/im-v1/introduction`, `src/feishu_codex_bot/config.py`
    _Requirements: FR-2, FR-8, FR-9, FR-10, FR-15
    Success: 飞书适配层暴露清晰接口；入站消息可标准化成统一模型；图片可以下载到本地并返回路径元数据。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 8. 实现 Codex JSON-RPC 客户端基础能力
  - Files: `src/feishu_codex_bot/adapters/codex_client.py`, `src/feishu_codex_bot/models/actions.py`
  - Requirements: FR-3, FR-12
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: JSON-RPC client engineer.
    Task: 实现连接本机 Codex app server 的持久 JSON-RPC 客户端，支持 initialize、thread/start、turn/start、通知分发和服务端反向请求注册。
    Restrictions: 严格以 `schemas/` 为协议依据；不要在本任务里实现完整业务编排；不要假设不存在反向请求。
    _Leverage: `schemas/ClientRequest.json`, `schemas/ServerNotification.json`, `schemas/ServerRequest.json`
    _Requirements: FR-3, FR-12
    Success: 客户端可以发送请求并接收通知；支持以 threadId/turnId 分发事件；基础协议层与业务层解耦。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 9. 实现 Codex 输出分类器
  - Files: `src/feishu_codex_bot/adapters/codex_output_classifier.py`, `src/feishu_codex_bot/models/actions.py`
  - Requirements: FR-7, FR-9, FR-12
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Output mapping engineer.
    Task: 将 Codex 的 delta、完成项、图片、文件、审批请求和 request_user_input 等协议对象统一分类成内部输出事件，供上层渲染和交互处理。
    Restrictions: 不要直接调用飞书发送逻辑；不要把分类器实现成依赖特定 UI 的代码；不要丢掉未知类型的告警路径。
    _Leverage: `src/feishu_codex_bot/adapters/codex_client.py`, `schemas/v2/*.json`
    _Requirements: FR-7, FR-9, FR-12
    Success: 文本、图片、文件、审批和未知输出均有清晰分类结果；未知类型能安全降级。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 10. 实现会话编排与 slash 命令路由
  - Files: `src/feishu_codex_bot/services/conversation_service.py`, `src/feishu_codex_bot/workers/session_executor.py`, `src/feishu_codex_bot/models/session.py`
  - Requirements: FR-4, FR-5, FR-6, FR-7, FR-9, FR-11, FR-13, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Conversation orchestration engineer.
    Task: 实现单聊/群聊 session 路由、每会话串行执行、slash 命令识别、`/clear` thread 轮换、多图图文混排输入构建、白名单校验接入和幂等校验接入。
    Restrictions: 不要在本任务里实现底层飞书 SDK 细节；不要绕过 repository 和 security service；不要把输出渲染逻辑耦合到核心路由中。
    _Leverage: `src/feishu_codex_bot/persistence/session_repo.py`, `src/feishu_codex_bot/services/security_service.py`, `src/feishu_codex_bot/adapters/codex_client.py`
    _Requirements: FR-4, FR-5, FR-6, FR-7, FR-9, FR-11, FR-13, FR-15
    Success: 单聊和群聊均可正确选择或创建 thread；`/clear`、`/model ...` 等命令路由正确；未授权和重复消息被正确拦截。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 11. 实现流式回复与表情控制
  - Files: `src/feishu_codex_bot/services/reply_service.py`, `src/feishu_codex_bot/workers/session_executor.py`
  - Requirements: FR-7, FR-8, FR-12
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Realtime response engineer.
    Task: 实现一次 turn 对应一条飞书回复消息的创建、节流更新、最终收尾，以及“敲键盘”表情的添加与撤销。
    Restrictions: 不要把 session 路由逻辑复制进来；不要每个 delta 新发一条消息；不要忽略失败和中断场景的表情清理。
    _Leverage: `src/feishu_codex_bot/adapters/feishu_adapter.py`, `src/feishu_codex_bot/persistence/reply_repo.py`
    _Requirements: FR-7, FR-8, FR-12
    Success: 文本流式输出稳定更新到同一条飞书消息；回复结束后表情被撤销；错误时也能收尾。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 12. 实现审批请求与用户输入桥接
  - Files: `src/feishu_codex_bot/services/approval_service.py`, `src/feishu_codex_bot/persistence/action_repo.py`, `src/feishu_codex_bot/adapters/feishu_adapter.py`
  - Requirements: FR-12
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Human-in-the-loop workflow engineer.
    Task: 将 Codex 的 command/file/permission approval 和 request_user_input 反向请求映射成飞书审批或交互消息，并将用户响应回传给 Codex。
    Restrictions: 不要依赖未定义的前端页面；不要丢失 pending action 持久化；不要忽略群聊 secret 输入的风险。
    _Leverage: `schemas/ServerRequest.json`, `src/feishu_codex_bot/adapters/codex_client.py`, `src/feishu_codex_bot/persistence/db.py`
    _Requirements: FR-12
    Success: 审批和补充输入请求可以通过飞书完成闭环；待处理动作可恢复；响应能准确回传到对应 request_id。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 13. 导出飞书权限配置文档
  - Files: `docs/feishu_app_permissions.json`, `docs/feishu_app_permissions.md`
  - Requirements: FR-10
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Platform integration documentation engineer.
    Task: 基于实际实现需要，导出一份可直接复制到飞书开放平台网页端使用的权限配置文档，并提供 Markdown 说明。
    Restrictions: 必须以飞书官方文档和实际代码需求为依据；不要编造不确定的权限名；不要输出与实现无关的权限。
    _Leverage: `https://open.feishu.cn/document/server-docs/im-v1/introduction`, `.spec-workflow/specs/feishu-codex-bot/requirements.md`
    _Requirements: FR-10
    Success: 生成的权限文档可读、可复制；权限项覆盖消息、图片、文件、表情、事件订阅和交互能力。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 15. 集成运行时主流程与事件分发
  - Files: `src/feishu_codex_bot/runtime.py`, `src/feishu_codex_bot/app.py`, `src/feishu_codex_bot/bootstrap.py`
  - Requirements: FR-1, FR-2, FR-3, FR-4, FR-5, FR-7, FR-8, FR-11, FR-12, FR-13, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Runtime integration engineer.
    Task: 将 FeishuAdapter、CodexClient、ConversationService、ReplyService、ApprovalService 和各 repository 组装为可运行的主流程，接入飞书长连接事件处理、Codex 通知分发、server request 延迟响应、入群建 thread、以及审批/补充输入的文本协议回复处理。
    Restrictions: 不要把业务逻辑重复散落到多个入口；不要跳过错误收尾；不要为了接线而破坏现有 service 的职责边界。
    _Leverage: `src/feishu_codex_bot/services/conversation_service.py`, `src/feishu_codex_bot/services/reply_service.py`, `src/feishu_codex_bot/services/approval_service.py`
    _Requirements: FR-1, FR-2, FR-3, FR-4, FR-5, FR-7, FR-8, FR-11, FR-12, FR-13, FR-15
    Success: 应用启动后能够建立飞书长连接和 Codex 连接；普通消息可进入 turn 并触发流式回复；server request 能分发到审批桥接；文本协议可回传审批与补充输入结果；入群事件可初始化群 thread。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。

- [x] 14. 增加基础测试与调试文档
  - Files: `tests/test_conversation_service.py`, `tests/test_security_service.py`, `README.md`
  - Requirements: FR-4, FR-5, FR-6, FR-13, FR-15
  - _Prompt: Implement the task for spec feishu-codex-bot, first run spec-workflow-guide to get the workflow guide then implement the task:
    Role: Test engineer.
    Task: 为会话路由、`/clear`、消息去重和白名单拦截编写基础测试，并补充本地启动与调试说明。
    Restrictions: 不要依赖真实飞书或真实 Codex 网络环境；不要跳过关键边界条件；不要把测试写成无法稳定复现的集成脚本。
    _Leverage: `src/feishu_codex_bot/services/conversation_service.py`, `src/feishu_codex_bot/services/security_service.py`
    _Requirements: FR-4, FR-5, FR-6, FR-13, FR-15
    Success: 核心路由和安全逻辑有可执行测试；README 说明启动方式、配置项和调试要点。
    Instructions: 开始前将本任务标记为 `[-]`；完成后记录实现日志；确认无误后将本任务标记为 `[x]`。
