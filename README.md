# Slinker

> *"These ghosts seem unassailable, don't worry they're still inhalable!"*
> ~ **Prof. E. Gadd**, Luigi's Mansion 3

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10+-blue.svg)](https://www.python.org/downloads/)
[![FastAPI](https://img.shields.io/badge/FastAPI-0.115+-009688.svg)](https://fastapi.tiangolo.com/)
[![CI](https://github.com/LukaG712/Slinker/actions/workflows/ci.yml/badge.svg)](https://github.com/LukaG712/Slinker/actions)

**Luigi's Mansion origin:** *Slinker* ~ stealthy ghost enemies from Luigi's Mansion 3 that slink along walls and floors.

**Slinker** is a production-quality coding-agent orchestration backend that executes AI coding tasks through the [Junie CLI](https://github.com/jetbrains/Junie) with Git worktree isolation, persistent SQLite storage, and a comprehensive REST API.

It serves as the execution engine for [Greenie](https://github.com/LukaG712/Greenie) and [Trapper](https://github.com/LukaG712/Trapper), and also works standalone as an OpenAI-compatible HTTP service compatible with Open WebUI.

---

## At a Glance

| Feature | Description |
|---------|-------------|
| **Git Worktree Isolation** | Each task runs in its own isolated Git branch — no interference with your main checkout |
| **Persistent Storage** | SQLite-based storage for tasks, projects, sessions, and workers with schema migrations |
| **OpenAI-Compatible API** | Drop-in replacement for OpenAI chat completions, works with Open WebUI |
| **Honest SSE Streaming** | Server-Sent Events that clearly distinguish progress from token output |
| **Pluggable Context** | Load context from MEMORY.md, session logs, Obsidian vaults, or custom sources |
| **Optional Ollama Integration** | Prompt refinement and summarization via Ollama (disabled by default) |
| **Obsidian Sync** | Mirror session logs to an Obsidian vault for knowledge management |
| **Worker Registry** | Track active workers, monitor concurrency, and manage task lifecycle |
| **Security Hardened** | Constant-time auth, input validation, safe subprocess invocation |
| **Docker Ready** | Containerized deployment with health checks |

---

## Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/LukaG712/Slinker.git
cd Slinker

# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your API key and workspace path
```

### As an HTTP Service

```bash
# Start the service
python -m orchestrator
# Or: uvicorn junie_adapter.service:app --host 0.0.0.0 --port 8000
```

The service starts on port 8000 by default. Verify it's running:

```bash
curl http://localhost:8000/health
# {"status": "ok", "service": "Slinker"}
```

### As a Python Module

```python
from junie_adapter import JunieAdapter, AdapterRequest

adapter = JunieAdapter(
    api_key="your-api-key",
    workspace_path="/path/to/project"
)

# Submit a task with worktree isolation
request = AdapterRequest(
    task="Refactor the main module to use async/await",
    project_path="/path/to/project",
)
result = await adapter.submit_task(request)

print(f"Status: {result.status}")
print(f"Changed files: {result.changed_files}")
print(f"Diff summary: {result.diff_summary}")
```

### With Greenie

Slinker integrates with Greenie via the `Slinker_provider` module. Configure it in `hub.yaml`:

```yaml
# hub.yaml
agents:
  - name: coder
    runner: Slinker
    config:
      api_key: "your-api-key"
      workspace_path: "/path/to/workspace"
```

---

## Architecture

```
Slinker/
├── junie_adapter/              # Core package
│   ├── __init__.py             # Public API exports
│   ├── adapter.py              # JunieAdapter — stable interface for Greenie
│   ├── config.py               # OrchestratorConfig, IsolationMode, from_env()
│   ├── context_provider.py     # Pluggable context loading interface
│   ├── junie_runner.py         # Junie CLI subprocess execution & JSON parsing
│   ├── memory_loader.py        # MEMORY.md + session log context loading
│   ├── models.py               # Typed dataclasses: RunResult, WorkerExecution, etc.
│   ├── obsidian.py             # Obsidian vault mirroring (optional)
│   ├── persistent_store.py     # SQLite storage for tasks, projects, sessions
│   ├── prompt_refiner.py       # Ollama-based prompt refinement (optional)
│   ├── service.py              # FastAPI HTTP service with SSE streaming
│   ├── session_logger.py       # Session log persistence & Ollama summary
│   ├── tracing.py              # OpenTelemetry distributed tracing (optional)
│   ├── worker_executor.py      # Worker execution with isolation & concurrency
│   └── worktree_manager.py     # Git worktree lifecycle & changed-file detection
├── tests/                      # Test suite (38 test classes)
│   ├── test_adapter.py         # Adapter interface tests
│   ├── test_orchestrator.py    # Core module tests
│   ├── test_worker_executor.py # Worker execution & concurrency tests
│   └── test_worktree_manager.py # Git worktree & diff detection tests
├── orchestrator.py             # Thin entry point: uvicorn orchestrator:app
├── Dockerfile                  # Container image definition
├── requirements.txt            # Runtime dependencies
├── pyproject.toml              # Build config, pytest, ruff settings
├── .env.example                # Environment variable template
└── CHANGELOG.md                # Release history
```

### Module Overview

| Module | Responsibility |
|--------|---------------|
| `adapter.py` | `JunieAdapter` — clean typed interface for Greenie integration |
| `config.py` | `OrchestratorConfig` dataclass, `IsolationMode` enum, env loading |
| `context_provider.py` | Pluggable `ContextProvider` protocol with 4 built-in implementations |
| `junie_runner.py` | Subprocess management, JSON parsing, rate-limit retries, timeouts |
| `memory_loader.py` | MEMORY.md + session log context via `ContextProvider` interface |
| `models.py` | Typed dataclasses: `RunResult`, `SessionLog`, `WorkerExecution`, `WorkerStatus` |
| `obsidian.py` | Mirror session logs to Obsidian vault (fully optional) |
| `persistent_store.py` | SQLite storage with versioned schema for tasks, projects, sessions |
| `prompt_refiner.py` | Ollama API calls for prompt rewriting (opt-in, disabled by default) |
| `service.py` | FastAPI app with OpenAI-compatible endpoints and honest SSE streaming |
| `session_logger.py` | Save session logs to disk, optional Ollama summary generation |
| `tracing.py` | OpenTelemetry distributed tracing (optional, graceful fallback) |
| `worker_executor.py` | Worker execution with isolation, concurrency limits, PID tracking |
| `worktree_manager.py` | Git worktree creation, changed-file detection, diff reporting |

---

## Configuration

All settings are loaded from environment variables. See `.env.example` for the full reference.

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | *(required)* | Bearer token for authentication |
| `WORKSPACE_PATH` | temp directory | Where Junie does project work |
| `Slinker_DB_PATH` | `WORKSPACE_PATH/Slinker.db` | SQLite database path |
| `JUNIE_TIMEOUT` | `1800` | Junie CLI timeout in seconds |
| `JUNIE_BIN` | `junie` | Path to Junie binary |
| `PORT` | `8000` | HTTP service port |
| `MAX_CONCURRENT_WORKERS` | `1` | Max concurrent workers (JUNIE-3394) |
| `ISOLATION_MODE` | `worktree` | Worker isolation: `worktree`, `none`, or `required` |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `gemma4:12b` | Ollama model for prompt refinement |
| `REFINE_PROMPT` | `false` | Enable Ollama-based prompt rewriting |
| `VAULT_PATH` | *(none)* | Obsidian vault path (optional) |
| `MIRROR_LOGS_TO_VAULT` | `false` | Mirror session logs to vault |

### Isolation Modes

| Mode | Behavior |
|------|----------|
| `worktree` | Use Git worktree isolation when the project is a Git repo; fall back to no isolation otherwise |
| `none` | No isolation — run directly in the project directory |
| `required` | Require Git worktree isolation — raise an error if the project is not a Git repo |

---

## REST API

Slinker provides a comprehensive REST API. All `/api/v1/*` endpoints require `Authorization: Bearer <API_KEY>` authentication. OpenAPI documentation is available at `/docs` and `/redoc`.

### Health & Status

| Endpoint | Description |
|----------|-------------|
| `GET /health` | Basic health check (no auth required) |
| `GET /ready` | Readiness check — verifies config and database |

### Projects

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/projects` | List all registered projects |
| `POST /api/v1/projects` | Register a new project |

### Tasks

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/tasks` | List tasks with `?project_id=`, `?status=`, `?limit=`, `?offset=` filters |
| `GET /api/v1/tasks/{task_id}` | Get task detail including output, changed files, and diff |

### Workers

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/workers` | List all active workers and their status |
| `GET /api/v1/workers/{worker_id}` | Get a specific worker by ID |

### Sessions

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/sessions` | List session records with `?task_id=` and `?limit=` filters |

### Queue & Concurrency

| Endpoint | Description |
|----------|-------------|
| `GET /api/v1/queue` | Queue status, active worker count, and concurrency limits |

### OpenAI-Compatible Endpoints

Compatible with OpenAI clients (e.g., Open WebUI):

| Endpoint | Description |
|----------|-------------|
| `GET /v1/models` | List available models (returns `junie-workflow`) |
| `POST /v1/chat/completions` | Execute a task through Junie |

### Database Backup

| Endpoint | Description |
|----------|-------------|
| `POST /api/v1/backup` | Create a backup of the SQLite database |
| `GET /api/v1/backups` | List available backups |

### Authentication

All endpoints require `Authorization: Bearer <API_KEY>` header. Authentication uses constant-time comparison (`hmac.compare_digest`) to prevent timing attacks.

### Request Format

```json
{
  "messages": [
    {"role": "user", "content": "Implement a binary search algorithm"}
  ],
  "stream": false
}
```

### Response Format

```json
{
  "id": "chatcmpl-1234567890",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "junie-workflow",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "Junie: [answer from Junie CLI]"
      },
      "finish_reason": "stop"
    }
  ]
}
```

### Streaming Response

Streaming uses Server-Sent Events (SSE) with distinct event types:

```
event: progress
data: {"type": "task_completed", "message": "Junie has completed the task."}

event: chunk
data: {"content": "Junie: [full answer from Junie CLI]"}

data: [DONE]
```

**Important**: Streaming is **post-completion**. The Junie CLI does not support reliable incremental output. Progress events indicate the execution phase; the final `chunk` event contains the complete response.

### Bypass Mode

Prefix a user message with `### Task:` to bypass Junie and send the request directly to Ollama:

```json
{
  "messages": [
    {"role": "user", "content": "### Task: Explain Python decorators"}
  ]
}
```

---

## Greenie Integration

### Adapter Interface (Recommended)

The `JunieAdapter` class provides a clean, typed interface for Greenie:

```python
from junie_adapter import JunieAdapter, AdapterRequest, AdapterResult

adapter = JunieAdapter(
    api_key="your-api-key",
    workspace_path="/path/to/project"
)

# Check health
if not adapter.check_health():
    raise RuntimeError("Slinker is not available")

# Submit a task
request = AdapterRequest(
    task="Refactor the main module to use async/await",
    project_path="/path/to/project",
    use_worktree=True,
    timeout=1800,
)
result: AdapterResult = await adapter.submit_task(request)

# Inspect results
print(f"Status: {result.status}")          # WorkerStatus.COMPLETED
print(f"Exit code: {result.exit_code}")    # 0
print(f"Changed files: {result.changed_files}")
print(f"Diff summary: {result.diff_summary}")
print(f"Worktree: {result.worktree_path}")
print(f"Branch: {result.branch_name}")

# Clean up worktree when done
await adapter.cleanup_worktree(result.worktree_path)
```

### AdapterRequest Options

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `task` | `str` | *(required)* | Task description to execute |
| `project_path` | `str` | *(required)* | Path to the project directory |
| `worker_id` | `str` | auto-generated | Unique worker identifier |
| `task_id` | `str` | auto-generated | Unique task identifier |
| `parent_task_id` | `str` | `None` | Parent task ID for task graphs |
| `use_worktree` | `bool` | `True` | Enable Git worktree isolation |
| `timeout` | `int` | `1800` | Per-task timeout in seconds |
| `isolation_mode` | `IsolationMode` | `WORKTREE` | Isolation mode enum |
| `config_overrides` | `dict` | `None` | Config overrides (API key, Junie binary, etc.) |

### AdapterResult Fields

| Field | Type | Description |
|-------|------|-------------|
| `worker_id` | `str` | The worker that executed the task |
| `task_id` | `str` | The task ID |
| `status` | `WorkerStatus` | `COMPLETED`, `FAILED`, or `TIMED_OUT` |
| `output` | `str` | Standard output from Junie |
| `exit_code` | `int` | Exit code from the Junie process |
| `changed_files` | `list[str]` | List of changed file paths |
| `diff_summary` | `str` | Human-readable summary of changes |
| `error` | `str` | Error message if execution failed |
| `worktree_path` | `str` | Path to the worktree (if isolation was used) |
| `branch_name` | `str` | Git branch name (if worktree was created) |
| `duration_seconds` | `float` | Execution duration in seconds |

### Worker Registry

The `WorkerRegistry` tracks active workers for monitoring and cancellation:

```python
from junie_adapter import get_worker_registry

registry = get_worker_registry()

# Check how many workers are active
print(f"Active workers: {registry.active_count}")

# List all active workers
active = await registry.get_active()
for worker in active:
    print(f"{worker.worker_id}: {worker.status} — {worker.task_description}")

# Check a specific worker
worker = await registry.get("worker-1")
if worker:
    print(f"Status: {worker.status}, PID: {worker.pid}")

# Remove a completed worker from the registry
await registry.remove("worker-1")
```

### Custom Context Injection

Slinker provides a pluggable `ContextProvider` interface for custom context loading:

```python
from junie_adapter import (
    CompositeContextProvider,
    MemoryFileContextProvider,
    VaultMapContextProvider,
    create_default_context_provider,
    OrchestratorConfig,
)

config = OrchestratorConfig.from_env()

# Use the default provider (MEMORY.md + session logs)
provider = create_default_context_provider(config, include_memory=True)
context = provider.load_context()

# Or build a custom composite
provider = CompositeContextProvider([
    MemoryFileContextProvider(config),
    VaultMapContextProvider(config=config),
])
context = provider.load_context()
```

| Provider | Description |
|----------|-------------|
| `EmptyContextProvider` | Returns empty string (no context) |
| `MemoryFileContextProvider` | Loads MEMORY.md and recent session logs |
| `VaultMapContextProvider` | Loads a vault map file for project navigation |
| `CompositeContextProvider` | Combines multiple providers |

---

## Worker Isolation

Each worker runs in an isolated Git worktree on its own branch, ensuring:

- **No interference** — Workers don't modify the main checkout or each other's work.
- **Reviewable changes** — Each worker's branch can be inspected, diffed, and merged independently.
- **Safe cleanup** — Worktrees are removed with their associated branches; unreviewed changes are preserved until explicitly cleaned up.
- **Branch collision handling** — If a branch name already exists, a unique suffix is appended.

### Changed-File Detection

The adapter reports all categories of file changes:

- **Modified tracked files** — Files that exist in the base commit and have been changed.
- **Added files** — New files staged or committed.
- **Deleted files** — Files removed since the base commit.
- **Renamed files** — Files detected as renames by Git.
- **Staged changes** — Files staged but not yet committed.
- **Untracked files** — New files not yet staged.
- **Unstaged changes** — Modified files not yet staged.

### Diff Reporting

```python
from junie_adapter import get_changed_files, get_worker_diff, get_change_summary
from pathlib import Path

worktree = Path("/path/to/worktree")

# Get all changed file paths
files = get_changed_files(worktree)

# Get structured diff data
diffs = get_worker_diff(worktree, "base-commit-sha")
for diff in diffs:
    print(f"{diff.file_path}: {diff.status}")
    print(diff.diff_content)

# Get a human-readable summary
summary = get_change_summary(worktree)
print(summary)
```

---

## Multi-Agent Execution

Slinker supports multiple independent workers with important limitations:

### JUNIE-3394 Limitation

Junie CLI enforces **one active session per project**. This means:

- Default `max_concurrent_workers=1` — workers run sequentially.
- Parallel workers targeting the same project will conflict.
- Git worktrees do not bypass this restriction.

### Sequential Multi-Worker Pattern

The recommended pattern for multi-agent work:

```python
from junie_adapter import JunieAdapter, AdapterRequest

adapter = JunieAdapter(api_key="your-key", workspace_path="/path/to/project")

tasks = [
    "Create authentication API endpoints",
    "Create frontend login form",
    "Write integration tests for auth flow",
]

results = []
for task in tasks:
    request = AdapterRequest(task=task, project_path="/path/to/project")
    result = await adapter.submit_task(request)
    results.append(result)

# Review all results
for r in results:
    print(f"{r.worker_id}: {r.status} — {r.diff_summary}")
```

---

## Platform Support

| Component | Windows | Linux | macOS |
|-----------|---------|-------|-------|
| Python package | ✓ | ✓ | ✓ |
| Ollama integration | ✓ | ✓ | ✓ |
| Junie CLI execution | ✗ | ✓ | ✓ |
| HTTP service | ✓ | ✓ | ✓ |
| Git worktree isolation | ✓ | ✓ | ✓ |

The Python package, HTTP service, and Git worktree isolation work on all platforms. Junie CLI execution requires a Linux or macOS environment.

---

## Docker Deployment

Build and run Slinker in a container:

```bash
# Build the image
docker build -t Slinker:latest .

# Run with environment variables
docker run -d \
  --name Slinker \
  -p 8000:8000 \
  -e API_KEY=your-secret-key \
  -e WORKSPACE_PATH=/workspace \
  -v $(pwd)/workspace:/workspace \
  Slinker:latest
```

Or use the unified `docker compose up` from the [Workflow](https://github.com/LukaG712/Workflow) parent repository to run all three services together.

---

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific test class
pytest tests/test_orchestrator.py::TestConfig -v
```

The test suite uses mocks for external processes and HTTP requests. No live Junie installation or Ollama instance is required. **38 test classes** covering:

- **Worktree creation** — Real Git branches, collision handling, dirty main tree preservation, safe cleanup
- **Changed-file detection** — Modified, added, deleted, renamed, staged, unstaged, and untracked files
- **Worker execution** — Success, failure, timeout, non-Git repo handling, config mutation safety
- **Configuration** — Workspace validation, isolation modes, optional dependencies
- **Streaming** — Event order, busy handling, session logging correctness
- **Adapter interface** — `AdapterRequest`, `AdapterResult`, `JunieAdapter` methods
- **Context providers** — All four provider implementations and composite behavior
- **Worker registry** — Registration, completion, active worker tracking
- **Concurrency** — `WorkerSemaphore` enforcement and `max_concurrent_workers` limits

---

## Deployment Scripts

Optional Linux deployment helpers (not required for module usage):

- `setup.sh` — Initial setup and dependency installation
- `start.sh` — Start the HTTP service
- `install_service.sh` — Install as a systemd service

---

## Troubleshooting

### "Workspace path does not exist"

Set `WORKSPACE_PATH` to a valid, writable directory:

```bash
export WORKSPACE_PATH=/path/to/your/project
```

### "Junie is busy" (HTTP 503)

A worker is already running. Wait for it to complete or increase `MAX_CONCURRENT_WORKERS` (not recommended due to JUNIE-3394).

### "Not a Git repository"

Worktree isolation requires a Git repository. Either:

1. Initialize the project as a Git repo: `git init`
2. Set `ISOLATION_MODE=none` to disable isolation
3. Set `ISOLATION_MODE=required` to enforce Git repos (raises on non-Git)

### Ollama not available

The service starts without Ollama. Prompt refinement and session summaries are silently skipped when Ollama is unreachable.

### Worktree validation fails on Windows

Git worktree paths use forward slashes in `git worktree list` output. The adapter normalizes paths for cross-platform compatibility. Ensure Git is up to date.

---

## Key Design Decisions

- **Prompt refinement is disabled by default** (`REFINE_PROMPT=false`). The raw prompt goes directly to Junie.
- **Obsidian is fully optional**. No vault path means no vault operations.
- **Ollama is optional**. Missing Ollama does not crash the service.
- **Workspace does not fall back to `VAULT_PATH`**. These are independent settings.
- **Default concurrency is 1** per JUNIE-3394 (one active session per project).
- **Worktrees use real Git branches** (not detached HEAD) for review and potential merge.
- **Streaming is post-completion**. Junie CLI output is not incrementally streamable; SSE events clearly distinguish progress from token output.
- **Auth uses constant-time comparison** to prevent timing attacks.

---

## Migration from JunieOrchestrator

Slinker was previously named **JunieOrchestrator**. The package name `junie_adapter` remains unchanged for backward compatibility.

### What Changed

- **Product name** — Changed from "Junie Orchestrator" to "Slinker" in documentation, log messages, and service descriptions.
- **Persistent storage** — New SQLite-based storage for tasks, projects, sessions, and workers.
- **Expanded API** — New endpoints: `/health`, `/ready`, `/api/v1/projects`, `/api/v1/tasks`, `/api/v1/workers`, `/api/v1/sessions`, `/api/v1/queue`.
- **Security** — Bearer token auth now uses constant-time comparison (`hmac.compare_digest`).
- **OpenTelemetry** — Optional distributed tracing support.
- **Database backup** — Built-in SQLite backup and restore endpoints.

### Backward Compatibility

- **`junie_adapter` package** — All existing imports continue to work.
- **`orchestrator.py`** — The thin entry point still works: `uvicorn orchestrator:app`.
- **Environment variables** — All existing variables are preserved; new ones are optional.
- **OpenAI endpoints** — `/v1/models` and `/v1/chat/completions` are unchanged.
- **`.env` file** — Existing `.env` files work without modification.

---

## License

MIT

**Repo:** [LukaG712/Slinker](https://github.com/LukaG712/Slinker)

---

> **Disclaimer:** This project is a fan-made, independent software project. It is not affiliated with, endorsed by, or in any way connected to Nintendo, Next Level Games, or any of their subsidiaries. All Luigi's Mansion trademarks and character names are property of their respective owners. Service names are used purely for thematic inspiration.
