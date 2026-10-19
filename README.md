# Junie Adapter

A modular Python package for integrating [Junie CLI](https://github.com/jetbrains/Junie) execution with optional Ollama-based prompt refinement, session logging, memory/context loading, and optional Obsidian vault integration.

Designed for use with [AgentHub](https://github.com/LukaG712/AgentHub) as a reusable coding-agent adapter, but also works as a standalone HTTP service compatible with Open WebUI.

## Quick Start

### As a Python module (recommended for AgentHub)

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
├── __init__.py        # Public API exports
├── config.py          # OrchestratorConfig dataclass + from_env() factory
├── models.py          # RunResult, SessionLog, TaskStatus dataclasses
├── junie_runner.py    # Junie CLI subprocess execution
├── prompt_refiner.py  # Ollama-based prompt refinement (optional)
├── session_logger.py  # Session log persistence
├── memory_loader.py   # MEMORY.md + session log context loading
├── obsidian.py        # Obsidian vault mirroring (optional)
└── service.py         # FastAPI HTTP service
```

### Module Overview

| Module | Responsibility |
|--------|---------------|
| `config.py` | Centralized configuration with `from_env()` factory and validation |
| `models.py` | Typed dataclasses: `RunResult`, `SessionLog`, `TaskStatus` |
| `junie_runner.py` | Subprocess management, JSON parsing, rate-limit retries, timeout handling |
| `prompt_refiner.py` | Ollama API calls for prompt rewriting (opt-in feature) |
| `session_logger.py` | Save session logs to disk, optional Ollama summary generation |
| `memory_loader.py` | Load MEMORY.md and recent session summaries for context |
| `obsidian.py` | Mirror session logs to Obsidian vault (fully optional) |
| `service.py` | FastAPI app with OpenAI-compatible `/v1/chat/completions` endpoint |

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

### Key Design Decisions

- **Prompt refinement is disabled by default** (`REFINE_PROMPT=false`). The raw prompt goes directly to Junie without an Ollama call.
- **Obsidian is fully optional**. No vault path means no vault operations.
- **Memory is pluggable**. AgentHub can inject its own context instead of relying on MEMORY.md.
- **No hardcoded paths**. All paths are configurable via environment variables.

## Platform Support

| Component | Windows | Linux | macOS |
|-----------|---------|-------|-------|
| Python package | ✓ | ✓ | ✓ |
| Ollama integration | ✓ | ✓ | ✓ |
| Junie CLI execution | ✗ | ✓ | ✓ |
| HTTP service | ✓ | ✓ | ✓ |

The Python package and HTTP service work on all platforms. Junie CLI execution requires a Linux or macOS environment.

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

### Direct Import (Recommended)

```python
from junie_adapter import run_junie_task, OrchestratorConfig
from junie_adapter.models import RunResult

config = OrchestratorConfig.from_env()
result: RunResult = await run_junie_task(
    task="Implement feature X",
    config=config,
)
```

### Custom Context Injection

Instead of using MEMORY.md, AgentHub can provide its own context:

```python
from junie_adapter import refine_prompt, OrchestratorConfig

config = OrchestratorConfig.from_env()
context = "AgentHub-provided project context..."
improved, fallback, error = await refine_prompt(raw_prompt, config, context=context)
```

### HTTP Service

```bash
uvicorn junie_adapter.service:app --host 0.0.0.0 --port 8000
```

AgentHub sends OpenAI-compatible requests to `POST /v1/chat/completions`.

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

The test suite uses mocks for external processes and HTTP requests. No live Junie installation or Ollama instance is required.

## Deployment Scripts

Optional Linux deployment helpers (not required for module usage):

- `setup.sh` — Initial setup and dependency installation
- `start.sh` — Start the HTTP service
- `install_service.sh` — Install as a systemd service

## Migration from Monolith

The old `orchestrator.py` (690 lines) is now a thin 17-line entry point that imports from `junie_adapter.service`. Existing `uvicorn orchestrator:app` commands continue to work.

### Breaking Changes

- `VAULT_PATH` no longer defaults to `/mnt/vault` — it must be explicitly set
- `REFINE_PROMPT` defaults to `false` — prompt refinement is opt-in
- Module-level globals are replaced by dependency injection via `OrchestratorConfig`

## License

MIT
