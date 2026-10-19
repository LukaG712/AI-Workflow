# Multi-Agent Junie Lite Feasibility Report

## Executive Summary

This report assesses whether multiple independent Junie CLI processes can be orchestrated externally (by AgentHub) to collaborate on the same software project. Based on investigation of the installed Junie CLI (v26.10.12) and official documentation, **limited multi-agent execution is feasible**, but with significant constraints that require sequential execution by default.

**Status**: Implemented. The adapter now provides worktree isolation, concurrency limits, a `WorkerRegistry` for active worker tracking, and a clean `JunieAdapter` interface. Default concurrency is 1 (JUNIE-3394). All changes are tested (114 tests pass, 1 skipped).

---

## 1. Can Junie Lite be launched through the required CLI mode?

**Answer: Yes — fully supported.**

The installed Junie CLI (version 26.10.12, build 3659.7) provides complete headless execution capabilities:

### Verified CLI Flags

| Flag | Purpose | Status |
|------|---------|--------|
| `--task <text>` | Pass a task description | ✅ Confirmed |
| `--project <path>` | Specify the project directory | ✅ Confirmed |
| `--output-format json` | Structured JSON output | ✅ Confirmed |
| `--output-format json-stream` | Streaming JSON output | ✅ Confirmed |
| `--input-format text` | Plain text input | ✅ Confirmed |
| `--input-format json` | JSON input | ✅ Confirmed |
| `--auth <token>` | Authentication token | ✅ Confirmed |
| `--model <name>` | Specify the model | ✅ Confirmed |
| `--provider <name>` | Specify the provider (BYOK) | ✅ Confirmed |
| `--gateway` | Run as a headless gateway worker | ✅ Confirmed |
| `--verbose` | Stream transcript to stdout | ✅ Confirmed |
| `--session-id <id>` | Follow up on a previous session | ✅ Confirmed |
| `--resume` | Resume a previous session | ✅ Confirmed |

### Authentication Methods

1. **Token-based**: `--auth <token>` where the token is obtained from `junie.jetbrains.com/cli`.
2. **BYOK (Bring Your Own Key)**: Provider-specific flags like `--openai-api-key`, `--anthropic-api-key`, etc.

### Gateway Mode

The `--gateway` flag runs Junie as a headless worker executing commands from a gateway source (e.g., cron jobs). With `--verbose`, it streams the full transcript to process output. This is designed for scheduled and remote tasks.

**Conclusion**: Headless execution is production-ready. The existing `junie_runner.py` module already uses these flags correctly.

---

## 2. Can multiple independent Junie Lite processes run simultaneously?

**Answer: Limited — one active session per project is enforced.**

### Confirmed Restriction (JUNIE-3394)

A known limitation documented in JetBrains YouTrack as **JUNIE-3394** states:

> "Junie CLI currently allows only **one active remote session per project**, and that restriction spans every git worktree of the repository."

This means:
- Two independent `junie --task` processes targeting the same project root will conflict.
- Using different Git worktrees does **not** bypass this restriction, as the session lock is tied to the repository, not the working directory.
- The restriction applies regardless of authentication method.

### Unverified Assumptions

- **BYOK multi-process behavior**: It is unverified whether using different authentication tokens (different accounts) might allow concurrent sessions. This would need empirical testing.
- **Session isolation**: Sessions are stored in `~/.junie/sessions/`. It is unclear whether separate session IDs can coexist for the same project.

### Practical Implications

1. **Sequential execution is the safe default**: Workers must run one at a time for a given project.
2. **Worktree isolation still provides value**: Even with sequential execution, worktrees give each worker a clean, isolated branch for its changes — making result review and merge easier.
3. **True parallelism requires verification**: The adapter should allow users to override the sequential default, but must document the JUNIE-3394 risk.

**Conclusion**: Simultaneous processes are not reliably supported. The adapter defaults to `max_concurrent_workers=1` but allows configuration overrides.

---

## 3. Are built-in subagents available for Junie Lite?

**Answer: Yes, but they are internal to a single Junie session.**

### How Subagents Work

Junie CLI supports subagents that the main agent can delegate tasks to. However:

- Subagents are **managed internally** by the main Junie session.
- They are **not exposed as independent CLI processes**.
- The main agent decides when and how to delegate to subagents automatically.
- Subagent mode is configurable via `/settings` → Subagents (SameModelOnly vs Auto mode).

### Relevance to External Orchestration

Built-in subagents cannot be used for external multi-agent orchestration because:
- AgentHub cannot directly invoke or control a subagent.
- Subagent lifecycle is managed by the parent Junie session.
- There's no CLI flag to spawn an independent subagent process.

**Conclusion**: Built-in subagents are an internal Junie feature, not a mechanism for external orchestration. AgentHub must orchestrate multiple independent Junie CLI invocations.

---

## 4. What are the known rate limits, concurrency constraints, and authentication restrictions?

### Concurrency Constraints

