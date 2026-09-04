# Creative Narration Duration Retry + Best-Effort Fallback Hotfix — Final Acceptance Report

## A. Git/Baseline

- 正式仓库：`D:\desktop\AI项目\codex\视频生成agent`
- 分支：`web-v1`
- 基线 HEAD：`6ffc51e feat: add AI prompt revision workflow`
- 本轮是既有未提交 `Creative Narration Duration Retry Hotfix` 的继续实施；开始时已有该 Hotfix 的两处代码变更、专项测试和本报告，未覆盖其他用户改动。
- 相对本地记录的 `origin/web-v1`：ahead 1 / behind 0；本轮未联网 fetch，不把该记录声明为远端实时状态。
- 未执行 `git add / commit / push / reset / restore / checkout / stash / clean`。

## B. Root Cause

- DeepSeek 返回内容已经能够通过 JSON 解析和 Pydantic Schema 校验。
- `_validate_creative_brief` 随后正确识别 `full_script` 预计朗读时长与 `target_duration_seconds` 明显不一致。
- 旧链路把 `StoryboardError` 包装成通用 `StructuredOutputError`，而该类型又继承 JSON 格式错误；因此业务失败被误记为 JSON 格式失败，重试提示也只要求“修复结构”，没有给模型可执行的旁白扩写或压缩数据。
- 最终耗尽错误因此误导性地写成“连续 3 次返回无效结构化 JSON”。
- 修正错误分类与重试反馈后，真实模型仍可能连续三次只在旁白预计时长上偏离目标；旧行为会直接失败，即使其中存在不超过视频时长、且其余 Creative 业务校验全部合法的可用候选。

## C. Existing Validation Preserved

- 保留现有 `estimate_narration_duration`，没有引入第二套语速模型。
- 保留现有容差：`max(0.75 秒, target_duration_seconds × 25%)`。
- 保留目标不得超过视频总时长、用户明确旁白时长范围、Creative 硬约束及 AV Timeline 校验。
- `target_duration_seconds` 继续表示计划旁白时长，`full_script` 继续表示真实旁白文案；未用估算值覆盖目标。
- 旁白关闭与原本匹配的旁白继续按原语义一次通过。
- 严格校验始终优先；任一轮严格通过即立即返回，不进入 Best-Effort 选择。
- 未修改 Creative Schema，未新增数据库字段或迁移。

## D. Retry Feedback Improvement

当合法目标下的旁白时长不匹配时，重试消息明确包含：

- `current target_duration_seconds`
- `current full_script`
- `current estimated_duration`
- `delta_seconds(estimated-target)`
- `required_direction=EXPAND / COMPRESS`
- “不要只修改 `target_duration_seconds`”
- 保持其他 Creative 字段稳定的约束

## E. Narration Expansion Logic

- 当 `estimated_duration < target_duration_seconds` 且超过既有容差时，反馈明确要求 `EXPAND（扩写 full_script）`。
- 模型必须增加真实旁白内容，使文案量接近计划目标；不得把合法目标缩短到当前估算值。
- 专项测试覆盖 12 秒目标配约 4.54 秒短文案，并验证第二次扩写到匹配文案后 Creative 成功。

## F. Narration Compression Logic

- 当 `estimated_duration > target_duration_seconds` 且超过既有容差时，反馈明确要求 `COMPRESS（压缩 full_script）`。
- 使用同一个既有朗读时长估算器计算当前估算值和差值。
- 专项测试覆盖 12 秒目标配约 19.14 秒长文案。

## G. Creative Stability Constraints

- 重试反馈要求保持 `creative_concept`、`target_audience`、`key_message`、`visual_direction`、`narrative_arc`、`narration_plan.tone`、`subtitle_strategy`、`global_constraints`、`av_timeline_constraints` 及其他 Creative 字段不变。
- 同一次结构化请求中，首个满足既有业务边界的旁白目标会成为 planned target。
- 后续响应若把合法的 12 秒目标改成 5 秒来迁就短文案，将继续被 Creative 业务校验拒绝；恢复 12 秒并扩写后才可通过。
- 若目标本身违反既有边界（例如超过视频总时长），不会锁定该非法目标，模型仍可按原业务规则修正目标。

## H. Error Classification Fix

三类错误现已分离：

1. `JSON Parse Error` → `LLM_JSON_PARSE_ERROR`
2. `Schema Validation Error` → `LLM_SCHEMA_VALIDATION_ERROR`
3. `Creative Business Validation Error` → `LLM_CREATIVE_BUSINESS_VALIDATION_ERROR`

最终耗尽错误改为“连续 3 次未返回可用结构化结果”，并带最后失败类型；Creative 业务失败不再被描述为无效 JSON。

## I. Best-Effort Creative Fallback

