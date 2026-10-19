import logging
from pathlib import Path

from .config import OrchestratorConfig

runs_logger = logging.getLogger("runs")


def mirror_to_vault(source: Path, config: OrchestratorConfig) -> None:
    """Copy a session log to VAULT_PATH/Session-Logs/ for Obsidian visibility.
    Skips silently if VAULT_PATH is not configured."""
    if not config.vault_path:
        return
    try:
        vault_logs_dir = Path(config.vault_path) / "Session-Logs"
        vault_logs_dir.mkdir(parents=True, exist_ok=True)
        dest = vault_logs_dir / source.name
        dest.write_text(source.read_text(encoding="utf-8"), encoding="utf-8")
        runs_logger.info(f"Session log mirrored to vault: {dest.name}")
    except Exception as e:
        runs_logger.warning(f"Failed to mirror session log to vault: {e}")
