import asyncio
import json
import logging
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from .config import OrchestratorConfig
from .models import RunResult, TaskStatus

runs_logger = logging.getLogger("runs")


def parse_junie_json(output: str) -> str:
    """Tolerant JSON parser for Junie output. Extracts answer text from any JSON shape."""
    stripped = output.strip()
    if not stripped:
        return ""
    try:
        data = json.loads(stripped)
    except json.JSONDecodeError:
        return stripped

    # Recursively search for text values in JSON
    def extract_text(obj, depth=0):
        if depth > 10:
            return ""
        if isinstance(obj, str):
            return obj
        if isinstance(obj, dict):
            # Try common key names for answer content
            for key in (
                "answer",
                "result",
                "content",
                "text",
                "output",
                "response",
                "message",
            ):
                if key in obj:
                    val = extract_text(obj[key], depth + 1)
                    if val.strip():
                        return val
            # Fall back to first string value
            for v in obj.values():
                val = extract_text(v, depth + 1)
                if val.strip():
                    return val
        if isinstance(obj, list):
            for item in obj:
                val = extract_text(item, depth + 1)
                if val.strip():
                    return val
        return ""

    return extract_text(data)


def has_rate_limit(output: str) -> bool:
    """Check if output indicates a rate limit error."""
    upper = output.upper()
    return "429" in upper or "RESOURCE_EXHAUSTED" in upper


async def run_junie_task(task: str, config: OrchestratorConfig) -> RunResult:
    """Run Junie CLI as a subprocess. Returns a RunResult with the answer and status."""
    workspace = config.workspace_dir
    workspace.mkdir(parents=True, exist_ok=True)

    # Support JUNIE_BIN pointing to a Python script (e.g., tests/fake_junie.py)
    junie_path = Path(config.junie_bin)
    if junie_path.suffix == ".py":
        cmd = [
            sys.executable,
            str(junie_path),
            "--task",
            task,
            "--project",
            str(workspace),
            "--output-format",
            "json",
        ]
    else:
        cmd = [
            config.junie_bin,
            "--task",
            task,
            "--project",
            str(workspace),
            "--output-format",
            "json",
        ]

    retry_delays = [30, 60, 90]
    last_error = ""
    start_time = time.time()

    for attempt, delay in enumerate([0] + retry_delays):
        if attempt > 0:
            runs_logger.info(f"Rate limit retry {attempt}/3 after {delay}s delay")
            await asyncio.sleep(delay)

        try:
            # Check if Junie binary exists
            junie_path = Path(config.junie_bin)
            if not junie_path.exists() and shutil.which(config.junie_bin) is None:
                duration = time.time() - start_time
                return RunResult(
                    task=task,
                    answer=f"Junie CLI not found: {config.junie_bin}",
                    status=TaskStatus.FAILED,
                    error=f"Junie CLI not found: {config.junie_bin}",
                    duration_seconds=duration,
                )

            # Start subprocess in its own process group for safe killing
            if sys.platform == "win32":
                # Windows: use CREATE_NEW_PROCESS_GROUP flag
                creationflags = 0x00000200  # CREATE_NEW_PROCESS_GROUP
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    creationflags=creationflags,
                )
            else:
                # Linux/Unix: use start_new_session for process group
                process = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    start_new_session=True,
                )

            try:
                stdout, stderr = await asyncio.wait_for(
                    process.communicate(),
                    timeout=config.junie_timeout,
                )
                output = (
                    stdout.decode("utf-8", errors="replace")
                    + stderr.decode("utf-8", errors="replace")
                ).strip()
                parsed = parse_junie_json(output)

                if has_rate_limit(output):
                    last_error = output
                    runs_logger.warning(
                        f"Rate limit detected in Junie output (attempt {attempt + 1})"
                    )
                    continue

                duration = time.time() - start_time
                runs_logger.info(f"Junie exit code: {process.returncode}")
                if parsed:
                    return RunResult(
                        task=task,
                        answer=parsed,
                        status=TaskStatus.COMPLETED,
                        duration_seconds=duration,
                    )
                else:
                    return RunResult(
                        task=task,
                        answer=f"Junie returned empty output (exit code {process.returncode})",
                        status=TaskStatus.FAILED,
                        error=f"Empty output, exit code {process.returncode}",
                        duration_seconds=duration,
                    )

            except asyncio.TimeoutError:
                # Kill the entire process group
                runs_logger.error(
                    f"Junie timed out after {config.junie_timeout}s, killing process group"
                )
                try:
                    if sys.platform == "win32":
                        # On Windows, terminate the process tree
                        process.kill()
                    else:
                        # On Unix, kill the entire process group
                        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
                    # Wait for the process to actually die
                    await process.wait()
                except Exception as kill_err:
                    runs_logger.error(f"Error killing process group: {kill_err}")
                    try:
                        process.kill()
                        await process.wait()
                    except Exception:
                        pass

                duration = time.time() - start_time
                return RunResult(
                    task=task,
                    answer=f"Junie timed out after {config.junie_timeout}s",
                    status=TaskStatus.TIMED_OUT,
                    error=f"Timeout after {config.junie_timeout}s",
                    duration_seconds=duration,
                )

        except Exception as e:
            last_error = str(e)
            runs_logger.error(f"Junie subprocess error: {e}")
            if attempt == 0:
                duration = time.time() - start_time
                return RunResult(
                    task=task,
                    answer=f"Junie error: {e}",
                    status=TaskStatus.FAILED,
                    error=str(e),
                    duration_seconds=duration,
                )
            continue

    duration = time.time() - start_time
    return RunResult(
        task=task,
        answer=f"Rate limit hit after {len(retry_delays)} retries. Try again later.",
        status=TaskStatus.FAILED,
        error=last_error or "Rate limit exceeded",
        duration_seconds=duration,
    )
