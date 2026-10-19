import os
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional


@dataclass
class OrchestratorConfig:
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "gemma4:12b"
    refine_prompt: bool = False
    workspace_path: Optional[str] = None
    vault_path: Optional[str] = None
    mirror_logs_to_vault: bool = False
    api_key: str = ""
    junie_timeout: int = 1800
    port: int = 8000
    junie_bin: str = "junie"
    vault_map_path: Optional[str] = None

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

        # Determine workspace path
        if workspace_env:
            workspace_path = workspace_env
        elif vault_env:
            workspace_path = vault_env
        else:
            workspace_path = None

        return cls(
            ollama_url=os.getenv("OLLAMA_URL", "http://localhost:11434"),
            ollama_model=os.getenv("OLLAMA_MODEL", "gemma4:12b"),
            refine_prompt=os.getenv("REFINE_PROMPT", "false").lower() == "true",
            workspace_path=workspace_path,
            vault_path=vault_env,
            mirror_logs_to_vault=os.getenv("MIRROR_LOGS_TO_VAULT", "false").lower() == "true",
            api_key=os.getenv("API_KEY", ""),
            junie_timeout=int(os.getenv("JUNIE_TIMEOUT", "1800")),
            port=int(os.getenv("PORT", "8000")),
            junie_bin=os.getenv("JUNIE_BIN", "junie"),
            vault_map_path=os.getenv("VAULT_MAP_PATH"),
        )

    def validate(self, require_api_key: bool = False) -> None:
        """Validate configuration, raising RuntimeError with actionable messages."""
        if require_api_key and not self.api_key:
            raise RuntimeError(
                "API_KEY environment variable is not set or is empty. "
                "Set it in your .env file or as an environment variable."
            )
