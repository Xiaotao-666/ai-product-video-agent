# Web Backend

本目录是 AI Product Video Agent 的本地 FastAPI Backend，负责安全 DTO、项目读写、Durable Task、Planning、Shot、Assembly 与 PostProduction Web 操作。业务语义继续复用根目录 Core。

完整安装、Provider 配置和 clone-to-run 步骤见根目录 [README](../README.md)。

## Local Development

从仓库根目录启动：

```powershell
.\.venv\Scripts\python.exe -m uvicorn web_backend.app:app --host 127.0.0.1 --port 8000
```

- API：`http://127.0.0.1:8000`
- API docs：`http://127.0.0.1:8000/docs`
- Health：`http://127.0.0.1:8000/api/health`

Backend 在没有 Provider Key 时仍可启动；能力接口会把对应 Provider 标记为 unavailable，不会在启动时调用 Provider。

## Local Runtime

- `WEB_PROJECTS_ROOT` 默认：`%USERPROFILE%\AIProductVideoAgentProjects`
- `WEB_RUNTIME_ROOT` 默认：`{WEB_PROJECTS_ROOT}/.web_runtime`
- `WEB_TASK_WORKERS` 默认：`2`
- CORS 默认允许 `127.0.0.1:5173` 与 `localhost:5173`

当前只支持一个 Uvicorn worker。不要同时使用 CLI 与 Web Backend 写入同一项目。

## Tests

从仓库根目录运行 Web Backend 测试：

```powershell
.\.venv\Scripts\python.exe -m unittest discover -s tests\web -p "test_*.py"
```
