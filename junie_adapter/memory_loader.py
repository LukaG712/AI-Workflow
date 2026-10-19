import logging
from pathlib import Path

from .config import OrchestratorConfig

runs_logger = logging.getLogger("runs")


def load_context(config: OrchestratorConfig) -> str:
    """Load MEMORY.md and the 3 most recent session summaries for context.
    Returns a formatted string to prepend to the prompt system, or empty string."""
    parts = []

    memory_file = config.memory_file
    session_logs_dir = config.session_logs_dir

    # Load permanent memory file
    if memory_file.exists():
        try:
            content = memory_file.read_text(encoding="utf-8").strip()
            if content:
                parts.append("## Project Memory\n\n" + content)
        except Exception as e:
            runs_logger.warning(f"Failed to read MEMORY.md: {e}")

    # Load 3 most recent session logs
    if session_logs_dir.exists():
        try:
            # Sort by modification time, newest first
            log_files = sorted(
                session_logs_dir.glob("session-*.md"),
                key=lambda p: p.stat().st_mtime,
                reverse=True,
            )[:3]
            if log_files:
                session_summaries = []
                for lf in log_files:
                    try:
                        summary = lf.read_text(encoding="utf-8").strip()
                        if summary:
                            session_summaries.append(summary)
                    except Exception as e:
                        runs_logger.warning(f"Failed to read session log {lf.name}: {e}")
                if session_summaries:
                    parts.append("## Recent Session Summaries\n\n" + "\n---\n\n".join(session_summaries))
        except Exception as e:
            runs_logger.warning(f"Failed to list session logs: {e}")

    if parts:
        return "\n\n".join(parts)
    return ""


def load_vault_map(config: OrchestratorConfig) -> str:
    """Load the vault map file if configured and exists."""
    if not config.vault_map_path:
        return ""
    vault_map = Path(config.vault_map_path)
    if not vault_map.exists():
        return ""
    try:
        content = vault_map.read_text(encoding="utf-8").strip()
        return content
    except Exception as e:
        runs_logger.warning(f"Failed to read vault map: {e}")
        return ""
