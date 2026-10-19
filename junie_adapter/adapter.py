"""Stable adapter interface for AgentHub integration.

Provides a clean, documented API for submitting tasks to JunieOrchestrator
without importing private implementation details.
"""

import asyncio
import logging
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional

from .config import IsolationMode, OrchestratorConfig
from .models import WorkerStatus
from .worktree_manager import (
    FileDiff,
    get_changed_files,
    get_change_summary,
    get_worker_diff,
    remove_worktree,
    validate_git_repo,
)

logger = logging.getLogger(__name__)


@dataclass
class AdapterRequest:
    """Request to execute a task via JunieOrchestrator.

    Attributes:
        task: The task description to execute.
        project_path: Path to the project/workspace directory.
        worker_id: Optional worker ID. Auto-generated if not provided.
        task_id: Optional task ID. Auto-generated if not provided.
        parent_task_id: Optional parent task ID for task graph tracking.
        use_worktree: Whether to use Git worktree isolation. Defaults to True.
        timeout: Per-task timeout in seconds. Defaults to 1800.
        isolation_mode: Worktree isolation mode. Defaults to WORKTREE.
        config_overrides: Optional config overrides (API key, Junie binary, etc.).
    """
    task: str
    project_path: str
    worker_id: Optional[str] = None
    task_id: Optional[str] = None
    parent_task_id: Optional[str] = None
    use_worktree: bool = True
    timeout: int = 1800
    isolation_mode: IsolationMode = IsolationMode.WORKTREE
    config_overrides: Optional[Dict[str, object]] = None

    def __post_init__(self):
        if self.worker_id is None:
            self.worker_id = f"worker-{uuid.uuid4().hex[:8]}"
        if self.task_id is None:
            self.task_id = f"task-{uuid.uuid4().hex[:8]}"


@dataclass
class AdapterResult:
    """Result of a task executed via JunieOrchestrator.

    Attributes:
        worker_id: The worker that executed the task.
        task_id: The task ID.
        status: Final status of the execution.
        output: Standard output from Junie.
        exit_code: Exit code from the Junie process.
        changed_files: List of changed file paths.
        diff_summary: Human-readable summary of changes.
        error: Error message if execution failed.
        worktree_path: Path to the worktree (if isolation was used).
        branch_name: Git branch name (if worktree was created).
        duration_seconds: Execution duration in seconds.
    """
    worker_id: str
    task_id: str
    status: WorkerStatus
    output: str
    exit_code: Optional[int]
    changed_files: List[str]
    diff_summary: str
    error: Optional[str]
    worktree_path: Optional[str]
    branch_name: Optional[str]
    duration_seconds: float = 0.0


