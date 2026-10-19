"""Single-worker execution with worktree isolation and metadata capture.

Wraps run_junie_task() to provide worker-specific metadata, worktree-based
isolation, output capture, timeout enforcement, and changed-file detection.

This module is opt-in — the existing run_junie_task() API is unchanged.
"""

import asyncio
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import OrchestratorConfig
from .models import WorkerExecution, WorkerStatus
from .worktree_manager import (
    WorktreeInfo,
    validate_git_repo,
)

runs_logger = logging.getLogger("runs")

# Default memory limit for output capture (10 MB)
DEFAULT_OUTPUT_LIMIT = 10 * 1024 * 1024


def _truncate_output(text: str, limit: int = DEFAULT_OUTPUT_LIMIT) -> str:
    """Truncate output to prevent unbounded memory growth."""
    if len(text) > limit:
        truncated = text[:limit]
        return f"{truncated}\n\n[Output truncated: {len(text)} bytes total, showing first {limit} bytes]"
    return text


async def execute_worker(
    task_description: str,
    config: OrchestratorConfig,
    worker_id: str,
    task_id: str,
    parent_task_id: Optional[str] = None,
    use_worktree: bool = True,
    timeout: Optional[int] = None,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
) -> WorkerExecution:
    """Execute a single worker task with optional worktree isolation.

    Creates a Git worktree for isolation (if available), runs Junie CLI,
    captures output and changed files, then returns a WorkerExecution result.

    Args:
        task_description: The task to execute.
        config: Orchestrator configuration.
        worker_id: Unique identifier for this worker.
        task_id: Unique identifier for this task.
        parent_task_id: Optional parent task ID for task graph tracking.
        use_worktree: Whether to use worktree isolation. Defaults to True.
        timeout: Per-task timeout in seconds. Defaults to config.junie_timeout.
        output_limit: Maximum output size in bytes. Defaults to 10 MB.

    Returns:
        WorkerExecution with full metadata about the execution.
    """
    start_time = datetime.now()
    timeout = timeout or config.junie_timeout

    # Determine the project path
    project_path = config.workspace_dir
    worktree_info: Optional[WorktreeInfo] = None
    actual_project_path = project_path

    # Create worktree for isolation if requested and Git is available
    if use_worktree and validate_git_repo(project_path):
        try:
            from .worktree_manager import create_worktree as _create_worktree
            worktree_info = _create_worktree(project_path, worker_id)
            actual_project_path = worktree_info.worktree_path
            runs_logger.info(
                f"Worker {worker_id} using worktree: {worktree_info.worktree_path}"
            )
        except RuntimeError as e:
            runs_logger.warning(
                f"Failed to create worktree for worker {worker_id}: {e}. "
                "Falling back to project directory."
            )
            actual_project_path = project_path
    elif use_worktree:
        runs_logger.warning(
            f"Worktree requested but {project_path} is not a Git repo. "
            "Running without worktree isolation."
        )

    # Temporarily override the workspace path for this execution
    original_workspace_path = config.workspace_path
    try:
        # Create a modified config with the worktree path
        from dataclasses import replace
        worker_config = replace(config, workspace_path=str(actual_project_path))

        # Run the Junie task with timeout
        try:
            from .junie_runner import run_junie_task
            result = await asyncio.wait_for(
                run_junie_task(task_description, worker_config),
                timeout=timeout,
            )

            end_time = datetime.now()
            stdout = result.answer or ""
            stderr = result.error or ""
            exit_code = 0 if result.status.value == "completed" else 1

            # Detect changed files in the worktree
            changed_files: list[str] = []
            if worktree_info:
                try:
                    from .worktree_manager import get_changed_files as _get_changed_files
                    changed_files = _get_changed_files(worktree_info.worktree_path)
                except Exception as e:
                    runs_logger.warning(f"Failed to detect changed files: {e}")

            # Determine worker status
            if result.status.value == "completed":
                worker_status = WorkerStatus.COMPLETED
            elif result.status.value == "timed_out":
                worker_status = WorkerStatus.TIMED_OUT
            else:
                worker_status = WorkerStatus.FAILED

            return WorkerExecution(
                worker_id=worker_id,
                task_id=task_id,
                parent_task_id=parent_task_id,
                task_description=task_description,
                project_path=str(project_path),
                worktree_path=str(worktree_info.worktree_path) if worktree_info else None,
                status=worker_status,
                pid=None,  # PID is captured by the subprocess, not exposed here
                start_time=start_time,
                end_time=end_time,
                stdout=_truncate_output(stdout, output_limit),
                stderr=_truncate_output(stderr, output_limit),
                exit_code=exit_code,
                result_summary=result.answer[:500] if result.answer else None,
                changed_files=changed_files,
                error=result.error,
            )

        except asyncio.TimeoutError:
            end_time = datetime.now()
            runs_logger.error(
                f"Worker {worker_id} timed out after {timeout}s"
            )
            return WorkerExecution(
                worker_id=worker_id,
                task_id=task_id,
                parent_task_id=parent_task_id,
                task_description=task_description,
                project_path=str(project_path),
                worktree_path=str(worktree_info.worktree_path) if worktree_info else None,
                status=WorkerStatus.TIMED_OUT,
                pid=None,
                start_time=start_time,
                end_time=end_time,
                stdout="",
                stderr=f"Worker timed out after {timeout}s",
                exit_code=None,
                result_summary=None,
                changed_files=[],
                error=f"Timeout after {timeout}s",
            )

    finally:
        # Restore original config
        config.workspace_path = original_workspace_path

        # Clean up worktree — keep it for review by default, but log its location
        if worktree_info:
            runs_logger.info(
                f"Worker {worker_id} completed. Worktree preserved at: "
                f"{worktree_info.worktree_path} for review."
            )


async def execute_worker_and_cleanup(
    task_description: str,
    config: OrchestratorConfig,
    worker_id: str,
    task_id: str,
    parent_task_id: Optional[str] = None,
    use_worktree: bool = True,
    timeout: Optional[int] = None,
    output_limit: int = DEFAULT_OUTPUT_LIMIT,
) -> WorkerExecution:
    """Execute a worker task and automatically clean up the worktree.

    Similar to execute_worker but removes the worktree after execution.
    Use this when you don't need to preserve the worktree for review.

    Args:
        task_description: The task to execute.
        config: Orchestrator configuration.
        worker_id: Unique identifier for this worker.
        task_id: Unique identifier for this task.
        parent_task_id: Optional parent task ID.
        use_worktree: Whether to use worktree isolation.
        timeout: Per-task timeout in seconds.
        output_limit: Maximum output size in bytes.

    Returns:
        WorkerExecution with full metadata.
    """
    result = await execute_worker(
        task_description=task_description,
        config=config,
        worker_id=worker_id,
        task_id=task_id,
        parent_task_id=parent_task_id,
        use_worktree=use_worktree,
        timeout=timeout,
        output_limit=output_limit,
    )

    # Clean up worktree if it was created
    if result.worktree_path:
        try:
            from .worktree_manager import remove_worktree as _remove_worktree
            _remove_worktree(Path(result.worktree_path))
            runs_logger.info(f"Cleaned up worktree for worker {worker_id}")
        except Exception as e:
            runs_logger.warning(
                f"Failed to clean up worktree for worker {worker_id}: {e}"
            )

    return result
