"""Pluggable context provider interface for JunieOrchestrator.

Provides a clean abstraction for loading context (MEMORY.md, session logs,
vault maps) without coupling core execution to Obsidian or other backends.
"""

import logging
from abc import ABC, abstractmethod
from pathlib import Path
from typing import List, Optional

from .config import OrchestratorConfig

logger = logging.getLogger(__name__)


class ContextProvider(ABC):
    """Abstract interface for loading execution context.

    Implementations can load context from MEMORY.md files, session logs,
    vault maps, or any other source.
    """

    @abstractmethod
    def load_context(self) -> str:
        """Load and return context string.

        Returns:
            Context string to prepend to the task prompt. Empty string if no context.
        """
        pass


class EmptyContextProvider(ContextProvider):
    """Returns empty context. Used when context loading is disabled."""

    def load_context(self) -> str:
        return ""


class MemoryFileContextProvider(ContextProvider):
    """Loads context from MEMORY.md and recent session logs.

    This is the default provider, replacing the old memory_loader.py logic.
    """

    def __init__(self, config: OrchestratorConfig, max_sessions: int = 5):
        """Initialize with orchestrator config.

        Args:
            config: Orchestrator configuration with workspace path.
            max_sessions: Maximum number of recent session logs to include.
        """
        self._config = config
        self._max_sessions = max_sessions

    def load_context(self) -> str:
        parts = []

        # Load MEMORY.md
        memory_path = self._config.memory_file
        if memory_path.exists():
            try:
                content = memory_path.read_text(encoding="utf-8").strip()
                if content:
                    parts.append(f"# Memory\n\n{content}")
            except Exception as e:
                logger.warning(f"Failed to read MEMORY.md: {e}")

        # Load recent session logs (supports both .md and .json formats)
        logs_dir = self._config.session_logs_dir
        if logs_dir.exists():
            try:
                # Read both .md (session logger format) and .json files
                log_files = sorted(
                    list(logs_dir.glob("session-*.md")) + list(logs_dir.glob("*.json")),
                    key=lambda p: p.stat().st_mtime,
                    reverse=True,
                )[: self._max_sessions]

                if log_files:
                    session_summaries = []
                    for log_file in log_files:
                        try:
                            content = log_file.read_text(encoding="utf-8").strip()
                            if log_file.suffix == ".json":
                                import json
                                data = json.loads(content)
                                prompt = data.get("raw_prompt", "")[:200]
                                status = data.get("status", "unknown")
                                session_summaries.append(
                                    f"- [{status}] {prompt}"
                                )
                            else:
                                # .md format: use first line as summary
                                first_line = content.split("\n")[0][:200]
                                session_summaries.append(f"- {first_line}")
                        except Exception as e:
                            logger.warning(f"Failed to read session log {log_file}: {e}")

                    if session_summaries:
                        parts.append(
                            f"# Recent Sessions\n\n" + "\n".join(session_summaries)
                        )
            except Exception as e:
                logger.warning(f"Failed to read session logs: {e}")

        return "\n\n".join(parts)


class VaultMapContextProvider(ContextProvider):
    """Loads context from a vault map file.

    The vault map provides a directory structure overview for navigation.
    """

    def __init__(self, vault_map_path: Optional[str] = None, config: Optional[OrchestratorConfig] = None):
        """Initialize with vault map path.

        Args:
            vault_map_path: Path to the vault map file.
            config: Orchestrator config (fallback for vault_map_path).
        """
        self._vault_map_path = vault_map_path
        self._config = config

    def load_context(self) -> str:
        path = self._vault_map_path
        if path is None and self._config and self._config.vault_map_path:
            path = self._config.vault_map_path

        if path is None:
            return ""

        vault_map = Path(path)
        if not vault_map.exists():
            logger.warning(f"Vault map not found: {path}")
            return ""

        try:
            content = vault_map.read_text(encoding="utf-8").strip()
            if content:
                return f"# Vault Map\n\n{content}"
        except Exception as e:
            logger.warning(f"Failed to read vault map: {e}")

        return ""


class CompositeContextProvider(ContextProvider):
    """Combines multiple context providers.

    Loads context from all providers and concatenates the results.
    """

    def __init__(self, providers: List[ContextProvider]):
        """Initialize with a list of context providers.

        Args:
            providers: List of ContextProvider instances. Context is loaded
                in order and concatenated with separators.
        """
        self._providers = providers

    def load_context(self) -> str:
        parts = []
        for provider in self._providers:
            try:
                context = provider.load_context()
                if context:
                    parts.append(context)
            except Exception as e:
                logger.warning(f"Context provider {provider.__class__.__name__} failed: {e}")

        return "\n\n---\n\n".join(parts)


def create_default_context_provider(
    config: OrchestratorConfig,
    include_memory: bool = True,
    include_vault_map: bool = False,
) -> ContextProvider:
    """Create a default context provider based on config.

    Args:
        config: Orchestrator configuration.
        include_memory: Whether to include MEMORY.md and session logs.
        include_vault_map: Whether to include vault map.

    Returns:
        A ContextProvider instance.
    """
    providers = []

    if include_memory:
        providers.append(MemoryFileContextProvider(config))

    if include_vault_map:
        providers.append(VaultMapContextProvider(config=config))

    if not providers:
        return EmptyContextProvider()

    return CompositeContextProvider(providers)
