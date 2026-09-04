# Shot Failure Recovery Hotfix — 最终验收报告

日期：2026-08-27

授权范围：原 Shot Failure Recovery Hotfix，加上用户明确批准的最小 Core Generation-Config 扩展。

结论：现有离线自动化复核无失败或错误，用户已完成单个失败 Shot 的真实 Manual Smoke。自动化与人工验收共同支持最终结论：SHOT FAILURE RECOVERY HOTFIX: PASS。既有 15 项条件跳过保持明确记录，不计为 PASS。

## A–Q. 实现验收

| 项目 | 结果与证据 |
| --- | --- |
| A. Git / Baseline | 正式仓库：<repo-root>。开始时 web-v1 / c1c81d8，工作树 clean。结束时分支、HEAD 未变；HEAD 与本地 origin/web-v1 引用 ahead/behind = 0/0。未 fetch，未声称远端实时状态。 |
| B. Root Cause Preserved | 保留审计结论：2061 是当前账户套餐拒绝所选组合，不等于产品能力不支持。未修改能力表、套餐逻辑或默认路由。 |
| C. Video Readiness Fix | 版本记录不再等于 Video READY。要求现有 Bundle 校验通过、实际 video.mp4 非空且相关文件完整；拒绝越界/符号链接。失败记录无视频显示 FAILED；损坏 Bundle 返回未就绪，不使只读页面报错。Prompt READY 保持。 |
| D. Failure Recovery Classification | 后端安全 DTO 区分 RETRY_ALLOWED、RETRY_BLOCKED_SUBMISSION_UNKNOWN、RESUME_AVAILABLE、BUSINESS_ALREADY_COMPLETE、ACTIVE_TASK、BLOCKED、NOT_APPLICABLE。前端不从错误文字推断资格。 |
| E. Explicit Rejection Retry | 仅持久化明确拒绝、FAILED、无未知提交、无远端 Task/File、无本地视频及无活动 Web Task 才开放新提交。默认沿用失败 Attempt 绑定的 Prompt 与安全快照，不重新调用 DeepSeek。 |
| F. SUBMISSION_UNKNOWN Protection | 未知提交禁止普通 Retry。固定提示可能重复收费，不自动重新提交。 |
| G. Resume Protection | provider_task_id 走已有 Resume/Poll；file_id 走 Download/Finalize；完整视频恢复已有结果；活动任务附着读取。补充 Web 测试验证：重试中断后 Resume 保持 8s / 2K / 同一模型与首帧配置，Submit 总计仍为一次。 |
| H. Failed Retry Preflight | 独立 options / preflight 接口，支持现有模型、时长、分辨率、视觉输入和项目参考素材。只读本地校验，无 Task、无写入、无套餐查询、无 Provider 网络。预检绑定 Prompt、下一版本、恢复记录和所选配置。 |
| I. Retry Confirmation | 前端列出 Prompt Version、Model、Duration、Resolution、Visual Input、下一 Generation Version及费用提示。取消不创建任务。后端严格要求 confirm_external_video_call = true；false、缺失或字符串均拒绝。 |
| J. Durable Task | HTTP 202 + Location；沿用 SHOT_GENERATE，Attempt intent = FAILED_RETRY。Worker 持有项目锁后再次分类、核对 fingerprint；仅排除自己的 Task，变化则 FAILED_RETRY_STALE，Provider = 0。 |
| K. Version / Attempt Preservation | v001 失败 Bundle 和 Generation Record 不变；v002 为新 Attempt，不删除旧历史、不重置 generation_count。测试对旧文件哈希/时间戳及旧 Generation Record 做相等检查。 |
| L. Retry Success Semantics | 成功后新版本 WAITING_REVIEW，真实 Video READY，未自动 APPROVED，正式版本仍须用户审核。 |
| M. Retry Failure Semantics | 再次明确拒绝后保留 v002 FAILED；下一次显示 v003，但必须再次预检、确认。没有自动 Retry 循环或 fallback。 |
| N. Multi-shot UX | FAILED 不加入批量 Initial Generate。显示“生成失败”，提供“查看镜头 / 调整配置后重试”入口。 |
| O. Shot Detail UX | 提供独立 Failed Retry Preparation；失败时明确“未生成可用视频”；历史失败版本标为“生成失败”，新待审核版本与历史分开。 |
| P. 202 Accepted Safety | 复用既有付费 Shot 提交器：Location GET → Project Task fallback → accepted-but-unreadable 锁定。同步防双击；会话恢复屏障跨 F5 保留；旧失败 Task 不会被误识别为新接受请求；状态恢复仅 GET，不重发付费 POST。 |
| Q. Security | 请求禁止 path、provider_task_id、file_id、version、credential 等额外字段。2061 仅返回固定安全提示和 VIDEO_PROVIDER_INVALID_REQUEST。无 raw response、端点、凭证或套餐原文泄露。 |

