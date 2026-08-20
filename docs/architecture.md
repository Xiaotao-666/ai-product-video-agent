# Web / Core 架构与版本契约

本文记录当前 Agent V1 的稳定架构边界。它描述已经实现的行为，不定义新的工作流，也不替代 Core 模型与自动化测试。

## 1. 总体流程

```text
React / Vite Frontend
        ↓ public HTTP DTO
FastAPI Router（协议、依赖注入、错误映射）
        ↓
Web Service（前置校验、Project Lock、Durable Task 编排）
        ↓
Shared Core Callable（CLI 与 Web 共用的业务语义）
        ↓
Canonical Project State / Prompt & Video Bundles
        ↓
Provider Adapter（仅由需要外部调用的 Core 流程触发）
```

产品工作流为：

```text
Creative
  ↓ 人工审核
Storyboard
  ↓ 人工审核
Video Prompt
  ↓ 人工审核
Shot Generation
  ↓ 逐版本人工审核
Version Management
  ↓
Assembly
```

Web 不是第二套 Core。Router 不直接修改 canonical 业务数据；Web Service 负责 Web 边界的安全校验和任务编排，最终复用 Core callable。CLI 和 Web 可以有不同的交互方式，但版本创建、审核、状态转换和 Bundle 持久化必须由同一 Core 语义完成。

## 2. 目录与职责

- `frontend/src/api/`：公开 API 类型、运行时响应校验和安全错误读取。
- `frontend/src/hooks/`：跨页面复用的客户端会话能力，例如 durable task 恢复与轮询。
- `frontend/src/components/`、`frontend/src/pages/`：展示、用户确认和路由级数据装配；不定义 Core 状态机。
- `web_backend/routers/`：HTTP 状态、请求/响应模型、依赖注入、Correlation ID 和安全错误映射。
- `web_backend/services/`：capability preflight、double validation、per-project lock、durable task submit，以及 Core callable 适配。
- `web_backend/models/`：public-safe DTO；不得暴露绝对路径、凭据、Provider locator 或内部文件系统细节。
- `web_backend/repositories/`：Web runtime task/draft 以及 Core canonical 数据的受控读取入口。
- 仓库根目录的 Core 模块：项目状态机、Prompt/Video 版本、审核、Provider 工作流和 canonical persistence 的唯一业务来源。

## 3. Canonical Source 规则

1. `project.json`、Storyboard、Video Prompt、Shot Bundle 等项目内资源是业务事实来源。
2. `.web_runtime/tasks` 是 Durable Web Task 记录，不代替项目业务状态。
3. 前端本地状态只描述当前交互会话，不得覆盖 durable task 或 canonical bundle 的结论。
4. Task 成功后的页面刷新必须重新读取 Project / Shot / Bundle；F5 后也应从 durable 状态恢复。
5. Provider task locator、下载 locator 等恢复信息只保留在内部持久化中，不进入公开 Task DTO。

## 4. Prompt Version 模型

Prompt Version 是不可覆盖的版本资源，核心语义包括：

- `version`：Shot 内单调递增的版本号。
- `source`：来源，当前为 `ai_generated`、`manual_edit` 或 `ai_revision`。
- `parent_version`：修订所基于的 Prompt；初始版本可为空。
- revision metadata：反馈、差异或修订上下文；只记录业务需要且安全的元数据。

采用 AI Draft 或确认手动编辑时创建新 Prompt Version，旧版本不变。AI Draft 本身不是正式 Prompt Version；只有 Adopt 才把 Draft 转为正式版本。创建 Prompt Version 不等于生成视频，也不自动改变已审核 Video。

## 5. Video Version 模型

每个 Video Version 对应一个不可覆盖的 Shot Bundle，至少包含生成时的 Prompt snapshot、generation snapshot、safety、review 和视频产物。关系约束为：

- 一个 Prompt Version 可以用于多个 Video Version。
- 一个 Video Version 只能绑定一个 Prompt Version。
- `prompt.json` 与 `generation.json` 必须指向同一个 Prompt Version。
- 生成待审核版本时，既有正式版本保持不变；审核通过后，正式 Video 与正式 Prompt pointer 同步切换。
- `generation_count` 只在真正分配新 Video Version 的 Core 流程中增加。

