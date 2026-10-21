import os
import tempfile
from dataclasses import dataclass
from enum import Enum
from pathlib import Path


class IsolationMode(Enum):
    """Worktree isolation mode for worker execution."""

    WORKTREE = "worktree"  # Use worktree isolation when Git is available
    NONE = "none"  # No isolation, run in the project directory
    REQUIRED = "required"  # Require worktree isolation, raise if not a Git repo


@dataclass
class OrchestratorConfig:
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:12b"
    refine_prompt: bool = False
    workspace_path: str | None = None
    vault_path: str | None = None
    mirror_logs_to_vault: bool = False
    api_key: str = ""
    junie_timeout: int = 1800
    port: int = 8000
    junie_bin: str = "junie"
    vault_map_path: str | None = None
    max_concurrent_workers: int = 1  # JUNIE-3394: one session per project
    isolation_mode: IsolationMode = IsolationMode.WORKTREE

    @property
    def workspace_dir(self) -> Path:
        """Return the workspace directory, falling back to a temp directory if not set."""
        if self.workspace_path:
            return Path(self.workspace_path)
        return Path(tempfile.gettempdir()) / "junie_workspace"

    @property
    def memory_file(self) -> Path:
        return self.workspace_dir / "MEMORY.md"

    @property
    def session_logs_dir(self) -> Path:
        return self.workspace_dir / "session-logs"

    @classmethod
    def from_env(cls) -> "OrchestratorConfig":
        """Load configuration from environment variables."""
        workspace_env = os.getenv("WORKSPACE_PATH")
        vault_env = os.getenv("VAULT_PATH")

        # Determine workspace path — do NOT fall back to VAULT_PATH
        # (VAULT_PATH is for Obsidian vault mirroring, not workspace)
        workspace_path = workspace_env if workspace_env else None

        isolation_str = os.getenv("ISOLATION_MODE", "worktree").lower()
        try:
            isolation_mode = IsolationMode(isolation_str)
        except ValueError:
            isolation_mode = IsolationMode.WORKTREE

        return cls(
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:12b"),
            refine_prompt=os.getenv("REFINE_PROMPT", "false").lower() == "true",
            workspace_path=workspace_path,
            vault_path=vault_env,
            mirror_logs_to_vault=os.getenv("MIRROR_LOGS_TO_VAULT", "false").lower()
            == "true",
            api_key=os.getenv("API_KEY", ""),
            junie_timeout=int(os.getenv("JUNIE_TIMEOUT", "1800")),
            port=int(os.getenv("PORT", "8000")),
            junie_bin=os.getenv("JUNIE_BIN", "junie"),
            vault_map_path=os.getenv("VAULT_MAP_PATH"),
            max_concurrent_workers=int(os.getenv("MAX_CONCURRENT_WORKERS", "1")),
            isolation_mode=isolation_mode,
        )

    def validate(self, require_api_key: bool = False) -> None:
        """Validate configuration, raising RuntimeError with actionable messages."""
        if require_api_key and not self.api_key:
            raise RuntimeError(
                "API_KEY environment variable is not set or is empty. "
                "Set it in your .env file or as an environment variable."
            )

    def validate_workspace(self) -> None:
        """Validate that the workspace path exists and is accessible.

        Raises:
            RuntimeError: If the workspace path is invalid.
        """
        ws = self.workspace_dir
        if not ws.exists():
            raise RuntimeError(
                f"Workspace path does not exist: {ws}. "
                "Set WORKSPACE_PATH to a valid directory."
            )
        if not ws.is_dir():
            raise RuntimeError(f"Workspace path is not a directory: {ws}")
        if not os.access(ws, os.W_OK):
            raise RuntimeError(f"Workspace path is not writable: {ws}")
