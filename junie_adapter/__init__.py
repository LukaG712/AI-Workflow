"""Junie Adapter — a modular Python package for integrating Junie CLI execution.

This package provides:
- Junie CLI execution with subprocess management
- Optional Ollama-based prompt refinement
- Session logging and persistence
- Memory/context loading
- Optional Obsidian vault integration
- An optional FastAPI HTTP service
- Multi-agent worker execution with Git worktree isolation (opt-in)
- Pluggable context provider interface
- Stable adapter interface for Evershade Valley integration

Usage:
    from junie_adapter import run_junie_task, OrchestratorConfig
    from junie_adapter.models import RunResult

    config = OrchestratorConfig.from_env()
    result: RunResult = await run_junie_task("Implement feature X", config)

Multi-agent usage:
    from junie_adapter import execute_worker, WorkerExecution, WorkerStatus
    from junie_adapter import create_worktree, remove_worktree

    worker_result: WorkerExecution = await execute_worker(
        task_description="Implement feature X",
        config=config,
        worker_id="worker-1",
        task_id="task-1",
    )

Evershade Valley adapter usage:
    from junie_adapter import JunieAdapter, AdapterRequest, AdapterResult

    adapter = JunieAdapter(api_key="your-api-key", workspace_path="/path/to/project")
    request = AdapterRequest(task="Refactor module X", project_path="/path/to/project")
    result: AdapterResult = await adapter.submit_task(request)
"""

from .adapter import (
    AdapterRequest,
    AdapterResult,
    JunieAdapter,
)
from .config import IsolationMode, OrchestratorConfig
from .context_provider import (
    CompositeContextProvider,
    ContextProvider,
    EmptyContextProvider,
    MemoryFileContextProvider,
    VaultMapContextProvider,
    create_default_context_provider,
)
from .junie_runner import run_junie_task
from .memory_loader import load_context
from .models import RunResult, SessionLog, TaskStatus, WorkerExecution, WorkerStatus
from .persistent_store import (
    PersistentStore,
    ProjectRecord,
    TaskRecord,
)
from .prompt_refiner import refine_prompt
from .session_logger import log_session
from .worker_executor import (
    WorkerRegistry,
    WorkerSemaphore,
    execute_worker,
    execute_worker_and_cleanup,
    get_worker_registry,
    set_max_concurrent_workers,
)
from .worktree_manager import (
    WorktreeInfo,
    create_worktree,
    get_change_summary,
    get_changed_files,
    get_worker_diff,
    remove_worktree,
    validate_git_repo,
)

__all__ = [
    "AdapterRequest",
    "AdapterResult",
    "CompositeContextProvider",
    "ContextProvider",
    "EmptyContextProvider",
    "IsolationMode",
    "JunieAdapter",
    "MemoryFileContextProvider",
    "OrchestratorConfig",
    "PersistentStore",
    "ProjectRecord",
    "RunResult",
    "SessionLog",
    "TaskRecord",
    "TaskStatus",
    "VaultMapContextProvider",
    "WorkerExecution",
    "WorkerRegistry",
    "WorkerSemaphore",
    "WorkerStatus",
    "WorktreeInfo",
    "create_default_context_provider",
    "create_worktree",
    "execute_worker",
    "execute_worker_and_cleanup",
    "get_change_summary",
    "get_changed_files",
    "get_worker_diff",
    "get_worker_registry",
    "load_context",
    "log_session",
    "refine_prompt",
    "remove_worktree",
    "run_junie_task",
    "set_max_concurrent_workers",
    "validate_git_repo",
]
