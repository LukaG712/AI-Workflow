from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum


class TaskStatus(Enum):
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    INTERRUPTED = "interrupted"


class WorkerStatus(Enum):
    """Status of a worker execution task."""

    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INTERRUPTED = "interrupted"


@dataclass
class RunResult:
    task: str
    answer: str
    status: TaskStatus
    error: str | None = None
    duration_seconds: float = 0.0


@dataclass
class SessionLog:
    raw_prompt: str
    refined_prompt: str | None = None
    answer: str = ""
    status: TaskStatus = TaskStatus.COMPLETED
    timestamp: datetime = field(default_factory=datetime.now)
    error: str | None = None


@dataclass
class WorkerExecution:
    """Represents a single worker task execution with full metadata.

    Extends RunResult with worker-specific fields for multi-agent orchestration.
    """

    worker_id: str
    task_id: str
    parent_task_id: str | None
    task_description: str
    project_path: str
    worktree_path: str | None
    status: WorkerStatus
    pid: int | None
    start_time: datetime
    end_time: datetime | None
    stdout: str
    stderr: str
    exit_code: int | None
    result_summary: str | None
    changed_files: list[str]
    error: str | None = None
