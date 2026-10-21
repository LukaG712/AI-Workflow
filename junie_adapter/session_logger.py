import logging

from .config import OrchestratorConfig
from .models import SessionLog
from .obsidian import mirror_to_vault
from .prompt_refiner import ollama_chat

runs_logger = logging.getLogger("runs")


async def log_session(session_log: SessionLog, config: OrchestratorConfig) -> None:
    """Save a session log to disk, optionally generate a summary via Ollama,
    and optionally mirror to vault. Logging failures do not raise."""
    timestamp = session_log.timestamp.strftime("%Y%m%d-%H%M%S")
    session_logs_dir = config.session_logs_dir
    session_logs_dir.mkdir(parents=True, exist_ok=True)
    log_file = session_logs_dir / f"session-{timestamp}.md"

    # Build the summary content
    if (
        session_log.refined_prompt
        and session_log.refined_prompt != session_log.raw_prompt
    ):
        summary_content = (
            f"# Session {timestamp}\n\n"
            f"**Original prompt:** {session_log.raw_prompt}\n\n"
            f"**Refined prompt:** {session_log.refined_prompt}\n\n"
            f"**Answer:** {session_log.answer[:500]}\n"
        )
    else:
        summary_content = (
            f"# Session {timestamp}\n\n"
            f"**Prompt:** {session_log.raw_prompt}\n\n"
            f"**Answer:** {session_log.answer[:500]}\n"
        )

    # Try to get a better summary from Ollama if refinement was used
    try:
        summary_prompt = (
            "Write a concise session summary (3-5 sentences) with: what was asked, "
            "what was done, key decisions, and any pending tasks. "
            "Format as markdown with a # Session header.\n\n"
            f"Raw prompt: {session_log.raw_prompt}\n\n"
            f"Refined prompt: {session_log.refined_prompt or session_log.raw_prompt}\n\n"
            f"Answer: {session_log.answer[:1000]}"
        )
        system = "You are a technical assistant that writes concise session summaries."
        summary = await ollama_chat(
            [
                {"role": "system", "content": system},
                {"role": "user", "content": summary_prompt},
            ],
            config,
            try_think=False,
        )
        if summary.strip():
            summary_content = summary.strip()
    except Exception as e:
        runs_logger.warning(
            f"Ollama summary generation failed: {e}. Using fallback summary."
        )

    # Save the summary
    try:
        log_file.write_text(summary_content, encoding="utf-8")
        runs_logger.info(f"Session log saved: {log_file.name}")
    except Exception as e:
        runs_logger.error(f"Failed to save session log: {e}")

    # Mirror to vault if enabled
    if config.mirror_logs_to_vault:
        mirror_to_vault(log_file, config)
