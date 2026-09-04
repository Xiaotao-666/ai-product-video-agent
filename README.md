# AI Product Video Agent

## Overview

AI Product Video Agent 是一个 Windows-first、Human-in-the-loop 的本地视频生产工具，可把产品信息逐步转换为 Creative、Storyboard、Video Prompt、分镜视频、配音、字幕、音乐和最终成片。所有正式生成与导出动作都保留人工确认、版本记录和恢复能力。

## Features

- Creative、Storyboard 与 Video Prompt 规划
- MiniMax 分镜视频生成
- 逐 Shot 人工审核、重试与版本管理
- 多镜头批量生成与本地 Assembly
- Voice / TTS、Narration Subtitle 与本地 Music
- FFmpeg Final Export
- Durable Task、F5 恢复和不可覆盖的历史版本
- CLI 与本地 React/FastAPI Web 界面

## Architecture

```text
Product Request
      ↓
Creative
      ↓
Storyboard
      ↓
Video Prompt
      ↓
Shot Generation
      ↓
Review / Versions
      ↓
Assembly
      ↓
Voice
      ↓
Narration Subtitle
      ↓
Music
      ↓
Final Export
```

详细的 Web/Core 边界和版本契约见 [docs/architecture.md](docs/architecture.md)。

## Provider Matrix

| Capability | Provider / Tool | Credential |
|---|---|---|
| Creative | DeepSeek | `DEEPSEEK_API_KEY` |
| Storyboard | DeepSeek | `DEEPSEEK_API_KEY` |
| Video Prompt / Safety | DeepSeek | `DEEPSEEK_API_KEY` |
| Video Generation | MiniMax Hailuo 2.3 | `MINIMAX_API_KEY` |
| Video Generation | MiniMax H3 | `MINIMAX_H3_API_KEY` |
| Voice / TTS | Xfyun Online TTS | `XFYUN_APP_ID`, `XFYUN_API_KEY`, `XFYUN_API_SECRET` |
| Voice / TTS | Aliyun NLS TTS | `ALIYUN_ACCESS_KEY_ID`, `ALIYUN_ACCESS_KEY_SECRET`, `ALIYUN_TTS_APP_KEY` |
| Subtitle | Local deterministic providers | None |
| Music | Local asset | None |
| Assembly / Final Export | FFmpeg + FFprobe | None |

MiniMax 只负责视频生成，不是 TTS Provider。

## Requirements

- Windows：Verified
- Python：3.14.6 tested；推荐使用 Python 3.14.x
- Node.js：24.19.0 tested；当前正式验证范围为 Node 24.x
- npm：11.17.0 tested
- FFmpeg 与 FFprobe：Assembly 和 Final Export 必需，且必须可从 `PATH` 发现
- Provider 账户：根据 Minimum 或 Full Setup 配置

Other Python versions are currently unverified.

## Quick Start

### 1. Clone

```powershell
git clone https://github.com/Xiaotao-666/ai-product-video-agent.git
cd ai-product-video-agent
```

### 2. Create the Python environment

如果 Windows Python Launcher 已安装：

```powershell
py -3.14 -m venv .venv
```

如果 `py -3.14` 不可用，但当前 `python` 已指向 Python 3.14：

```powershell
python -m venv .venv
```

安装 Python 依赖：

```powershell
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
```

### 3. Install the frontend

仓库包含 `package-lock.json`，请使用可复现安装：

```powershell
cd frontend
npm ci
cd ..
```

### 4. Install FFmpeg

自行安装 FFmpeg，并确保 `ffmpeg` 和 `ffprobe` 都在系统 `PATH` 中。不要只安装其中一个。

```powershell
where.exe ffmpeg
where.exe ffprobe
ffmpeg -version
ffprobe -version
```

没有这两个命令时，Planning、Shot Generation、Voice 和 Music Upload 仍可使用，但 Assembly 与 Final Export 不可用。

### 5. Configure the environment

```powershell
Copy-Item .env.example .env
```

打开根目录 `.env`，填入自己的 Provider 凭据。不要把 `.env` 提交到 Git。

#### Minimum Setup

基本视频生产需要：

- `DEEPSEEK_API_KEY`
- `MINIMAX_API_KEY` 或 `MINIMAX_H3_API_KEY` 中与所选视频模型匹配的一项
- Python、Node.js、FFmpeg 和 FFprobe

可完成：

```text
Creative → Storyboard → Video Prompt → Shot Generation → Assembly
```

#### Full Setup

在 Minimum Setup 基础上，再配置一个 TTS Provider：

- Xfyun：`XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`
- 或 Aliyun：`ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`、`ALIYUN_TTS_APP_KEY`、`ALIYUN_TTS_REGION`

即可完成 Voice、Narration Subtitle 和带旁白的 Final Export。Music 始终可选，并使用本地音频文件。

