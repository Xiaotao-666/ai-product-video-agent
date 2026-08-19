# Web Backend Phase 1

This directory contains the local FastAPI backend for the AI Product Video
Agent. Creative and Storyboard generation, approval, targeted revision, and
full regeneration are the currently executable planning actions. Video Prompt,
video generation, assembly, and export actions remain unavailable.

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
POST /api/projects/{project_id}/planning/creative/generate
POST /api/projects/{project_id}/planning/creative/retry
POST /api/projects/{project_id}/planning/creative/approve
POST /api/projects/{project_id}/planning/creative/revise
POST /api/projects/{project_id}/planning/creative/regenerate
POST /api/projects/{project_id}/planning/storyboard/generate
POST /api/projects/{project_id}/planning/storyboard/revise
POST /api/projects/{project_id}/planning/storyboard/regenerate
POST /api/projects/{project_id}/planning/storyboard/approve
```

The workflow endpoint reports deterministic `available_actions`. The Creative
generate endpoint accepts only projects whose current actions include
`GENERATE_CREATIVE`, returns a durable task with HTTP 202, and performs the
same check again after the worker acquires the project write lock. Capabilities
contain only availability booleans and never return credential material or
local paths.

The default development CORS origins are:

```text
http://127.0.0.1:5173
http://localhost:5173
```

## Concurrency limit

Web writes are serialized only inside one backend process. Use one Uvicorn
worker, and do not let the CLI and Web backend write the same project at the
same time. Cross-process task recovery and locking are not part of Phase 1.

## Durable local task foundation

Phase 3A-1 adds durable Web execution tracking without exposing a task submit
endpoint or connecting any business action. Task records are atomically stored
under `WEB_RUNTIME_ROOT/tasks` (by default the `.web_runtime` directory beside
the Agent projects), never inside an Agent project.

```text
GET /api/tasks/{task_id}
GET /api/projects/{project_id}/tasks
```

`WEB_TASK_WORKERS` defaults to `2`. Reads do not create the runtime directory;
the first future internal task submission creates it lazily. On startup,
abandoned `QUEUED` or `RUNNING` records become `INTERRUPTED` and are never
automatically replayed. The runner does not retry business callables.

The current implementation remains limited to one Uvicorn worker. The CLI and
Web backend must not write the same project concurrently.

## Creative tasks and approval

Generate, revise, and regenerate use distinct durable task operations and the
shared Core Creative callables, so Core remains responsible for DeepSeek
prompts, structured-output retry, atomic canonical replacement, evaluation
history, and `project.json` review state. Revise feedback is captured only by
the in-process worker and Core evaluation history; it is never copied into a
Web task record. The Web runner does not retry provider calls. A successful
task stores only a small Creative resource reference; clients reload Creative
and Workflow through the GET APIs. Approval remains a short synchronous action
and never starts Storyboard automatically.

## Storyboard generation task

Storyboard generation is available only after Creative approval and uses the
durable `STORYBOARD_GENERATE` operation. The endpoint returns HTTP 202, and the
worker revalidates workflow state after acquiring the project lock. Shared Core
logic remains responsible for the DeepSeek prompt, structured-output checks,
deterministic A/V scheduling, canonical atomic save, evaluation history, and
transition to Storyboard review. A successful task returns only a small
Storyboard resource reference; clients reload Project, Workflow, and
Storyboard through GET APIs. Generation never auto-approves Storyboard or
starts Video Prompt generation.

Storyboard approval is a short synchronous HTTP 200 action. It rejects active
project tasks, validates `APPROVE_STORYBOARD` before and after acquiring the
existing project write lock, then calls the shared Core approval transition.
It creates no durable task, does not modify the Storyboard canonical, and does
not generate Video Prompts or call any provider.

Storyboard revise and regenerate are distinct durable operations. Revise uses
the current canonical Storyboard plus bounded feedback; regenerate deliberately
does not pass the old Storyboard or feedback to Core. Both reuse Core provider
validation and deterministic Timeline Scheduler behavior, atomically replace
the canonical only after validation, and return to `WAITING_REVIEW`. Feedback
is not stored in Web task JSON, and neither action starts Video Prompt work.

The Backend loads the same repository `.env` file as the CLI during server
startup. Capability preflight checks only whether DeepSeek is configured and
never returns the credential. Automated tests mock the Core Provider call.
