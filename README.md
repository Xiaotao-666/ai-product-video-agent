# Human-in-the-loop 视频生成 Agent

命令行流程：用户需求 + 用户备注 + 可选 Reference Asset → DeepSeek Creative Brief → 人工审核 → Storyboard → 人工审核 → 每镜头 Video Prompt → 人工审核 → Safety → MiniMax 逐镜头生成 → 逐 Shot 人工审核 → 人工确认 FFmpeg 合片。Reference Asset 只作为后续视频生成可选素材保存，不进行自动图片分析。

Web、Core、Durable Task、Prompt/Video 双版本体系及安全边界见 [Web / Core 架构与版本契约](docs/architecture.md)。

每个审核节点支持：

1. 确认并继续
2. 输入修改意见，让 DeepSeek 基于当前版本修改
3. 重新生成当前方案
4. 取消任务，立即终止所有后续 LLM 和视频 API 调用

Shot 视频生成后可选择通过、使用原 Prompt 单独重生成、AI 仅修改当前 Shot Prompt、基于当前 Prompt 用 Windows 记事本手动编辑，或取消。前一个 Shot 未通过时不会开始下一个 Shot。

手动编辑会在当前视频项目的 `prompts/editing/` 中创建一个预填完整 active Prompt 的临时副本，并等待记事本关闭。程序随后显示修改前、修改后及逐行差异；只有人工确认后才创建新的 Prompt version。放弃或返回不会改变 active Prompt，临时编辑副本会被清理。确认后的 Prompt 仍会经过 Safety，Safety 结果与人工确认内容分别保存在同一个版本记录中。

## 运行

```powershell
.\.venv\Scripts\python.exe main.py
```

程序启动后会先提示：

```text
请选择本次视频项目保存目录：
```

输入的路径就是本次任务的项目目录。程序会自动创建：

```text
项目目录/
├── videos/       完整无声视频及 assembly_manifest.json
├── shots/        MiniMax 生成的 active 分镜视频；versions/ 保留旧视频版本
├── prompts/      active Prompt、安全结果；versions/ 保留逐 Shot Prompt 历史
├── concepts/     已确认的 Creative Brief
├── storyboard/   已确认的 Storyboard JSON
├── reviews/      人工审核与取消记录
├── work/
│   └── assembly/ FFmpeg 标准化和拼接的项目内临时工作区
└── logs/
    ├── tasks/    完整任务运行日志
    ├── llm_raw/  DeepSeek 未清洗的原始响应
    ├── errors/   JSON、API、下载、文件和程序异常
    └── api/      DeepSeek 与 MiniMax 调用和状态变化
```

所有写入位置都由 `project_manager.py` 统一创建和提供，不再使用固定输出目录。

## Reference Asset 与用户备注

项目创建时可以填写 `user_notes`，用于补充人物、镜头偏好、品牌调性、禁止元素和其他创意要求。该字段保存在 `project.json.request.user_notes`；旧 Schema 2 项目缺少该字段时自动按空字符串兼容，不升级 Shot Schema。

Reference Asset 继续复制到当前项目并记录 `asset_id`、SHA-256、尺寸和格式，但当前运行流程不会自动分析图片，也不会调用 Gemini Vision、Qwen-VL 或其他 Vision API。DeepSeek 只会收到“项目是否存在参考素材”的提示，不会收到或推断图片内容。

`VisionProvider`、历史分析缓存和审核文件暂时保留，便于未来重新接入；`vision_provider_config.json` 默认设置 `visual_understanding_enabled: false` 且不配置默认 Vision Provider。主流程不会读取 Vision 凭据。

分析结果按 Reference Asset 保存：

```text
references/
├── project/
│   └── ref_001.png
└── visual_analysis/
    └── ref_001/
        └── analysis.json
```

已有项目中的 `references/visual_analysis/` 会原样保留。新项目不会创建新的 `analysis.json`，Resume、Shot 管理和 Assembly 也不会读取或生成视觉分析。

