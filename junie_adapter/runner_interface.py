"""Pluggable runner interface for Slinker.

Introduces a runner interface so that:
- Junie remains the default runner
- Other runners (e.g., Ollama-backed local model runner) can be added
- Each runner declares its capabilities, required binaries, and configuration
- Every runner executes inside the same sandbox, resource limits, and approval policy
- Runner-specific logic does not leak into workflow definitions
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class IsolationLevel(str, Enum):
    """Isolation levels for agent execution."""
    WORKTREE = "worktree"  # Git worktree only, NOT a security boundary
    CONTAINER = "container"  # Hardened container
    WORKSTATION = "workstation"  # Full isolated workstation


@dataclass
class RunnerCapabilities:
    """Declares what a runner can do."""
    can_execute_code: bool = True
    can_browse_web: bool = False
    can_use_tools: bool = False
    can_stream_output: bool = False
    supports_isolation: list[IsolationLevel] = field(default_factory=lambda: [IsolationLevel.WORKTREE])
    max_concurrent_tasks: int = 1


@dataclass
class RunnerRequirements:
    """Required binaries and configuration for a runner."""
    binaries: list[str] = field(default_factory=list)
    env_vars: list[str] = field(default_factory=list)
    min_version: Optional[str] = None


@dataclass
class TaskRequest:
    """A task to be executed by a runner."""
    prompt: str
    context: dict = field(default_factory=dict)
    repository_path: Optional[str] = None
    branch: Optional[str] = None
    isolation_level: IsolationLevel = IsolationLevel.WORKTREE
    timeout_seconds: int = 300
    max_tokens: Optional[int] = None
    model: Optional[str] = None


@dataclass
class TaskResult:
    """Result of a runner task execution."""
    success: bool
    output: str = ""
    files_modified: list[str] = field(default_factory=list)
    files_created: list[str] = field(default_factory=list)
    error: Optional[str] = None
    duration_seconds: float = 0
    token_usage: dict = field(default_factory=dict)
    steps: list[dict] = field(default_factory=list)


class BaseRunner(ABC):
    """Abstract base class for agent execution runners.
    
    All runners execute inside the same sandbox with the same resource limits
    and approval policy. Runner-specific logic must not leak into workflow definitions.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Unique runner identifier, e.g., 'junie', 'ollama'."""
        ...

    @property
    @abstractmethod
    def capabilities(self) -> RunnerCapabilities:
        """What this runner can do."""
        ...

    @property
    @abstractmethod
    def requirements(self) -> RunnerRequirements:
        """Required binaries and configuration."""
        ...

    @abstractmethod
    async def execute(self, task: TaskRequest) -> TaskResult:
        """Execute a task and return the result."""
        ...

    async def check_available(self) -> bool:
        """Check if the runner is available (binaries present, config valid)."""
        import shutil
        for binary in self.requirements.binaries:
            if not shutil.which(binary):
                return False
        return True


class RunnerRegistry:
    """Registry of available runners.
    
    Manages discovery, selection, and lifecycle of runners.
    """

    def __init__(self):
        self._runners: dict[str, BaseRunner] = {}
        self._default: Optional[str] = None

    def register(self, runner: BaseRunner):
        """Register a runner implementation."""
        self._runners[runner.name] = runner
        if self._default is None:
            self._default = runner.name

    def set_default(self, name: str):
        """Set the default runner."""
        if name not in self._runners:
            raise ValueError(f"Runner '{name}' not registered")
        self._default = name

    def get(self, name: Optional[str] = None) -> BaseRunner:
        """Get a runner by name, or the default."""
        name = name or self._default
        if name is None:
            raise RuntimeError("No runners registered")
        if name not in self._runners:
            raise ValueError(f"Runner '{name}' not found. Available: {list(self._runners.keys())}")
        return self._runners[name]

    def list_available(self) -> list[dict]:
        """List all registered runners with their capabilities."""
        result = []
        for name, runner in self._runners.items():
            available = runner.check_available() if hasattr(runner, 'check_available') else True
            result.append({
                "name": name,
                "default": name == self._default,
                "available": available,
                "capabilities": {
                    "can_execute_code": runner.capabilities.can_execute_code,
                    "can_browse_web": runner.capabilities.can_browse_web,
                    "can_use_tools": runner.capabilities.can_use_tools,
                    "supports_isolation": [i.value for i in runner.capabilities.supports_isolation],
                },
                "requirements": {
                    "binaries": runner.requirements.binaries,
                    "env_vars": runner.requirements.env_vars,
                },
            })
        return result

    def select_for_task(self, task: TaskRequest) -> Optional[BaseRunner]:
        """Select the best runner for a given task.
        
        Considers isolation level, capabilities, and availability.
        """
        candidates = []
        for runner in self._runners.values():
            if task.isolation_level in runner.capabilities.supports_isolation:
                if runner.check_available():
                    candidates.append(runner)
        
        if not candidates:
            return None
        
        # Prefer the default runner if it's a candidate
        if self._default and self._runners.get(self._default) in candidates:
            return self._runners[self._default]
        
        return candidates[0]


# Global registry
runner_registry = RunnerRegistry()
