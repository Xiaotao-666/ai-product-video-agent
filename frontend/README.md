# Web Frontend

本目录是 AI Product Video Agent 的本地 React/Vite 界面。完整的系统要求、环境配置和 clone-to-run 步骤见根目录 [README](../README.md)。

## Development

```powershell
npm ci
npm run dev
```

- 开发服务器：`http://127.0.0.1:5173`
- 默认 Backend：`http://127.0.0.1:8000`

测试与构建：

```powershell
npm test
npm run build
```

需要修改 Backend 地址时，复制 `.env.example` 为 `.env` 并设置 `VITE_API_BASE_URL`。Frontend 环境只能包含公开的 API 地址，禁止放置 DeepSeek、MiniMax 或 TTS Secret。
