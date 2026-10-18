---
sessionId: session-261018-135529-1o4v
---

# Requirements

### Overview & Goals
Build a Python FastAPI orchestrator service that connects Open WebUI to Junie CLI through a prompt-improvement step using a local Ollama model. Everything runs locally on a Linux VM/LAN with no paid APIs or cloud services.

### Flow
```
Open WebUI -> orchestrator (OpenAI-compatible API)
  -> 1) promptbuilder: local Ollama model rewrites the prompt
  -> 2) run Junie CLI headless on Obsidian vault with improved prompt
  -> 3) return Junie's answer to Open WebUI as a chat reply
```
The improved prompt runs immediately with no approval step.

### Scope

**In Scope:**
- OpenAI-compatible API (`/v1/models`, `/v1/chat/completions` with streaming support)
- Bearer token authentication
- Prompt improvement via Ollama with fallback to raw prompt
- Junie CLI execution as subprocess with JSON output parsing
- Single-run lock (one Junie at a time)
- Rate limit retry logic (429 / RESOURCE_EXHAUSTED)
- `### Task:` background request bypass to Ollama
- Logging to `logs/runs.log`
- Pytest test suite with mock Junie
- README with setup, systemd unit, Open WebUI config

**Out of Scope:**
- Sessions, memory, or conversation history
- Custom frontend
- Database
- Paid APIs or cloud services
- Running real `junie` against the vault during development

### Tech Stack
- Python 3.11+, FastAPI, uvicorn, httpx, pytest
- Files: `orchestrator.py`, `.env.example`, `requirements.txt`, `README.md`, `tests/`
- Config via environment variables with sensible defaults


# Technical Design

### Current Implementation
Empty project folder. Everything is new.

### Key Decisions

1. **Single file architecture** — `orchestrator.py` contains the entire FastAPI app (routes, services, utilities). Keeps it simple as requested.
2. **Junie CLI flags** — From `junie --help`: `junie --task "<text>" --project <path> --output-format json`. JSON prints to stdout. A tolerant parser will extract the answer from whatever JSON shape Junie returns.
3. **Async subprocess** — `asyncio.to_thread(subprocess.run, ...)` for non-blocking Junie execution with a lock for single-run enforcement.
4. **Ollama bypass heuristic** — If the last user message starts with `### Task:`, forward directly to Ollama instead of Junie. This handles Open WebUI's background title/tag generation requests.
5. **Rate limit retries** — On 429 or RESOURCE_EXHAUSTED in Junie output, retry up to 3 times with delays of 30s, 60s, 90s.

### Environment Variables
| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `qwen2.5:14b` | Model for prompt improvement |
| `VAULT_PATH` | `/mnt/vault` | Path to Obsidian vault |
| `API_KEY` | (required) | Shared secret for Bearer auth |
| `JUNIE_TIMEOUT` | `1800` | Timeout in seconds for Junie runs |
| `PORT` | `8000` | Port for uvicorn |
| `JUNIE_BIN` | (none) | Override Junie binary path (for tests) |

### File Structure
```
orchestrator.py        # Main FastAPI app
.env.example           # Environment variable template
requirements.txt       # Python dependencies
README.md              # Setup, run, Open WebUI config, systemd unit
tests/
  __init__.py
  test_orchestrator.py # Pytest tests
  fake_junie           # Mock Junie script that prints sample JSON
logs/                  # Created at runtime for runs.log
```

### API Endpoints

**GET /v1/models**
- Auth: `Authorization: Bearer <API_KEY>` required (401 otherwise)
- Returns: `{"object":"list","data":[{"id":"junie-workflow","object":"model"}]}`

**POST /v1/chat/completions**
- Auth: Bearer token required
- Request body: Standard OpenAI chat completion format with `messages` array and optional `stream` boolean
- Response (non-streaming): Standard OpenAI `chat.completion` JSON with the orchestrator's reply
- Response (streaming): Server-Sent Events with one content chunk, then a final chunk with `finish_reason: "stop"`, then `data: [DONE]`
- Bypass: If last user message starts with `### Task:`, forward to Ollama directly

### Components

**1. Auth middleware**
- Checks `Authorization: Bearer <API_KEY>` header on both endpoints
- Returns 401 with `{"error":"Unauthorized"}` if missing or wrong

**2. `build_prompt(raw)` — promptbuilder**
- Calls `POST {OLLAMA_URL}/api/chat` with `stream=false`
- System prompt rewrites rough requests into precise tasks for an Obsidian vault AI agent
- On Ollama error/unreachable: returns raw prompt, notes fallback in reply

