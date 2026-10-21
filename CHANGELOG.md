# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/),
and this project adheres to [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-19

### Added
- **Git worktree isolation** — Each worker runs in its own isolated Git branch, preventing interference with the main checkout or other workers.
- **Stable adapter interface** — Clean typed API (`JunieAdapter`) for Evershade Valley integration without importing private details.
- **Comprehensive changed-file detection** — Reports modified, added, deleted, renamed, staged, unstaged, and untracked files with diff content.
- **Pluggable context providers** — Load context from MEMORY.md, session logs, vault maps, or custom sources via the `ContextProvider` protocol.
- **Honest streaming** — SSE events clearly distinguish progress updates from token output.
- **Configurable concurrency** — Default `max_concurrent_workers=1`, with optional override.
- **Junie CLI subprocess execution** — Subprocess management with JSON parsing, rate-limit retries, and timeout handling.
- **Optional Ollama prompt refinement** — Opt-in prompt rewriting via Ollama API (disabled by default).
- **Session logging and persistence** — Save session logs to disk with optional Ollama summary generation.
- **MEMORY.md context loading** — Load project memory and session log context for informed task execution.
- **Optional Obsidian vault integration** — Mirror session logs to an Obsidian vault (fully optional dependency).
- **FastAPI HTTP service** — OpenAI-compatible `/v1/chat/completions` endpoint with honest SSE streaming.
- **Worker execution with isolation and concurrency** — PID tracking, `WorkerRegistry`, and semaphore-based concurrency control.
- **Python module API** — Import and use directly via `from junie_adapter import JunieAdapter, AdapterRequest`.
- **Command-line entry point** — `junie-orchestrator` script for starting the HTTP service.

### Notes
- Optional dependencies (Obsidian, Ollama) are fully optional; the service starts without them.
- Requires Python >= 3.10.
- Designed for use with [Evershade Valley](https://github.com/LukaG712/Evershade Valley) as a coding-agent execution backend, but also works as a standalone HTTP service compatible with Open WebUI.