DeepSeek 的 Creative、Storyboard 和 Video Prompt 输入继续包含产品名称、产品描述、宣传目标、视频风格和 `user_notes`。真实链路 Evaluation 仍记录文本 Prompt、Shot Generation Bundle 与 Final Assembly，但不会新增视觉分析记录。

## 参考图片 / Visual Input

新建项目时可以导入多张 JPG、JPEG、PNG 或 WebP 图片。程序会把原图复制到当前项目的 `references/project/`，并在 `references/reference_manifest.json` 中记录 Reference ID、项目内相对路径、大小和 SHA-256；重复导入相同内容会复用已有资产，外部原图不会被修改。

每个 Shot 在首次真实生成前独立选择 `none` 或 `reference_image`。`none` 继续使用 MiniMax Text-to-Video；`reference_image` 把项目内副本编码为官方 `first_frame_image` Base64 Data URL，自动切换为 Image-to-Video。两种模式继续共用原有 Prompt Safety、异步 task/file ID、轮询、下载和 Resume 链路。

Shot 当前默认选择保存在 `shot.json.visual_input`，每个 `vXXX/generation.json` 另存不可变的 Visual Input snapshot，因此以后更换参考图不会改写历史版本。Candidate 同样保存独立 snapshot，批准前不会改变正式 Approved 版本。旧 Schema 2 项目缺少该字段时按 `mode=none` 读取，不需要 Schema 3 迁移。

统一任务状态：`PENDING`、`WAITING_REVIEW`、`APPROVED`、`REVISING`、`CANCELLED`、`GENERATING`、`COMPLETED`。取消记录顶层包含 `task_id`、`cancel_stage`、`timestamp`、`user_action: cancel`，并保留取消前的完整审核历史。

DeepSeek 响应使用 JSON 模式，并在解析前自动清理 Markdown 围栏、字符串内未转义换行和非法控制字符。本地修复失败时会自动重新请求，最多三次。

每次运行会创建形如 `20260812_175500_a3f2` 的 task_id。所有业务日志只使用 `project_manager.ProjectPaths.logs_dir` 及其子目录，不写入代码目录或固定输出目录。日志会对已注册 API Key、Authorization Bearer、Token 和常见密钥形式进行脱敏。

## Checkpoint 与恢复

每个用户项目根目录包含 `project.json`，记录项目 ID、需求、当前阶段、各阶段状态、取消位置、错误信息，以及每个 Shot 的 `NOT_STARTED / GENERATING / WAITING_REVIEW / APPROVED / FAILED` 状态、Prompt/视频版本和 MiniMax task/file ID。关键状态通过临时文件替换方式原子保存。

再次选择已有项目时，可以继续、查看状态、从指定阶段重新开始或退出。`APPROVED` Shot 直接跳过；`WAITING_REVIEW` 直接恢复视频审核；`GENERATING` 复用已有 MiniMax task/file ID；旧版 `COMPLETED` Shot 在视频存在时迁移为 `WAITING_REVIEW`，不会重复提交 API。

已有项目菜单还提供“Shot 管理（主动编辑已 APPROVED 镜头）”。只有主动进入该菜单才会重新打开 Approved Shot；普通继续和 Resume 仍自动跳过所有 Approved Shot。新的 Candidate Prompt 继续保存到 `prompts/versions/`，Candidate Safety 单独保存到 `prompts/candidates/`，Candidate 视频保存在 `shots/candidates/`。Candidate 批准前不会改变正式 Prompt、正式视频或 Shot 的 `APPROVED` 状态；拒绝后视频移入 `shots/versions/`，Prompt 历史保留并标记为 `REJECTED`。

Candidate 处于 `GENERATING` 时会复用现有 MiniMax task/file ID；处于 `WAITING_REVIEW` 时直接恢复审核；处于 `FAILED` 时原 Approved Shot 仍然有效。批准 Candidate 后，旧 Approved 视频先归档，再将 Candidate 安全切换为 `shots/shot_XX.mp4`。如果 `videos/` 已存在完整成片，`project.json` 会将 `assembly.needs_update` 标记为 `true`，但不会删除旧成片。

