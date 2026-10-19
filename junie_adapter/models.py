from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import List, Optional


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
    error: Optional[str] = None
    duration_seconds: float = 0.0


@dataclass
class SessionLog:
    raw_prompt: str
    refined_prompt: Optional[str] = None
    answer: str = ""
    status: TaskStatus = TaskStatus.COMPLETED
    timestamp: datetime = field(default_factory=datetime.now)
    error: Optional[str] = None


@dataclass
class WorkerExecution:
    """Represents a single worker task execution with full metadata.

    Extends RunResult with worker-specific fields for multi-agent orchestration.
    """
    worker_id: str
    task_id: str
    parent_task_id: Optional[str]
    task_description: str
    project_path: str
    worktree_path: Optional[str]
    status: WorkerStatus
    pid: Optional[int]
    start_time: datetime
    end_time: Optional[datetime]
    stdout: str
    stderr: str
    exit_code: Optional[int]
    result_summary: Optional[str]
    changed_files: List[str]
    error: Optional[str] = None