### Version Role 与 Review Status

两类状态必须分开显示：

| 维度 | 值 | 含义 |
| --- | --- | --- |
| Version Role | `OFFICIAL` | 当前正式版本 |
| Version Role | `PENDING_REVIEW` | 当前待审核新版本 |
| Version Role | `HISTORY` | 保留的历史版本 |
| Review Status | `APPROVED` | 该版本曾被审核通过 |
| Review Status | `WAITING_REVIEW` | 等待人工审核 |
| Review Status | `REJECTED` | Core review 状态；历史 UI 还需结合 history reason 解释 |

`HISTORY` 不等于用户拒绝。历史原因应使用 `PREVIOUSLY_APPROVED`、`SUPERSEDED`、`EXPLICITLY_REJECTED` 或 `UNKNOWN` 表达，避免把系统候选替代误写成用户行为。

## 6. Generation Intent

- `INITIAL`：使用当前已审核的初始 Video Prompt 生成 Shot 的首个 Video Version。
- `REGENERATE_CURRENT_PROMPT`：使用当前正式 Prompt 创建新 Video Version，不创建 Prompt Version。
- `REGENERATE_MANUAL_PROMPT`：以当前 Prompt 为基础，确认编辑后先创建新 Prompt Version，再用该新 Prompt 创建新 Video Version。
- `GENERATE_WITH_PROMPT_VERSION`：使用用户明确选择的既有 Prompt Version（当前用于已 Adopt 的 AI Revision Prompt）创建新 Video Version。

AI Revision 的 Draft 流程独立于 Video Generation：

```text
feedback → DeepSeek Draft → local durable Draft → Adopt → Prompt Version
```

Draft 生成不调用 MiniMax，不创建 Video Version。Adopt 是同步本地写入，不创建 Durable Web Task。只有随后显式选择 `GENERATE_WITH_PROMPT_VERSION` 并通过付费确认，才进入视频生成。

## 7. Durable Task Contract

公开 Task Operation 由 Backend `TaskOperation` 与 Frontend `TASK_OPERATIONS` 共同约束，并由跨语言 contract test 防止漂移。Task 生命周期为：

```text
QUEUED → RUNNING → SUCCEEDED
                 ↘ FAILED
                 ↘ INTERRUPTED
                 ↘ CANCELLED（兼容终态）
```

- Active：`QUEUED`、`RUNNING`。
- Terminal：`SUCCEEDED`、`FAILED`、`INTERRUPTED`、`CANCELLED`。
- `FAILED` / `INTERRUPTED` 必须带 public-safe `TaskError`。
- 只有 `SUCCEEDED` 可以带小型 `TaskResultReference`；它只是 canonical 资源指针，不复制业务结果。
- Backend worker 每次只执行一次 callable。文件替换 retry、轮询和页面刷新都不得重新调用业务 callable 或 Provider。

Web Task、Provider Task、Video Bundle 和 Review 是不同层次：

1. Web Task 记录本地执行生命周期。
2. Provider Task 是外部视频服务的内部恢复句柄，不公开。
3. Video Bundle 是生成结果的 canonical 版本资源。
4. Review 决定待审核 Bundle 是否成为正式版本。

HTTP `202` 只表示 Durable Web Task 已被接受；客户端必须根据 Task 与 canonical generation status 协调结果，不能把一次瞬时响应当成最终业务事实。

## 8. Frontend 会话与轮询

`useProjectTaskPolling` 是 Planning 与 Prompt Revision 等普通 Durable Task 的共享入口，负责恢复活动 Task、轮询终态、终态后刷新和安全错误状态。

Shot 付费生成目前保留专用会话协调：它要同时读取 Web Task 与 durable Shot/Bundle generation status，并处理 submission unknown、resume 和结果 reconciliation。它与通用 Hook 共用 Task status contract 和 active-status 判断，但本阶段不强行合并。未来若提取 `useShotGenerationSession`，必须保持 Durable Bundle 优先、Provider 不重复提交和 F5 恢复语义。

页面展示错误时只使用安全 code、message 和 Correlation ID。页面不得从未知响应中显示路径、凭据或 Provider 原始响应。

## 9. Error Taxonomy

当前 API code 保持向后兼容；以下是维护分类，不是一次重命名计划：