从阶段重新开始不会丢失旧文件；受影响的现有产物会先移动到各自目录下的 `revisions/`，防止旧产物被误判为当前版本，再由后续流程生成新的当前版本。

## Approved Shot 合片

只有 Storyboard 顺序中的所有 Shot 都为 `APPROVED` 且 active 视频可由 ffprobe 正常解析时，程序才显示合片确认菜单。程序不会自动启动 FFmpeg；必须选择“合成为完整视频”。参与合片的文件固定为 `shots/shot_XX.mp4`，并同时核对 `approved_video_version`，Candidate、Rejected 和 `shots/versions/` 历史文件不会进入合片。

程序首先检测 `ffmpeg -version` 和 `ffprobe -version`。媒体规格完全一致且为 H.264/yuv420p 时优先使用 concat stream copy；分辨率、FPS、codec 或 pixel format 不一致时，以第一个 Approved Shot 为目标画布，使用 `scale=...:force_original_aspect_ratio=decrease` 和 `pad=...` 保持宽高比，再统一为 H.264、yuv420p 和目标 FPS。第一版通过 `-an` 生成无声成片，不加入转场、BGM、配音或字幕。

第一版输出为 `videos/final_video.mp4`。已有完整视频时必须人工选择保存新版本、明确覆盖或取消；新版本命名为 `final_video_v002.mp4`、`final_video_v003.mp4`。`videos/assembly_manifest.json` 的顶层记录最新合片，同时通过 `assemblies[]` 保留各完整视频版本对应的 Shot ID、Approved Video version、路径和总时长。

`project.json` 的 `assembly` 记录 `NOT_STARTED / RUNNING / COMPLETED / FAILED`、最终路径、版本、时间、总时长、参与的 Shot 版本和 `needs_update`。已完成且未过期时 Resume 只显示完整视频菜单，不自动运行 FFmpeg；Candidate 批准导致 Shot 版本更新时，旧成片继续保留并将 `assembly.needs_update` 设为 `true`，等待人工确认重新合片。

## Video Provider / Adapter

视频生成业务现在只构造统一的 `VideoGenerationRequest`，由 `VideoProviderRegistry` 根据 Visual Input 能力和 `video_provider_config.json` 选择 Adapter。`video_generator.py` 只负责提交、轮询、下载和通用日志，不包含厂商 endpoint、请求字段或状态码映射。

内置 Adapter 位于 `providers/`：Hailuo 2.3 v1 支持 `none / first_frame`，H3 v2 支持 `none / first_frame / reference_asset`。厂商 API Key、Authorization Header、请求 payload、响应状态和错误码映射只存在于 Adapter/config 层。

每个 `vXXX/generation.json` 都保存 `provider`、`provider_model`、`provider_api_version`、`generation_mode`、`provider_task_id`、`file_id` 和完整 Visual Input snapshot。Resume 总是使用历史 Bundle 记录的 Provider/Model；即使以后修改默认模型，也不会把已提交任务切换到新 Provider 或重复提交。

MiniMax Credential 按模型隔离：`MiniMax-Hailuo-2.3` 继续读取 `MINIMAX_API_KEY`，`MiniMax-H3` 只读取 `MINIMAX_H3_API_KEY`，不会回退使用 Hailuo Key。API Key 只保存在 `.env`/环境变量中；项目状态、Bundle 与日志只记录安全的 `credential_env_name`。

每次创建新的 Video Version 前，可选择 `AUTO`（按 Visual Input 默认路由）或 `MANUAL`（从已注册 Adapter 中选择）。提交前会显示最终 Provider、Model、API version、generation mode、分辨率、时长和 Credential 环境变量名称；只有人工确认后才提交。Preflight 会在本地检查模型注册、Visual Input capability、Credential、分辨率、时长和参考素材完整性。