- 只有三次 DeepSeek 响应全部为“仅旁白时长不匹配”的合法候选时，才允许在第三次严格失败后启用回退。
- 候选必须同时满足：JSON 可解析、Schema 合法、除旁白时长外的全部 Creative 业务校验通过、旁白启用且脚本非空、估算值有效、估算值不超过当前视频允许时长、目标仍在全部既有合法边界内。
- JSON、Schema、其他 Creative 业务错误、无旁白、空脚本、非法估算、超出视频时长或非法目标均不会进入候选池；任一轮出现这些情况，三次结束后仍按原错误路径失败。
- 每个候选仅在内存中保留原始解析对象、attempt、target、estimated 与 `gap = abs(estimated - target)`。
- 三次结束后按 gap 最小选择；gap 相同时选择更早的 attempt。真实复现场景 `8.11 / 20.64 / 4.89` 对目标 `12` 会选择 attempt 1。
- 返回所选模型原始结果，不改写旁白、不改写目标，也不伪装为严格校验通过。
- 日志分别记录“严格校验三次失败”和“Best-Effort 已使用”，并记录 selected attempt、target、estimated、gap；回退成功使用独立完成事件，不记录普通严格成功事件。
- `MAX_JSON_REQUEST_ATTEMPTS` 保持为 3；未增加额外 DeepSeek、Judge 或修复调用，不存在第四次请求或无限循环。
- 未找到合格候选时仍失败；旁白关闭路径保持原行为。

## J. Tests

`tests/test_creative_narration_duration_retry_hotfix.py` 现有 33 项：原 Duration Retry / Error Classification 15 项，加本轮 Best-Effort Fallback 18 项。

本轮 18 项覆盖：

1. 第一次严格通过时不触发回退。
2. 第二次严格通过时立即采用严格结果。
3. 三次纯时长偏差时选择最小 gap。
4. `8.11 / 20.64 / 4.89` 对目标 `12` 选择 attempt 1。
5. gap 计算准确。
6. gap 相同时选择更早 attempt。
7. 超过视频时长的候选被排除。
8. JSON 非法响应使整组不可回退。
9. Schema 非法响应使整组不可回退。
10. 其他 Creative 业务错误使整组不可回退。
11. 无合格候选时仍失败。
12. 回退不增加 DeepSeek 调用。
13. 最大尝试次数仍为 3。
14. 日志包含严格失败、回退使用及选择指标。
15. 回退日志不误报 JSON 错误。
16. 旁白关闭路径保持不变。
17. 正常 Creative 严格成功路径保持不变。
18. 专项测试不触发 Storyboard、Voice、Subtitle、Shot 或子进程。

结果：33/33 PASS，其中本轮新增 18/18 PASS。

## K. Regression

- Creative / Structured Retry / AV Timeline / Storyboard / Subtitle / Voice 分层回归：198/198 PASS。
- Web Backend 全量离线回归：833 tests，831 PASS，2 SKIP，FAIL=0，ERROR=0。两项为当前 Windows 无符号链接创建权限的既有条件跳过。
- 最终代码树 Core 全量：640 tests，627 PASS，13 SKIP，FAIL=0，ERROR=0。13 项为需要真实 FFmpeg/FFprobe 的既有装配集成测试；本轮按约束隐藏工具发现并让既有条件跳过生效。
- Frontend/Web UI 未修改，因此未运行前端测试或构建。

## L. Automated Verification Calls

- DeepSeek Real：NO（仅指自动化测试与回归阶段）
- MiniMax Real：NO
- TTS Real：NO
- FFmpeg / FFprobe Real：NO
- External Provider Network：0
- 所有 Hotfix 自动化行为测试均使用 Mock DeepSeek 响应；视频、语音及导出相关回归使用既有 Fake/Mock，或按既有条件跳过真实工具测试。后续用户人工 Smoke 使用了真实 DeepSeek，结果见 N 节。

## M. Diff Guard

授权实现范围内的文件：

- `prompt_generator.py`：错误分类、分类日志、分类重试提示、候选收集、三次后确定性选择及准确的最终耗尽诊断。
- `storyboard.py`：Creative 时长反馈、合法 planned target 稳定性、候选资格复核与回退结果的受控最终校验。
- `tests/test_creative_narration_duration_retry_hotfix.py`：专项离线测试。
- `docs/creative-narration-duration-retry-hotfix-report.md`：本报告。

未修改 Storyboard 生成阶段语义、Voice、Subtitle、Video Prompt 业务规则、MiniMax、Web、PostProduction 或 FFmpeg Core。`storyboard.py` 中的改动仅位于既有 Creative 校验与调用衔接处。

## N. Manual Smoke Acceptance

用户已使用此前真实失败的 `OIP-C耳机 / 30 秒` 输入完成真实 Creative Smoke，结果 PASS：

- DeepSeek 真实调用正常，最大尝试次数仍为 3，没有第 4 次调用。
- 任一轮严格校验通过时，系统正常采用首次或后续严格 PASS 结果，Best-Effort 不会错误启动。
- 三次均为纯 Narration Duration Mismatch 时，Creative 不再直接 FAILED，Best-Effort Fallback 正确启动。
- 只有合法 JSON、合法 Schema 且无其他 Creative 业务错误的结果进入候选池。
- `duration_gap` 计算正确，并选择 gap 最小的合法候选。
- 超出视频允许时长的候选不会被选中。
- selected attempt、target、estimated、gap 日志正确。
- Creative Business Validation Error 未被错误记录为 JSON Error。
- 最终 Creative 成功落盘。
- 未调用 MiniMax、TTS 或 FFmpeg，未自动进入任何下游生成阶段。

## O. git status

报告写入前的业务代码状态：

```text
 M prompt_generator.py
 M storyboard.py
?? tests/test_creative_narration_duration_retry_hotfix.py
```

写入本报告后另有：

```text
?? docs/creative-narration-duration-retry-hotfix-report.md
```

未暂存、未提交、未推送。

## P. Result

`CREATIVE NARRATION BEST-EFFORT FALLBACK: PASS`
