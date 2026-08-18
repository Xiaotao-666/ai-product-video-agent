# Web Frontend Phase 2C

This is the local React/Vite interface for the AI Product Video Agent. Phase 2C
adds local project creation while preserving the Projects and System pages.

```powershell
npm install
npm run dev
```

The development server binds to `http://127.0.0.1:5173`. The default backend
is `http://127.0.0.1:8000`; it can be changed with `VITE_API_BASE_URL`.

- `/projects` reads the safe project summaries from `GET /api/projects`.
- `/projects/new` creates a standard Agent project with `POST /api/projects`.
- `/system` reads backend health and capability status.
- `/` redirects to `/projects`.

Frontend environment variables must never contain provider credentials.