新安装请在 `.env` 中自行配置：

```dotenv
MINIMAX_API_KEY=
MINIMAX_H3_API_KEY=
GEMINI_API_KEY=
DEEPSEEK_API_KEY=
```

接入新视频平台时，只需实现 `VideoProvider`、向 Registry 注册，并在配置中声明默认模型和能力；Creative、Storyboard、Prompt Safety、Shot Review、Candidate、历史版本与 Assembly 无需修改。

## Audio / Voice Pipeline（基础架构）

项目现在包含与 Video Provider 完全分离的 `VoiceProvider` 和
`VoiceProviderRegistry`。接口统一为 `generate_voice()`、`supports()` 与
`get_metadata()`；默认配置不选择任何 Provider，因此不会自动调用真实 TTS。
后续的 OpenAI TTS、ElevenLabs 或 Azure TTS 只需增加独立 Adapter。

新项目会创建以下项目内目录：

```text
voice/
├── voice_manifest.json        # 首次生成音频版本时创建
├── scripts/
│   └── script_v001.txt        # 可独立查看的脚本历史
└── versions/
    └── v001/
        ├── script.txt
        ├── voice_config.json
        └── audio.wav
```

一个 `vXXX` 目录对应一次真实 Voice Generation，版本创建后永不覆盖。
`voice_manifest.json` 记录 active version、Provider/Model、语言、声音、任务 ID
和项目内相对路径。旧项目重新打开时只会补充默认 `voice_config` 和
`post_production` 状态，不会生成音频，也不会改变 Shot、Candidate 或
Assembly 数据。

`project.json.voice_config` 默认包含 `enabled=false`、`provider=null`、
`voice=null`、`language=zh-CN`。`post_production` 使用三个互相独立的阶段：
`VIDEO_ASSEMBLY → AUDIO_PROCESSING → FINAL_EXPORT`。它不接管现有 FFmpeg
合片；完整视频生成后由独立的命令行 Post Production 菜单调用 Voice
Pipeline，并创建不可覆盖的 Final Export Bundle。

## PostProduction 与 Final Export

`ProjectStage.COMPLETED` 只保留为旧视频工作流的兼容标记。新的
`completion_status` 区分 `VIDEO_GENERATION_COMPLETED`、
`VIDEO_ASSEMBLY_COMPLETED`、`POST_PRODUCTION` 与 `FINAL_COMPLETED`。因此
`videos/final_video.mp4` 存在不再代表整个项目结束；Resume 会显示完整视频、
配音、字幕、音乐和 Final Export 状态，并允许进入后期制作。

配音、字幕与背景音乐分别复用既有版本资产；如果对应 Manifest 已有 active
version，Resume 只读取现有素材，不会自动重复调用 TTS、字幕 Provider 或音乐
导入。

Final Export 保存到项目内的不可覆盖版本目录：

```text
exports/
├── export_manifest.json
└── v001/
    ├── final_video.mp4
    └── export_manifest.json
```

Final Export 使用 FFmpeg 将当前 Assembly 视频、100% 配音和指定音量的背景音乐
混流，并把当前字幕烧录为底部居中、白字黑边。每次导出创建新的 `vXXX`，不
覆盖历史版本；版本 Manifest 记录本次使用的 Video、Voice、Subtitle 和 Music
版本。FFmpeg/FFprobe 不可用时会在写入新版本前明确停止。

导出前会计算素材与配置指纹，包含各素材版本、文件 SHA-256、音乐音量、字幕
样式和编码设置。如果最新成功 Export 的指纹完全相同，菜单会优先显示已有
最终视频，不再默认创建重复版本；只有用户明确选择“强制重新导出”才会生成
下一个 `vXXX`。Resume 只读取 active Export，不会自动调用 FFmpeg。

### 阿里云 TTS Adapter