| Constraint | Description | Impact |
|------------|-------------|--------|
| JUNIE-3394 | One active session per project | Blocks true parallel workers |
| Rate limiting | HTTP 429 errors on API calls | Handled by existing retry logic in `junie_runner.py` |
| Session storage | `~/.junie/sessions/` per user | Shared across processes |

### Rate Limiting

The existing `junie_runner.py` already handles rate limiting:
- Detects HTTP 429 (Too Many Requests) responses.
- Implements exponential backoff retry logic.
- This is important for multi-agent scenarios where multiple workers might hit rate limits.

### Authentication Restrictions

- Each Junie CLI process requires its own authentication token.
- Token-based auth (`--auth`) uses a single token for all workers.
- BYOK auth allows per-worker provider keys.
- Sessions are stored per-user in `~/.junie/sessions/`.

### Resource Limits

- No documented limit on the number of concurrent Junie processes across different projects.
- The one-session-per-project restriction is the primary constraint.

**Conclusion**: Rate limiting is already handled. The one-session-per-project restriction is the main concurrency blocker. Authentication requires per-process tokens.

---

## 5. Which orchestration responsibilities must be implemented externally?

Since Junie CLI does not provide external orchestration primitives, **all** of the following must be implemented by AgentHub:

### Required External Components

1. **Task Graph Management**
   - Define tasks, subtasks, and dependencies.
   - Determine execution order based on dependency resolution.
   - Prevent tasks from starting before prerequisites complete.

2. **Worker Scheduling**
   - Decide which worker runs which task.
   - Enforce concurrency limits (default: 1 per project).
   - Handle worker queueing and prioritization.

3. **Result Validation**
   - Verify individual worker outputs.
   - Run tests or validation checks on worker changes.
   - Do not trust worker self-reports; obtain actual execution results.

4. **Merge Coordination**
   - Decide when and how to integrate worker changes.
   - Handle Git conflicts between worker branches.
   - Provide review mechanisms before merging.

5. **Cleanup Policy**
   - Determine when worktrees can be safely removed.
   - Handle leftover worktrees from crashed runs.
   - Manage branch lifecycle.

6. **Error Recovery**
   - Detect worker failures and timeouts.
   - Implement retry logic with backoff.
   - Support resuming interrupted tasks.

7. **Context Passing**
   - Pass relevant project context to each worker.
   - Share interface definitions and contracts between workers.
   - Ensure workers have clear scope boundaries.

### What the Adapter Provides

The `junie_adapter` package provides:
- Single-worker execution with metadata capture (`worker_executor.py`).
- Active worker tracking via `WorkerRegistry` for monitoring and cancellation.
- Concurrency enforcement via `WorkerSemaphore` with configurable limits.
- Git worktree lifecycle management (`worktree_manager.py`).
- Worker execution models (`WorkerExecution`, `WorkerStatus`).
- Output capture, timeout enforcement, and changed-file detection.
- Pluggable context loading via `ContextProvider` interface (`context_provider.py`).
- Clean typed adapter interface (`JunieAdapter`) for AgentHub integration.

**Conclusion**: AgentHub owns orchestration policy. The adapter provides execution and isolation primitives.

---

## 6. What is the simplest reliable proof of concept?

### Recommended Proof of Concept

The simplest reliable workflow is **sequential workers with worktree isolation**:

```
1. AgentHub receives a high-level task.
2. AgentHub splits the task into independent subtasks.
3. For each subtask (sequentially):
   a. Create a Git worktree with a unique branch.
   b. Launch Junie CLI in the worktree directory.
   c. Capture output, exit code, and changed files.
   d. Record the WorkerExecution result.
   e. Keep the worktree for review (do not auto-merge).
4. AgentHub reviews all worker results.
5. AgentHub decides which changes to merge.
```

### Why This Works

- **No parallelism conflicts**: Sequential execution avoids JUNIE-3394.
- **Clean isolation**: Each worker gets its own branch and worktree.
- **Reviewable results**: Changes are preserved for manual or automated review.
- **Recoverable**: Worktrees persist until explicitly cleaned up.
- **Backward compatible**: Single-agent execution path is unchanged.

### Example Scenario

```
Task: "Implement user authentication feature"

Subtask A: "Create authentication API endpoints"
  → Worktree: junie-worker-A-20261018
  → Branch: feature/auth-api
  → Result: 5 files changed, tests passing

Subtask B: "Create frontend login form"
  → Worktree: junie-worker-B-20261018
  → Branch: feature/auth-frontend
  → Result: 3 files changed, depends on API contract

Subtask C: "Write integration tests for auth flow"
  → Worktree: junie-worker-C-20261018
  → Branch: test/auth-integration
  → Result: 2 test files added

AgentHub reviews all three branches, resolves any conflicts, and merges.
```

**Conclusion**: Sequential worktree-based execution is the proven, reliable approach. It provides isolation and reviewability without requiring unsupported parallel features.

---

## Summary of Findings

| Question | Answer | Confidence |
|----------|--------|------------|
| CLI headless mode | Fully supported | ✅ Confirmed |
| Simultaneous processes | Limited by JUNIE-3394 | ✅ Confirmed |
| Built-in subagents | Internal only, not external | ✅ Confirmed |
| Rate limits | Handled, one session/project | ✅ Confirmed |
| External orchestration | All policy must be external | ✅ Confirmed |
| Simplest PoC | Sequential + worktree isolation | ✅ Recommended |

