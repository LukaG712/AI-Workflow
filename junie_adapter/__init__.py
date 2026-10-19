"""Junie Adapter — a modular Python package for integrating Junie CLI execution.

This package provides:
- Junie CLI execution with subprocess management
- Optional Ollama-based prompt refinement
- Session logging and persistence
- Memory/context loading
- Optional Obsidian vault integration
- An optional FastAPI HTTP service
- Multi-agent worker execution with Git worktree isolation (opt-in)

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
"""

from .config import OrchestratorConfig
from .junie_runner import run_junie_task
from .prompt_refiner import refine_prompt
from .session_logger import log_session
from .memory_loader import load_context
from .models import RunResult, SessionLog, TaskStatus, WorkerExecution, WorkerStatus
from .worker_executor import execute_worker, execute_worker_and_cleanup
from .worktree_manager import create_worktree, remove_worktree, validate_git_repo

__all__ = [
    "OrchestratorConfig",
    "run_junie_task",
    "refine_prompt",
    "log_session",
    "load_context",
    "RunResult",
    "SessionLog",
    "TaskStatus",
    "WorkerExecution",
    "WorkerStatus",
    "execute_worker",
    "execute_worker_and_cleanup",
    "create_worktree",
    "remove_worktree",
    "validate_git_repo",
]