**3. `run_junie(task)` — Junie runner**
- Runs `junie --task "<task>" --project <VAULT_PATH> --output-format json` as subprocess
- Uses `asyncio.to_thread()` in a worker thread
- Lock ensures only one Junie run at a time; extra requests get a "Junie is busy" message
- Timeout: `JUNIE_TIMEOUT` seconds
- Parses JSON output tolerantly; falls back to raw stdout/stderr
- Retries on 429/RESOURCE_EXHAUSTED: 3 attempts with 30s, 60s, 90s delays

**4. Reply formatter**
- Returns markdown: `> Improved prompt: ...` blockquote followed by `Junie: ...` answer
- Logs every request, improved prompt, and Junie exit code to `logs/runs.log`

**5. Ollama bypass**
- Detects `### Task:` prefix on last user message
- Forwards messages straight to Ollama and returns its answer
- No Junie call, no prompt improvement

### Testing Strategy
- Mock Junie via `JUNIE_BIN` env var pointing to `tests/fake_junie` script
- Fake Junie prints sample JSON to stdout
- Tests cover: auth (401), models endpoint, streaming, non-streaming, `### Task:` bypass, Ollama fallback, lock (busy rejection)
- No real Junie calls during testing


# Testing

### Validation Approach
Pytest tests using FastAPI's `TestClient` with a mock Junie script and mocked Ollama calls.

### Key Scenarios
1. **Auth**: Missing/wrong Bearer token returns 401 on both endpoints
2. **Models endpoint**: Returns `junie-workflow` model with correct structure
3. **Non-streaming chat**: Returns OpenAI-compatible response with improved prompt and Junie answer
4. **Streaming chat**: Returns SSE chunks with content, finish_reason stop, and `[DONE]`
5. **`### Task:` bypass**: Forwards to Ollama directly, no Junie call
6. **Ollama fallback**: When Ollama is unreachable, uses raw prompt and notes fallback
7. **Lock**: Second concurrent request gets "Junie is busy" message
8. **Rate limit retry**: Output containing 429 triggers retry with delays

### Mock Setup
- `tests/fake_junie`: Shell script that prints sample JSON output
- `JUNIE_BIN` env var points to fake script during tests
- Ollama calls mocked via `respx` or `httpx` transport mocking

### Edge Cases
- Empty messages array
- Junie subprocess timeout
- JSON parsing failure (falls back to raw output)
- Ollama connection refused


# Delivery Steps

### * Step 1: Phase 1: API skeleton with auth, models endpoint, and chat completions
Build the FastAPI app skeleton with authentication, `/v1/models`, and `/v1/chat/completions` (streaming and non-streaming). Include the `### Task:` bypass to Ollama. Create `requirements.txt`, `.env.example`, and the project structure.
- Create `orchestrator.py` with FastAPI app, config loading from env vars, and Bearer token auth middleware
- Implement `GET /v1/models` returning `{"object":"list","data":[{"id":"junie-workflow"}]}`
- Implement `POST /v1/chat/completions` with OpenAI-compatible request/response format
- Add streaming support (SSE) with `stream=true` parameter
- Implement `### Task:` bypass: forward to Ollama directly when detected
- Create `requirements.txt` (fastapi, uvicorn, httpx, python-dotenv, pytest, pytest-asyncio)
- Create `.env.example` with all env vars documented
- Create `tests/` directory with `fake_junie` script and initial test scaffolding
- Write tests for: auth (401), models endpoint, streaming, non-streaming, `### Task:` bypass
- Run tests and fix any failures

### ✓ Step 1: Phase 1: API skeleton with auth, models endpoint, and chat completions
### ✓ Step 2: Phase 2: Promptbuilder with Ollama integration and fallback

### ✓ Step 3: Phase 3: Junie runner with lock, timeout, retries, and JSON parsing

### ✓ Step 4: Phase 4: Reply formatting and logging

### ✓ Step 5: Phase 5: Complete test suite and README
Finalize the test suite and write a comprehensive README with setup, run instructions, systemd unit, and Open WebUI configuration.
- Ensure all tests pass: auth, models, streaming, non-streaming, bypass, Ollama fallback, lock, retries, reply format, logging
- Write `README.md` with:
  - Installation instructions (`pip install -r requirements.txt`)
  - Run command: `uvicorn orchestrator:app --host 0.0.0.0 --port 8000`
  - systemd unit example
  - Open WebUI setup: Admin Settings → Connections → OpenAI API, base URL `http://<vm-ip>:8000/v1`, API key
  - Reminder to set Open WebUI's task model to a different model in Admin Settings → Interface
  - Manual steps section: mounting NAS at VAULT_PATH, installing Ollama models, opening firewall
  - Environment variable reference table
- Verify `uvicorn orchestrator:app` starts and responds to `/v1/models` with correct auth