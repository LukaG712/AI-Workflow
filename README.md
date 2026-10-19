# Junie Adapter

A modular Python package for integrating [Junie CLI](https://github.com/jetbrains/Junie) execution with Git worktree isolation, optional Ollama-based prompt refinement, session logging, pluggable context providers, and optional Obsidian vault integration.

Designed for use with [AgentHub](https://github.com/LukaG712/AgentHub) as a reliable, reusable coding-agent execution backend. Also works as a standalone HTTP service compatible with Open WebUI.

**Current version:** 1.0.0 — See [CHANGELOG.md](CHANGELOG.md) for release history.

## Key Features

- **Git worktree isolation** — Each worker runs in its own isolated Git branch, preventing interference with the main checkout or other workers.
- **Stable adapter interface** — Clean typed API (`JunieAdapter`) for AgentHub integration without importing private details.
- **Comprehensive changed-file detection** — Reports modified, added, deleted, renamed, staged, unstaged, and untracked files with diff content.
- **Pluggable context providers** — Load context from MEMORY.md, session logs, vault maps, or custom sources.
- **Honest streaming** — SSE events clearly distinguish progress updates from token output.
- **Configurable concurrency** — Default `max_concurrent_workers=1` per JUNIE-3394, with optional override.
- **Optional dependencies** — Obsidian and Ollama are fully optional; the service starts without them.

## Quick Start

### As a Python module (recommended for AgentHub)

```python
from junie_adapter import JunieAdapter, AdapterRequest

adapter = JunieAdapter(
    api_key="your-api-key",
    workspace_path="/path/to/project"
)

# Check health
if not adapter.check_health():
    raise RuntimeError("JunieOrchestrator is not available")

# Submit a task with worktree isolation
request = AdapterRequest(
    task="Refactor the main module to use async/await",
    project_path="/path/to/project",
)
result = await adapter.submit_task(request)

print(f"Status: {result.status}")
print(f"Output: {result.output[:200]}")
print(f"Changed files: {result.changed_files}")
print(f"Diff summary: {result.diff_summary}")
```

### Simple task execution (no worktree isolation)

```python
from junie_adapter import run_junie_task, OrchestratorConfig

config = OrchestratorConfig(
    api_key="your-key",
    workspace_path="/path/to/project",
    junie_bin="junie",
)
result = await run_junie_task("Implement feature X", config)
print(result.answer)
```

### As an HTTP service

```bash
# Install dependencies
pip install -r requirements.txt

# Configure environment
cp .env.example .env
# Edit .env with your settings

# Start the service
python -m orchestrator
# Or: uvicorn junie_adapter.service:app --host 0.0.0.0 --port 8000
```

## Architecture

```
junie_adapter/
├── __init__.py           # Public API exports
├── config.py             # OrchestratorConfig, IsolationMode, from_env()
├── models.py             # RunResult, SessionLog, WorkerExecution, WorkerStatus
├── junie_runner.py       # Junie CLI subprocess execution, JSON parsing
├── prompt_refiner.py     # Ollama-based prompt refinement (optional)
├── session_logger.py     # Session log persistence, Ollama summary
├── memory_loader.py      # MEMORY.md + session log context loading
├── obsidian.py           # Obsidian vault mirroring (optional)
├── service.py            # FastAPI HTTP service with honest streaming
├── worktree_manager.py   # Git worktree lifecycle, changed-file detection
├── worker_executor.py    # Worker execution with isolation and concurrency
├── context_provider.py   # Pluggable context provider interface
└── adapter.py            # Stable adapter interface for AgentHub
```

### Module Overview

| Module | Responsibility |
|--------|---------------|
| `config.py` | `OrchestratorConfig` dataclass, `IsolationMode` enum, `from_env()` factory, workspace validation |
| `models.py` | Typed dataclasses: `RunResult`, `SessionLog`, `TaskStatus`, `WorkerExecution`, `WorkerStatus` |
| `junie_runner.py` | Subprocess management, JSON parsing, rate-limit retries, timeout handling |
| `prompt_refiner.py` | Ollama API calls for prompt rewriting (opt-in, disabled by default) |
| `session_logger.py` | Save session logs to disk, optional Ollama summary generation |
| `memory_loader.py` | MEMORY.md + session log context via `ContextProvider` interface |
| `obsidian.py` | Mirror session logs to Obsidian vault (fully optional) |
| `service.py` | FastAPI app with OpenAI-compatible `/v1/chat/completions`, honest SSE streaming |
| `worktree_manager.py` | Git worktree creation (with real branches), changed-file detection, diff reporting |
| `worker_executor.py` | Worker execution with isolation, concurrency limits, PID tracking, `WorkerRegistry` |
| `context_provider.py` | Pluggable `ContextProvider` protocol with memory, vault map, and composite implementations |
| `adapter.py` | `JunieAdapter` class — clean typed interface for AgentHub integration |

## Configuration

All settings are loaded from environment variables. See `.env.example` for the full reference.

| Variable | Default | Description |
|----------|---------|-------------|
| `API_KEY` | *(required for HTTP service)* | Bearer token for authentication |
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API endpoint |
| `OLLAMA_MODEL` | `gemma4:12b` | Ollama model for prompt refinement |
| `REFINE_PROMPT` | `false` | Enable Ollama-based prompt rewriting |
| `WORKSPACE_PATH` | temp directory | Where Junie does project work |
| `VAULT_PATH` | *(none)* | Obsidian vault path (optional) |
| `MIRROR_LOGS_TO_VAULT` | `false` | Mirror session logs to vault |
| `JUNIE_TIMEOUT` | `1800` | Junie CLI timeout in seconds |
| `PORT` | `8000` | HTTP service port |
| `JUNIE_BIN` | `junie` | Path to Junie binary |
| `MAX_CONCURRENT_WORKERS` | `1` | Max concurrent workers (JUNIE-3394 limitation) |
| `ISOLATION_MODE` | `worktree` | Worker isolation: `worktree`, `none`, or `required` |

### Isolation Modes

| Mode | Behavior |
|------|----------|
| `worktree` | Use Git worktree isolation when the project is a Git repo; fall back to no isolation otherwise |
| `none` | No isolation — run directly in the project directory |
| `required` | Require Git worktree isolation — raise an error if the project is not a Git repo |

### Key Design Decisions

- **Prompt refinement is disabled by default** (`REFINE_PROMPT=false`). The raw prompt goes directly to Junie.
- **Obsidian is fully optional**. No vault path means no vault operations.
- **Ollama is optional**. Missing Ollama does not crash the service.
- **Workspace does not fall back to `VAULT_PATH`**. These are independent settings.
- **Default concurrency is 1** per JUNIE-3394 (one active session per project).
- **Worktrees use real Git branches** (not detached HEAD) for review and potential merge.
- **Streaming is post-completion**. Junie CLI output is not incrementally streamable; SSE events clearly distinguish progress from token output.

## Platform Support

| Component | Windows | Linux | macOS |
|-----------|---------|-------|-------|
| Python package | ✓ | ✓ | ✓ |
| Ollama integration | ✓ | ✓ | ✓ |
| Junie CLI execution | ✗ | ✓ | ✓ |
| HTTP service | ✓ | ✓ | ✓ |
| Git worktree isolation | ✓ | ✓ | ✓ |

The Python package, HTTP service, and Git worktree isolation work on all platforms. Junie CLI execution requires a Linux or macOS environment.

## HTTP API

The HTTP service provides an OpenAI-compatible interface for integration with Open WebUI or similar clients.

### Endpoints

- `GET /v1/models` — List available models (returns `junie-workflow`)
- `POST /v1/chat/completions` — Execute a task through Junie

### Authentication

All endpoints require `Authorization: Bearer <API_KEY>` header.

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
data: {"type": "task_started", "message": "Task submitted"}

event: progress
data: {"type": "junie_running", "message": "Junie CLI is executing"}

event: progress
data: {"type": "task_completed", "message": "Task completed"}

event: chunk
data: {"content": "Junie: [full answer from Junie CLI]"}

data: [DONE]
```

**Important**: Streaming is **post-completion**. The Junie CLI does not support reliable incremental output. Progress events indicate the execution phase; the final `chunk` event contains the complete response.

### Busy Handling

When a worker is already running, the service returns HTTP 503:

```json
{
  "error": "Junie is busy. Please try again later."
}
```

### Bypass Mode

Prefix a user message with `### Task:` to bypass Junie and send the request directly to Ollama:

```json
{
  "messages": [
    {"role": "user", "content": "### Task: Explain Python decorators"}
  ]
}
```

## AgentHub Integration

### Adapter Interface (Recommended)

The `JunieAdapter` class provides a clean, typed interface for AgentHub:

```python
from junie_adapter import JunieAdapter, AdapterRequest, AdapterResult

adapter = JunieAdapter(
    api_key="your-api-key",
    workspace_path="/path/to/project"
)

# Check health
if not adapter.check_health():
    raise RuntimeError("JunieOrchestrator is not available")

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

### Direct Import (Legacy)

```python
from junie_adapter import run_junie_task, OrchestratorConfig
from junie_adapter.models import RunResult

config = OrchestratorConfig.from_env()
result: RunResult = await run_junie_task(
    task="Implement feature X",
    config=config,
)
```

### Worker Execution with Isolation

```python
from junie_adapter import execute_worker, OrchestratorConfig, IsolationMode

config = OrchestratorConfig(
    api_key="your-key",
    workspace_path="/path/to/project",
    isolation_mode=IsolationMode.WORKTREE,
    max_concurrent_workers=1,
)

result = await execute_worker(
    task_description="Implement feature X",
    config=config,
    worker_id="worker-1",
    task_id="task-1",
)
```

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

The registry is automatically updated when `execute_worker` starts and completes. It's thread-safe via `asyncio.Lock`.

### Custom Context Injection

Instead of using MEMORY.md, AgentHub can provide its own context via the pluggable `ContextProvider` interface:

```python
from junie_adapter import (
    ContextProvider,
    MemoryFileContextProvider,
    VaultMapContextProvider,
    CompositeContextProvider,
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

## Multi-Agent Execution

JunieOrchestrator supports multiple independent workers, but with important limitations:

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

See [docs/multi-agent-feasibility.md](docs/multi-agent-feasibility.md) for the full analysis.

## Context Providers

The pluggable `ContextProvider` interface decouples context loading from core execution:

| Provider | Description |
|----------|-------------|
| `EmptyContextProvider` | Returns empty string (no context) |
| `MemoryFileContextProvider` | Loads MEMORY.md and recent session logs |
| `VaultMapContextProvider` | Loads a vault map file for project navigation |
| `CompositeContextProvider` | Combines multiple providers |

```python
from junie_adapter import create_default_context_provider, OrchestratorConfig

config = OrchestratorConfig.from_env()
provider = create_default_context_provider(
    config,
    include_memory=True,
    include_vault_map=False,
)
context = provider.load_context()
```

## Installation

```bash
# Clone the repository
git clone https://github.com/LukaG712/JunieOrchestrator.git
cd JunieOrchestrator

# Install dependencies
pip install -r requirements.txt

# For development (editable install)
pip install -e ".[test]"

# Configure environment
cp .env.example .env
# Edit .env with your settings
```

## Testing

```bash
# Run all tests
pytest tests/ -v

# Run a specific test class
pytest tests/test_orchestrator.py::TestConfig -v
```

The test suite uses mocks for external processes and HTTP requests. No live Junie installation or Ollama instance is required. **114 tests pass** (1 skipped), covering:

- **Worktree creation** — Real Git branches, collision handling, dirty main tree preservation, safe cleanup
- **Changed-file detection** — Modified, added, deleted, renamed, staged, unstaged, and untracked files
- **Worker execution** — Success, failure, timeout, non-Git repo handling, config mutation safety
- **Configuration** — Workspace validation, isolation modes, optional dependencies
- **Streaming** — Event order, busy handling, session logging correctness
- **Adapter interface** — `AdapterRequest`, `AdapterResult`, `JunieAdapter` methods
- **Context providers** — All four provider implementations and composite behavior
- **Worker registry** — Registration, completion, active worker tracking
- **Concurrency** — `WorkerSemaphore` enforcement and `max_concurrent_workers` limits

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

### "Lock is not acquired"

This bug was fixed. The service now uses a simple busy flag instead of `asyncio.Lock`, with proper `finally` blocks to guarantee release.

### Worktree validation fails on Windows

Git worktree paths use forward slashes in `git worktree list` output. The adapter normalizes paths for cross-platform compatibility. If you still encounter issues, ensure Git is up to date.

### Ollama not available

The service starts without Ollama. Prompt refinement and session summaries are silently skipped when Ollama is unreachable.

## Deployment Scripts

Optional Linux deployment helpers (not required for module usage):

- `setup.sh` — Initial setup and dependency installation
- `start.sh` — Start the HTTP service
- `install_service.sh` — Install as a systemd service

## Migration from Monolith

The old `orchestrator.py` (690 lines) is now a thin 17-line entry point that imports from `junie_adapter.service`. Existing `uvicorn orchestrator:app` commands continue to work.

### Breaking Changes

- **`VAULT_PATH` no longer falls back to `WORKSPACE_PATH`** — These are independent settings.
- **`REFINE_PROMPT` defaults to `false`** — Prompt refinement is opt-in.
- **Module-level globals replaced** — Use dependency injection via `OrchestratorConfig`.
- **Worktrees use real branches** — Changed from detached HEAD to named branches.
- **Streaming is post-completion** — SSE events clearly distinguish progress from token output.
- **Default concurrency is 1** — Enforced via `MAX_CONCURRENT_WORKERS`.

## License

MIT