### 6. Projects root

Web 默认把用户项目保存到：

```text
%USERPROFILE%\AIProductVideoAgentProjects
```

项目数据默认位于用户目录，不在 Git 仓库中。需要自定义时，在根 `.env` 设置：

```dotenv
WEB_PROJECTS_ROOT=E:\AIProductVideoAgentProjects
```

`WEB_RUNTIME_ROOT` 留空时自动使用 `{WEB_PROJECTS_ROOT}/.web_runtime`。

### 7. Start the backend

在仓库根目录打开一个 PowerShell：

```powershell
.\.venv\Scripts\python.exe -m uvicorn web_backend.app:app --host 127.0.0.1 --port 8000
```

- Backend：`http://127.0.0.1:8000`
- API docs：`http://127.0.0.1:8000/docs`

### 8. Start the frontend

在第二个 PowerShell 中：

```powershell
cd frontend
npm run dev
```

浏览器打开：`http://127.0.0.1:5173`

## CLI

CLI 保持可用，并会要求选择本次项目目录：

```powershell
.\.venv\Scripts\python.exe main.py
```

## First Project

在 Web 打开 `Projects`，选择 `Create Project`，填写产品信息后按以下阶段推进：

```text
Create Project → Planning → Shots → Assembly → PostProduction
```

生成、重试、审核和导出均由用户明确触发；刷新页面不会自动重复调用 Provider。

## API / Provider Configuration

根 `.env.example` 是 Backend/CLI 配置来源。主要变量：

- AI Planning：`DEEPSEEK_API_KEY`
- Video：`MINIMAX_API_KEY`、`MINIMAX_H3_API_KEY`、`MINIMAX_API_BASE_URL`
- Xfyun TTS：`XFYUN_APP_ID`、`XFYUN_API_KEY`、`XFYUN_API_SECRET`
- Aliyun TTS：`ALIYUN_ACCESS_KEY_ID`、`ALIYUN_ACCESS_KEY_SECRET`、`ALIYUN_TTS_APP_KEY`、`ALIYUN_TTS_REGION`
- Web：`WEB_HOST`、`WEB_PORT`、`WEB_PROJECTS_ROOT`、`WEB_RUNTIME_ROOT`、`WEB_TASK_WORKERS`、`WEB_CORS_ORIGINS`

Frontend 只读取 `frontend/.env` 中的 `VITE_API_BASE_URL`。不要把任何 Provider Secret 放入 Frontend 环境。

## Testing

从仓库根目录分别运行 Core 与 Web Backend 测试：

```powershell
# Core
.\.venv\Scripts\python.exe -m unittest discover -s tests

# Web Backend
.\.venv\Scripts\python.exe -m unittest discover -s tests\web -p "test_*.py"
```

运行 Frontend 测试与构建：

```powershell
cd frontend
npm test
npm run build
```

测试不得使用真实 Provider Key；涉及 Provider 的自动化测试使用 Mock/Fake。

## Security

- 永远不要提交 `.env`、Provider 响应、用户项目或生成媒体。
- Browser/Frontend 不读取 DeepSeek、MiniMax 或 TTS Secret。
- 只使用你自己的 Provider 凭据，并自行管理额度与权限。
- 视频与 TTS Provider 可能产生费用；提交前检查确认页面。
- 对失败或提交状态未知的 Provider Task，重试前先确认远端任务状态。
- Web 默认只绑定本机 loopback；不要在没有额外安全边界时暴露到公网。

## Runtime Data

每个项目使用本地文件系统保存 `project.json`、Creative、Storyboard、Prompt、Shot、Voice、Subtitle、Music、Export 和日志。Web Durable Task 默认保存在项目根下的 `.web_runtime`。

项目不需要数据库、Redis、Celery、Docker、云存储或消息队列。不要让 CLI 与 Web Backend 同时写入同一个项目。

## Platform Support

- Windows：Verified
- macOS：Not currently supported for the full workflow
- Linux：Not currently supported for the full workflow

当前包含 Windows 路径、PowerShell 和系统文件打开行为；不要把未验证平台视为已支持。

## Known Limitations

- Windows-first；完整 macOS/Linux 工作流未验证。
- FFmpeg/FFprobe 需要用户自行安装并加入 `PATH`。
- Provider Key、模型权限、套餐与地区可用性由用户账户决定。
- TTS 可选，但启用 Narration 的 Final Export 需要有效 active Voice。
- Music 是本地可选资产；Subtitle 是本地确定性生成。
- 数据只保存在本地文件系统，没有数据库或云端备份。
- 当前面向单机开发者/个人工作流，不是多用户 SaaS。
- Web Backend 仅支持单个 Uvicorn worker。

## License

This project is licensed under the [MIT License](LICENSE).
