"""Memory and context loading for JunieOrchestrator.

Provides backward-compatible functions (`load_context`, `load_vault_map`)
that delegate to the pluggable `ContextProvider` interface.

New code should prefer `ContextProvider` implementations directly:

    from junie_adapter import create_default_context_provider, OrchestratorConfig

    config = OrchestratorConfig.from_env()
    provider = create_default_context_provider(config, include_memory=True)
    context = provider.load_context()
"""

import logging
from pathlib import Path
from typing import Optional

from .config import OrchestratorConfig

runs_logger = logging.getLogger("runs")


def load_context(config: OrchestratorConfig) -> str:
    """Load MEMORY.md and the 3 most recent session summaries for context.

    Uses the `MemoryFileContextProvider` from the pluggable context provider
    interface. Returns a formatted string to prepend to the prompt system,
    or empty string.

    Args:
        config: Orchestrator configuration with workspace path.

    Returns:
        Context string or empty string if no memory files exist.
    """
    from .context_provider import MemoryFileContextProvider

    provider = MemoryFileContextProvider(config, max_sessions=3)
    return provider.load_context()


def load_vault_map(config: OrchestratorConfig) -> str:
    """Load the vault map file if configured and exists.

    Uses the `VaultMapContextProvider` from the pluggable context provider
    interface.

    Args:
        config: Orchestrator configuration with optional vault_map_path.

    Returns:
        Vault map content or empty string if not configured.
    """
    from .context_provider import VaultMapContextProvider

    provider = VaultMapContextProvider(config=config)
    return provider.load_context()
