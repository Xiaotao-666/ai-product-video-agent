# Web Backend Phase 1

This directory contains the local FastAPI foundation for the AI Product Video
Agent. It does not provide generation, review, assembly, export, or frontend
actions yet.

## Local start

From the repository root, use the existing virtual environment:

```powershell
.\.venv\Scripts\python.exe -m uvicorn web_backend.app:app --host 127.0.0.1 --port 8000
```

The supported Web V1 binding is local loopback only:

```text
http://127.0.0.1:8000
```

Interactive API documentation is available at:

```text
http://127.0.0.1:8000/docs
```

## Phase 1 API

```text
GET  /api/health
GET  /api/capabilities
GET  /api/projects
POST /api/projects
GET  /api/projects/{project_id}
GET  /api/projects/{project_id}/workflow
```

The workflow endpoint reports deterministic `available_actions`, but Phase 1
does not expose endpoints that execute those actions. Capabilities contain only
availability booleans and never return credential material or local paths.

The default development CORS origins are:

```text
http://127.0.0.1:5173
http://localhost:5173
```

## Concurrency limit

Web writes are serialized only inside one backend process. Use one Uvicorn
worker, and do not let the CLI and Web backend write the same project at the
same time. Cross-process task recovery and locking are not part of Phase 1.