接口：

- GET /api/projects/{project_id}/shots/{shot_id}/generation/failed-retry/options
- POST /api/projects/{project_id}/shots/{shot_id}/generation/failed-retry/preflight
- POST /api/projects/{project_id}/shots/{shot_id}/generation/failed-retry

现有 Initial、Regenerate 和 Resume 的业务入口未放宽为通用 FAILED 重试；原通用 preflight 明确拒绝 FAILED_RETRY intent。

## R–V. 测试、构建及调用

| 项目 | 最终结果 |
| --- | --- |
| R. Backend Tests | 失败恢复专项 17 PASS。包含实际配置透传、旧历史不变、再次拒绝、Unknown、远端 Task/File、完整视频、活动任务、过期预检、锁内重检、安全输入，以及重试后的 Web Resume。 |
| S. Frontend Tests | 新增 20 项验收覆盖：独立组件/API 17 项，加详情页与多镜头 3 项；全部通过。前端全量 28 个文件、667 PASS。 |
| T. Regression | 本次报告更新前重新运行现有自动化：后端全量 833 项，831 PASS、2 SKIP、0 FAIL、0 ERROR，已包含补充 Web Resume 用例；Core 全量 607 项，594 PASS、13 SKIP、0 FAIL、0 ERROR；前端全量 667 PASS。Core 配置专项 10 项与失败恢复专项 17 项均在本次全量复核范围内。 |
| U. Build | 保留此前 npm run build 成功证据，TypeScript 与 Vite 均通过。存在 >500 KB chunk 提示（主 JS 538.81 KB）。本次仅更新报告，不改代码、不重建产物；git diff --check 再次通过。 |
| V. Automated Calls | MiniMax Real = NO；DeepSeek = NO；TTS = NO；FFmpeg = NO；External Provider Network = 0。测试使用临时项目、Fake/Mock Provider，并在全量 Python 回归进程中屏蔽真实网络及外部进程。 |

跳过项明确说明：

- 后端 2 项为既有符号链接逃逸测试；当前 Windows 无创建符号链接特权（WinError 1314），测试按既有条件跳过。
- Core 13 项是 tests/test_video_assembly.py 中需要真实 FFmpeg/FFprobe 的集成用例。为遵守本轮 FFmpeg = NO，进程级屏蔽工具发现，使既有条件跳过生效；未修改这些测试或 Core。
- 没有把上述 15 项计为 PASS。其余适用离线测试全部通过。
- 全量覆盖 Initial Shot Generate、Preflight、Resume、Regenerate、Multi-shot Foundation/Generation、Task Runner、PostProduction 及其他现有 Core/Web 测试。

执行方式：Python unittest 按 tests/web 和 tests 下的测试模块列表运行；后端运行时加入 tests/web 搜索路径，兼容既有 bare helper import。最终使用内存日志汇总精确结果，未生成真实媒体或操作生产项目。前端使用现有 Vitest 和生产构建脚本，未安装新依赖。

## W. Core Diff Guard

唯一修改的正式 Core 源码文件：

- shot_generation_workflow.py

未修改 Provider submit/poll/download 实现、Project State、Shot Version Schema、Assembly、Voice、Subtitle、Music、Export、FFmpeg Core。

其余实现修改仅在 web_backend、frontend、tests；本文件为验收报告。实现与自动化阶段未操作真实项目、未试探套餐。本次真实单 Shot 恢复由用户自行完成并提供验收结果，详见 X；本轮报告更新未操作或重试任何真实 Shot。

## CORE GENERATION CONFIG EXTENSION

- 只给现有 continue_shot_generation 增加向后兼容的关键字参数 resolution: str = "768P"，没有新建配置框架或重写请求 API。
- 新 Attempt 使用确认的 resolution；时长仍通过既有 StoryboardShot.duration 传递，Web 只复制该对象，不修改原 Storyboard 文件。
- 相同的 duration / resolution 同时进入实际生成请求及现有 on_submitting 持久化回调；Attempt 与 Bundle 保存同一配置。
- Resume 优先读取该 Generation Record 的持久化 duration / resolution，新的调用参数或 UI 默认值不能覆盖已提交配置。
- 缺 resolution 的 Legacy Attempt 按历史 768P 解释，仅做读取兼容，不迁移历史文件。
- model、visual input、reference asset 继续使用原传递和恢复机制。
- Submit → 持久化 Task ID → Poll → 持久化 File → Download → Finalize → Bundle → WAITING_REVIEW 原流程保持不变。