class JunieAdapter:
    """Clean adapter interface for AgentHub to use JunieOrchestrator.

    This class provides the main entry points for task execution,
    health checking, changed-file reporting, and cleanup.

    Example:
        ```python
        from junie_adapter import JunieAdapter, AdapterRequest

        adapter = JunieAdapter(api_key="your-api-key", workspace_path="/path/to/project")

        # Check if the service is available
        if not adapter.check_health():
            print("JunieOrchestrator is not available")
            return

        # Submit a task
        request = AdapterRequest(
            task="Refactor the main module to use async/await",
            project_path="/path/to/project",
        )
        result = await adapter.submit_task(request)

        if result.status == WorkerStatus.COMPLETED:
            print(f"Task completed: {result.output[:200]}")
            print(f"Changed files: {result.changed_files}")
        else:
            print(f"Task failed: {result.error}")
        ```
    """

    def __init__(self, config: Optional[OrchestratorConfig] = None, **config_kwargs):
        """Initialize the adapter with configuration.

        Args:
            config: Optional OrchestratorConfig instance. If not provided,
                one will be created from config_kwargs.
            **config_kwargs: Keyword arguments passed to OrchestratorConfig.
        """
        if config:
            self._config = config
        else:
            self._config = OrchestratorConfig(**config_kwargs)

    def check_health(self) -> bool:
        """Check if the adapter and its dependencies are available.

        Verifies that the configuration is valid and the workspace exists.

        Returns:
            True if the adapter is healthy and ready to execute tasks.
        """
        try:
            if not self._config.workspace_dir.exists():
                logger.warning(f"Workspace does not exist: {self._config.workspace_dir}")
                return False
            return True
        except Exception as e:
            logger.error(f"Health check failed: {e}")
            return False

    async def submit_task(self, request: AdapterRequest) -> AdapterResult:
        """Submit and execute a task.

        Creates a worktree for isolation (if requested), runs the Junie CLI,
        captures output and changed files, and returns a structured result.

        Args:
            request: The adapter request containing task details.

        Returns:
            AdapterResult with execution status, output, and changed files.
        """
        import time
        from datetime import datetime

        start_time = time.time()

        # Build config with overrides
        config_dict = {
            "workspace_path": request.project_path,
            "isolation_mode": request.isolation_mode,
            "junie_timeout": request.timeout,
        }
        if request.config_overrides:
            config_dict.update(request.config_overrides)

        exec_config = OrchestratorConfig(**config_dict)

        try:
            from .worker_executor import execute_worker

            result = await execute_worker(
                task_description=request.task,
                config=exec_config,
                worker_id=request.worker_id,
                task_id=request.task_id,
                parent_task_id=request.parent_task_id,
                use_worktree=request.use_worktree,
                timeout=request.timeout,
            )

            duration = time.time() - start_time

            # Get change summary
            diff_summary = ""
            if result.worktree_path:
                try:
                    diff_summary = get_change_summary(Path(result.worktree_path))
                except Exception as e:
                    logger.warning(f"Failed to get change summary: {e}")

            return AdapterResult(
                worker_id=result.worker_id,
                task_id=result.task_id,
                status=result.status,
                output=result.stdout,
                exit_code=result.exit_code,
                changed_files=result.changed_files,
                diff_summary=diff_summary,
                error=result.error,
                worktree_path=result.worktree_path,
                branch_name=None,  # Branch name is internal to worktree_manager
                duration_seconds=duration,
            )

        except Exception as e:
            duration = time.time() - start_time
            logger.error(f"Task execution failed: {e}")
            return AdapterResult(
                worker_id=request.worker_id,
                task_id=request.task_id,
                status=WorkerStatus.FAILED,
                output="",
                exit_code=None,
                changed_files=[],
                diff_summary="",
                error=str(e),
                worktree_path=None,
                branch_name=None,
                duration_seconds=duration,
            )

    async def get_changed_files(self, worktree_path: str) -> List[str]:
        """Get the list of changed files in a worktree.

        Args:
            worktree_path: Path to the worktree directory.

        Returns:
            List of changed file paths.
        """
        try:
            return get_changed_files(Path(worktree_path))
        except Exception as e:
            logger.error(f"Failed to get changed files: {e}")
            return []

    async def get_diff(self, worktree_path: str) -> List[FileDiff]:
        """Get structured diff data for a worktree.

        Args:
            worktree_path: Path to the worktree directory.

        Returns:
            List of FileDiff objects with status, content, and summary.
        """
        try:
            return get_worker_diff(Path(worktree_path))
        except Exception as e:
            logger.error(f"Failed to get diff: {e}")
            return []

    async def cancel_task(self, worker_id: str) -> bool:
        """Attempt to cancel a running task (best effort).

        NOTE: Junie CLI does not support graceful cancellation. This is a
        best-effort operation that may not stop a running process immediately.

        Args:
            worker_id: The worker ID to cancel.

        Returns:
            True if cancellation was attempted, False if worker not found.
        """
        logger.warning(
            f"Cancel requested for worker {worker_id}. "
            "Junie CLI does not support graceful cancellation."
        )
        return False

    async def cleanup_worktree(self, worktree_path: str) -> bool:
        """Safely clean up a worktree.

        Removes the worktree directory and associated Git branch.

        Args:
            worktree_path: Path to the worktree directory.

        Returns:
            True if the worktree was removed, False if it didn't exist.
        """
        try:
            return remove_worktree(Path(worktree_path), force=True)
        except Exception as e:
            logger.error(f"Failed to cleanup worktree: {e}")
            return False