| 分类 | 典型含义 / 现有代码示例 |
| --- | --- |
| Validation | 输入或公开 DTO 无效，如 `INVALID_INPUT`、`INVALID_RESPONSE` |
| State / Conflict | 当前状态不允许操作，如 `ACTION_NOT_ALLOWED`、`PROJECT_BUSY` |
| Stale | 二次校验发现基础状态变化，如 `GENERATION_PREFLIGHT_STALE`、Draft stale |
| Capability | 凭据或能力不可用，如 `CAPABILITY_UNAVAILABLE` |
| Provider | 已进入外部调用但请求失败，如 `PROVIDER_REQUEST_FAILED` |
| Task / Recovery | durable task 或提交结果需要恢复，如 `SUBMISSION_UNKNOWN`、`INTERRUPTED` |
| Revision | AI 修订输出或 Draft 无效，如 `PROMPT_REVISION_OUTPUT_INVALID` |
| Persistence / Internal | canonical 数据损坏或安全写入失败；对外只给稳定安全文案 |

错误转换在 Backend 边界按异常类型完成，不能通过解析 Provider 私密原文决定 public message。Durable task callable 使用共享的 safe failure constructor，保证 `TaskError` 的校验规则一致。

## 10. API DTO 安全契约

公开 DTO 必须满足：

- 字段白名单和 `extra="forbid"`；响应由 Pydantic / 前端 runtime parser 双重验证。
- 不返回绝对路径、内部临时文件、Provider locator、credential env name、API key 或 Provider raw response。
- Task result 只包含安全的 `resource_type`、`resource_id` 和可选版本号。
- 错误只包含稳定 code、安全 message、retryable 和 Correlation ID。
- 新增 Task status / operation 时必须同步 Backend enum、OpenAPI、Frontend runtime contract 与测试。

## 11. 当前架构审计与延后项

本阶段确认 Web 调用链没有绕过 Core，但存在以下维护机会：

- `PlanningActionService` 文件同时承载 Creative、Storyboard 和 Video Prompt，规模较大。未来可按 domain 拆分 adapter，但应保持共享前置校验与 Core callable，不在稳定性阶段搬目录。
- `ShotDetailPage`、`ProjectStagePage` 同时装配多个 API 与子组件。未来可提取 read-model hooks，避免页面继续增长；当前不改加载顺序和交互行为。
- Shot generation 的轮询和普通 Task polling 有结构相似处，但前者还承担 durable generation reconciliation，不应仅为去重而合并。
- 部分前端安全错误文案仍分布在业务组件。未来可按 domain 建立 mapper，同时保留现有 code/message 行为。
- Router 的 `202` response example helper 有少量重复，可在不改变 OpenAPI 的前提下集中维护。

## 12. 测试覆盖审计

当前自动化已经覆盖：

- Prompt：AI Draft、legacy fingerprint 兼容、Draft Adopt、parent/source metadata、手动与 AI Prompt Version。
- Video：Initial Generate、Current Prompt Regenerate、Manual Prompt Regenerate、指定 Prompt Version Generate、Approve。
- Task：operation/status contract、F5 恢复相关读取、Project Busy、失败/中断、paid response reconciliation、Windows atomic persistence。
- Version：历史角色/原因、Prompt binding、Set Official、正式 pointer 保护、generation count。

仍需人工或后续专项覆盖的边界：

- 真实 Provider 的额度、超时与长时间中断只能通过受控付费 Smoke 验证；自动化必须使用 mock，禁止网络调用。
- 浏览器媒体播放、响应式布局、可访问性和长文本视觉效果仍需真实浏览器验收。
- 进程在极窄时间窗内崩溃、系统强制重启及 Provider 极端状态组合可继续做故障注入测试。
- Assembly 及后续 PostProduction 的全链路不属于本阶段；不得用架构整理顺带触发。

新增测试应保护真实契约或恢复语义，不以测试数量为目标。

## 13. 变更原则

架构稳定阶段只做可验证的小步改动：不批量改名、不移动大目录、不升级 Core Schema、不改变状态机、不改变付费调用边界。任何未来抽象都必须先证明不会改变 canonical source、版本不可变性、per-project lock、Durable Task exactly-once callable 和公开 DTO 安全。
