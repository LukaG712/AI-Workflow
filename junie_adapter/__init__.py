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
- Stable adapter interface for AgentHub integration

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

AgentHub adapter usage:
    from junie_adapter import JunieAdapter, AdapterRequest, AdapterResult

    adapter = JunieAdapter(api_key="your-api-key", workspace_path="/path/to/project")
    request = AdapterRequest(task="Refactor module X", project_path="/path/to/project")
    result: AdapterResult = await adapter.submit_task(request)
"""

from .config import IsolationMode, OrchestratorConfig
from .junie_runner import run_junie_task
from .prompt_refiner import refine_prompt
from .session_logger import log_session
from .memory_loader import load_context
from .models import RunResult, SessionLog, TaskStatus, WorkerExecution, WorkerStatus
from .worker_executor import (
    execute_worker,
    execute_worker_and_cleanup,
    WorkerRegistry,
    WorkerSemaphore,
    get_worker_registry,
    set_max_concurrent_workers,
)
from .worktree_manager import (
    WorktreeInfo,
    create_worktree,
    get_changed_files,
    get_change_summary,
    get_worker_diff,
    remove_worktree,
    validate_git_repo,
)
from .context_provider import (
    ContextProvider,
    EmptyContextProvider,
    MemoryFileContextProvider,
    VaultMapContextProvider,
    CompositeContextProvider,
    create_default_context_provider,
)
from .adapter import (
    AdapterRequest,
    AdapterResult,
    JunieAdapter,
)

__all__ = [
    # Config
    "OrchestratorConfig",
    "IsolationMode",
    # Core execution
    "run_junie_task",
    "refine_prompt",
    "log_session",
    "load_context",
    # Models
    "RunResult",
    "SessionLog",
    "TaskStatus",
    "WorkerExecution",
    "WorkerStatus",
    # Worker execution
    "execute_worker",
    "execute_worker_and_cleanup",
    "WorkerRegistry",
    "WorkerSemaphore",
    "get_worker_registry",
    "set_max_concurrent_workers",
    # Worktree management
    "WorktreeInfo",
    "create_worktree",
    "remove_worktree",
    "validate_git_repo",
    "get_changed_files",
    "get_change_summary",
    "get_worker_diff",
    # Context providers
    "ContextProvider",
    "EmptyContextProvider",
    "MemoryFileContextProvider",
    "VaultMapContextProvider",
    "CompositeContextProvider",
    "create_default_context_provider",
    # Adapter interface
    "AdapterRequest",
    "AdapterResult",
    "JunieAdapter",
]