## Recommendations

1. **Default to sequential execution** with `max_concurrent_workers=1`.
2. **Use worktree isolation** for clean, reviewable results.
3. **Document JUNIE-3394** clearly for users who attempt parallel execution.
4. **Allow configuration overrides** for users who want to test parallel execution.
5. **Keep the feature opt-in** — existing single-agent behavior is unchanged.
6. **Verify BYOK behavior** in a future iteration to determine if parallel execution is possible with separate auth tokens.

## Implementation Status

The following features have been implemented based on this feasibility analysis:

### Completed Features

| Feature | Status | Notes |
|---------|--------|-------|
| Worktree isolation | ✅ Implemented | Real Git branches (not detached HEAD), collision handling, safe cleanup |
| Changed-file detection | ✅ Implemented | Modified, added, deleted, renamed, staged, unstaged, untracked files |
| Diff reporting | ✅ Implemented | `get_worker_diff()` and `get_change_summary()` for structured diff data |
| Worker execution hardening | ✅ Implemented | Config mutation fix, timeout with `TIMED_OUT` status, PID capture |
| Concurrency limits | ✅ Implemented | `max_concurrent_workers` config (default 1), semaphore-based enforcement |
| Isolation modes | ✅ Implemented | `WORKTREE`, `NONE`, `REQUIRED` via `IsolationMode` enum |
| Context providers | ✅ Implemented | Pluggable `ContextProvider` protocol with 4 implementations |
| Adapter interface | ✅ Implemented | `JunieAdapter` class with typed `AdapterRequest`/`AdapterResult` models |
| Honest streaming | ✅ Implemented | Distinct SSE event types, post-completion streaming documented |
| Service hardening | ✅ Implemented | Lifespan handler, busy flag, proper error responses (503/504) |
| Configuration fixes | ✅ Implemented | No workspace fallback to VAULT_PATH, workspace validation |
| Optional dependencies | ✅ Implemented | Obsidian and Ollama are truly optional |
| Worker registry | ✅ Implemented | `WorkerRegistry` tracks active workers, supports monitoring and cancellation |
| Memory loader migration | ✅ Implemented | `memory_loader.py` now delegates to `ContextProvider` interface |

### Test Coverage

- **114 tests pass** (1 skipped), covering:
  - Worktree creation with real Git branches
  - Branch collision handling
  - Changed-file detection (all categories: modified, added, deleted, renamed, staged, unstaged, untracked)
  - Worker execution (success, failure, timeout, non-Git repo handling)
  - Configuration validation (workspace paths, isolation modes)
  - Streaming event correctness (event order, busy handling)
  - Adapter interface compatibility (`AdapterRequest`, `AdapterResult`, `JunieAdapter`)
  - Context provider implementations (all four providers and composite)
  - Worker registry (registration, completion, active tracking)
  - Concurrency limits (`WorkerSemaphore` enforcement)
- All tests use mocks and fake Junie — no live Junie or Ollama required.

### Architecture

```
AgentHub (orchestration)
  ↓ AdapterRequest
JunieAdapter (adapter.py)
  ↓ submit_task()
WorkerExecutor (worker_executor.py)
  ├─→ WorkerRegistry (active worker tracking)
  ├─→ WorkerSemaphore (concurrency limits)
  ↓ create_worktree()
WorktreeManager (worktree_manager.py)
  ↓ git worktree add -b <branch>
Git Repository
  ↓ run_junie_task()
JunieRunner (junie_runner.py)
  ↓ subprocess
Junie CLI
```

### Known Limitations

1. **JUNIE-3394**: One active session per project. Default concurrency = 1. Parallel workers targeting the same project will conflict.
2. **Streaming is post-completion**: Junie CLI does not support reliable incremental output. SSE events clearly distinguish progress from token output.
3. **Windows Junie CLI**: Junie CLI execution requires Linux or macOS. The Python package and worktree isolation work on all platforms.
4. **BYOK multi-process behavior**: Unverified whether different auth tokens allow concurrent sessions. Needs empirical testing.

### Recommended Usage Pattern

```python
from junie_adapter import JunieAdapter, AdapterRequest

adapter = JunieAdapter(api_key="your-key", workspace_path="/path/to/project")

# Sequential multi-worker execution
tasks = ["Task A", "Task B", "Task C"]
results = []
for task in tasks:
    request = AdapterRequest(task=task, project_path="/path/to/project")
    result = await adapter.submit_task(request)
    results.append(result)

# Review all results before cleanup
for r in results:
    print(f"{r.worker_id}: {r.status} — {r.diff_summary}")
```

## References

- Junie CLI version: 26.10.12 (build 3659.7)
- YouTrack issue: JUNIE-3394 (one session per project restriction)
- Junie documentation: https://docs.jetbrains.com/junie
- Session storage: `~/.junie/sessions/`