`providers/aliyun_tts_provider.py` 实现阿里云智能语音交互 NLS 短文本
REST TTS。官方接口没有 `model=cosyvoice` 请求参数；本项目使用
`nls-stream-tts` 作为内部 Adapter 身份，真实请求通过 `voice` 指定发音人，
固定输出 WAV。单次文本上限为 300 字符，采样率支持 8000 或 16000 Hz。

真实调用需要在 `.env` 配置：

```dotenv
ALIYUN_ACCESS_KEY_ID=
ALIYUN_ACCESS_KEY_SECRET=
ALIYUN_TTS_APP_KEY=
ALIYUN_TTS_REGION=cn-shanghai
```

`AppKey` 是智能语音交互项目级凭证，与账号级 AccessKey 不同，不能省略。
`ALIYUN_TTS_REGION` 当前支持 `cn-shanghai / cn-beijing / cn-shenzhen`；NLS
Token 获取按官方要求始终使用上海 Meta endpoint。所有凭据只从环境变量
读取，不写入 `project.json`、Voice Bundle 或日志。

调用前必须经过 Voice Preflight 和命令行确认。用户可确认、编辑配音稿或
取消；取消不会获取 Token，也不会发送 TTS 请求。生成成功后继续使用
`voice/versions/vXXX/` 不可覆盖版本结构，`voice_config.json` 只保存安全的
Provider/Model、发音人、语言、创建时间和本地计算的 WAV 时长。

### 讯飞在线语音合成

`providers/xfyun_tts_provider.py` 实现讯飞开放平台在线语音合成 WebSocket
v2 Adapter。默认 `voice_provider_config.json` 选择 `xfyun_tts`，同时继续注册
并保留 `aliyun_tts`；已有项目若在 `project.json.voice_config.provider` 中明确
保存了 `aliyun_tts`，仍会继续使用阿里云，不会被自动切换。

讯飞凭据只从 `.env` 读取：

```dotenv
XFYUN_APP_ID=
XFYUN_API_KEY=
XFYUN_API_SECRET=
```

讯飞接口返回 `raw` PCM 分片，Adapter 在内存中封装为 16-bit 单声道 WAV，
最终仍由现有 `VoiceAssetManager` 保存为
`voice/versions/vXXX/audio.wav`。默认发音人参数为 `xiaoyan`；实际可用发音人
必须以对应讯飞应用控制台已经开通并显示的 `vcn` 参数值为准。

### Subtitle Pipeline

PostProduction 的“字幕制作”现在会读取当前 Voice Bundle 的 `script.txt` 和
`audio.wav`，由本地 `ScriptSubtitleProvider` 生成 SRT，不调用外部 API。
字幕按可见文本长度分配音频总时长，最后一个 cue 不会超过 WAV 时长。

每次生成创建不可覆盖版本：

```text
subtitles/
├── subtitle_manifest.json
└── versions/
    ├── v001/
    │   ├── subtitle.srt
    │   └── subtitle_config.json
    └── v002/
        ├── subtitle.srt
        └── subtitle_config.json
```

重新打开项目时会读取 `subtitle_manifest.json` 的 active version，不会自动
生成新字幕；只有用户明确选择“生成新的字幕版本”才会创建下一个 `vXXX`。

### Music Pipeline

PostProduction 的“背景音乐”入口支持用户导入本地 WAV、MP3、FLAC、OGG、
M4A 或 AAC 文件。`LocalMusicProvider` 只做本地格式、大小和文件签名校验，
不调用外部 API，也不生成音乐。

```text
music/
├── assets/
├── music_manifest.json
└── versions/
    ├── v001/
    │   ├── music.<原格式>
    │   └── music_config.json
    └── v002/
        ├── music.<原格式>
        └── music_config.json
```

`music_volume` 默认是 `0.25`，范围为 `0.0-1.0`。导入、替换都会创建不可
覆盖的新版本，已有音乐不会被删除。Final Export 读取当前 active Music version
及其音量，在导出时与配音进行混流。