DEFAULT LEGACY BEHAVIOR CHANGED = NO

AUTO FALLBACK ADDED = NO

SUBMISSION_UNKNOWN SEMANTICS CHANGED = NO

## X. User Manual Smoke Acceptance — PASS

证据来源：用户本次提供的真实人工验收结果；不是本轮再次执行的 Provider 测试。范围仅一个此前被 MiniMax 2061 明确拒绝的 FAILED Shot。

| 阶段 | 用户确认的真实结果 |
| --- | --- |
| 原失败状态 | v001 = FAILED；MiniMax-Hailuo-2.3 / 10s / 768P；provider_task_id、file_id、video.mp4 均不存在；SUBMISSION_UNKNOWN = false。 |
| 失败恢复入口 | Web 正常显示恢复入口，不再错误显示 VIDEO 已就绪。 |
| Preflight | 正常通过，Prompt Version 保持原有版本；用户主动改为 MiniMax-Hailuo-2.3 / 6s / 768P；预检阶段 MiniMax 调用 = 0。 |
| 确认与任务 | 用户仅确认一次新的外部视频生成调用；TaskOperation = SHOT_GENERATE；未发生重复 POST。 |
| 真实生成 | MiniMax 新请求成功，创建 v002；v001 FAILED 历史完整保留；generation_count = 2。 |
| 配置与视频 | v002 持久配置与真实请求一致：6s / 768P；video.mp4 正常存在；VIDEO 正确显示 READY。 |
| 审核语义 | v002 = WAITING_REVIEW；未自动 APPROVE；当前正式版本仍遵守原审核语义。 |
| F5 Recovery | v002 仍正常存在；不会再次调用 MiniMax，不会创建重复版本。 |

CORE GENERATION CONFIG EXTENSION: PASS

VIDEO READINESS FIX: PASS

EXPLICIT FAILED RETRY: PASS

SUBMISSION_UNKNOWN PROTECTION: 保持不变

AUTO FALLBACK: NO

DEFAULT LEGACY 768P BEHAVIOR CHANGED: NO

本次成功只证明用户所测试配置在该次真实调用中可用，不推广为所有账户或配置的套餐支持结论。不自动重试其他 Shot。

## Y. git status --short

以下为 Hotfix 累计未暂存修改/新增；暂存区为空，HEAD 不变。本轮仅更新本报告：报告以外的 311 个仓库文件更新前后 SHA-256 一致，没有代码变更。

```text
 M frontend/src/api/client.ts
 M frontend/src/api/types.ts
 M frontend/src/components/shots/MultiShotGenerationPanel.tsx
 M frontend/src/components/shots/ShotsStageContent.test.tsx
 M frontend/src/components/shots/ShotsStageContent.tsx
 M frontend/src/pages/ShotDetailPage.test.tsx
 M frontend/src/pages/ShotDetailPage.tsx
 M shot_generation_workflow.py
 M tests/web/test_backend_phase_1_acceptance.py
 M tests/web/test_backend_phase_4a_multishot_foundation.py
 M web_backend/dependencies.py
 M web_backend/errors.py
 M web_backend/models/generation.py
 M web_backend/models/shots.py
 M web_backend/repositories/shot_repository.py
 M web_backend/routers/projects.py
 M web_backend/routers/shot_generation.py
 M web_backend/services/shot_generation.py
 M web_backend/services/shot_generation_preflight.py
?? docs/shot-failure-recovery-hotfix-report.md
?? frontend/src/api/failedRetry.test.ts
?? frontend/src/components/shots/FailedShotRetryAction.test.tsx
?? frontend/src/components/shots/FailedShotRetryAction.tsx
?? tests/test_shot_generation_config.py
?? tests/web/test_backend_shot_failure_recovery.py
?? web_backend/models/shot_failure_recovery.py
?? web_backend/repositories/shot_bundle_readiness.py
?? web_backend/services/shot_failure_recovery.py
```

未执行 git add / commit / push / reset / restore / checkout / stash / clean。

工作方法采用 karpathy-agentic-engineering 的小步更改、专项测试和差异审查；严格遵守本次授权，不执行通用工作流中的提交步骤。

## Z. Result

SHOT FAILURE RECOVERY HOTFIX: PASS

依据：现有适用离线自动化重新验证通过，用户单 Shot 真实 Manual Smoke 成功；原有条件跳过项已明确保留。

本轮仅更新最终验收报告；未修改代码，未重试其他真实 Shot，未 git add / commit / push。到此停止，不进入新 Phase。
