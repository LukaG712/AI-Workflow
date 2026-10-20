import asyncio
import hashlib
import hmac
import json
import logging
import os
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException, Query
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv

import httpx

from .config import OrchestratorConfig, IsolationMode
from .junie_runner import run_junie_task
from .memory_loader import load_context, load_vault_map
from .models import SessionLog, TaskStatus, WorkerStatus
from .obsidian import mirror_to_vault
from .prompt_refiner import refine_prompt, ollama_chat, ollama_chat_stream
from .session_logger import log_session
from .worker_executor import get_worker_registry, _worker_semaphore

runs_logger = logging.getLogger("runs")

# Load environment variables from .env file if present
load_dotenv()

# ─── App ─────────────────────────────────────────────────────────────────────

# Lock for single-run Junie enforcement — use a simple flag instead of asyncio.Lock
# to avoid issues with locked()/acquire() semantics in async context
_junie_busy = False

# Global config, set on startup
config: Optional[OrchestratorConfig] = None

# Global persistent store
from .persistent_store import PersistentStore
store: Optional[PersistentStore] = None


@asynccontextmanager
async def lifespan(app_instance: FastAPI):
    """Manage application startup and shutdown lifecycle."""
    global config, store
    config = OrchestratorConfig.from_env()
    config.validate(require_api_key=True)
    # Ensure workspace and session logs dirs exist
    config.workspace_dir.mkdir(parents=True, exist_ok=True)
    config.session_logs_dir.mkdir(parents=True, exist_ok=True)
    # Setup logging
    log_dir = Path("logs")
    log_dir.mkdir(exist_ok=True)
    runs_log = log_dir / "runs.log"
    runs_logger.setLevel(logging.INFO)
    runs_logger.addHandler(logging.FileHandler(str(runs_log)))
    runs_logger.propagate = False
    # Initialize persistent storage
    db_path = os.getenv("JUNIEHUB_DB_PATH", str(config.workspace_dir / "juniehub.db"))
    store = PersistentStore(db_path=db_path)
    runs_logger.info("JunieHub started")
    yield
    if store:
        store.close()
    runs_logger.info("JunieHub shutting down")


app = FastAPI(title="JunieHub", version="1.0.0", description="JunieHub — coding-agent orchestration backend", lifespan=lifespan)


# ─── Auth ────────────────────────────────────────────────────────────────────

async def require_auth(request: Request):
    """Check Bearer token auth using constant-time comparison. Raise 401 if missing or wrong."""
    if not config or not config.api_key:
        raise HTTPException(status_code=500, detail="API_KEY not configured")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Unauthorized")
    token = auth[7:]
    # Constant-time comparison to prevent timing attacks
    if not hmac.compare_digest(token, config.api_key):
        raise HTTPException(status_code=401, detail="Unauthorized")


# ─── Bypass detection ────────────────────────────────────────────────────────

def is_bypass_request(messages: list) -> bool:
    """Check if the last user message starts with '### Task:' (Open WebUI background request)."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "").startswith("### Task:")
    return False


def get_last_user_message(messages: list) -> str:
    """Extract the last user message content."""
    for msg in reversed(messages):
        if msg.get("role") == "user":
            return msg.get("content", "")
    return ""


# ─── Reply formatting ────────────────────────────────────────────────────────

def format_reply(improved_prompt: str, junie_answer: str, fallback: bool, error_reason: str = "", raw_prompt: str = "") -> str:
    """Format the final reply as markdown with blockquote and answer."""
    lines = []
    if fallback:
        if error_reason:
            lines.append(f"> **Note:** Ollama prompt rewrite failed ({error_reason}). Using original prompt.")
        else:
            lines.append(f"> **Note:** Ollama was unreachable. Using original prompt.")
        if raw_prompt and raw_prompt != improved_prompt:
            lines.append(f"> Original prompt: {raw_prompt}")
        lines.append(f"> Used prompt: {improved_prompt}")
    else:
        if raw_prompt and raw_prompt != improved_prompt:
            lines.append(f"> Original prompt: {raw_prompt}")
            lines.append(f"> Refined prompt: {improved_prompt}")
        else:
            lines.append(f"> Prompt: {improved_prompt}")
    lines.append("")
    lines.append(f"Junie: {junie_answer}")
    return "\n".join(lines)


# ─── OpenAI-compatible response helpers ──────────────────────────────────────

def make_completion_response(content: str, model: str = "junie-workflow") -> dict:
    """Create a standard OpenAI chat.completion response."""
    return {
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": {
                    "role": "assistant",
                    "content": content,
                },
                "finish_reason": "stop",
            }
        ],
    }


def sse_event(data: str) -> str:
    """Format a Server-Sent Event."""
    return f"data: {data}\n\n"


def _sse_chunk(content: str, comp_id: str, timestamp: int, model: str, finish_reason: Optional[str] = None, role: Optional[str] = None) -> str:
    """Build a single SSE chunk JSON."""
    delta = {}
    if role is not None:
        delta["role"] = role
    if content is not None:
        delta["content"] = content
    data = json.dumps({
        "id": comp_id,
        "object": "chat.completion.chunk",
        "created": timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": delta,
                "finish_reason": finish_reason,
            }
        ],
    })
    return sse_event(data)


async def stream_progress(improved_prompt: str, junie_answer: str, fallback: bool, error_reason: str, raw_prompt: str = "", model: str = "junie-workflow"):
    """Stream the completed reply as SSE events.

    NOTE: This is post-completion streaming. The Junie CLI does not support
    reliable incremental output, so the entire reply is sent after Junie
    completes. Progress events are emitted as distinct event types, not
    disguised as token chunks.
    """
    timestamp = int(time.time())
    comp_id = f"chatcmpl-{timestamp}"

    # Progress event: task completed
    progress_data = json.dumps({
        "type": "progress",
        "status": "task_completed",
        "message": "Junie has completed the task.",
    })
    yield f"event: progress\ndata: {progress_data}\n\n"

    # Chunk 1: role + full reply
    reply = format_reply(improved_prompt, junie_answer, fallback, error_reason, raw_prompt)
    yield _sse_chunk(reply, comp_id, timestamp, model, role="assistant")

    # Chunk 2: stop
    yield _sse_chunk(None, comp_id, timestamp, model, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def stream_busy_event(model: str = "junie-workflow"):
    """Stream a busy event when Junie is already running."""
    timestamp = int(time.time())
    comp_id = f"chatcmpl-{timestamp}"

    progress_data = json.dumps({
        "type": "progress",
        "status": "busy",
        "message": "Junie is currently busy. Please try again in a moment.",
    })
    yield f"event: progress\ndata: {progress_data}\n\n"

    yield _sse_chunk("Junie is busy. Please try again in a moment.", comp_id, timestamp, model, role="assistant")
    yield _sse_chunk(None, comp_id, timestamp, model, finish_reason="stop")
    yield "data: [DONE]\n\n"


async def stream_from_ollama_progress(messages: list, model: str = "junie-workflow"):
    """Stream from Ollama with progress chunks: role → content chunks → stop → [DONE]."""
    timestamp = int(time.time())
    comp_id = f"chatcmpl-{timestamp}"

    # Initial chunk with role
    yield _sse_chunk("", comp_id, timestamp, model, role="assistant")

    # Stream content from Ollama
    async for chunk_content in ollama_chat_stream(messages, config):
        yield _sse_chunk(chunk_content, comp_id, timestamp, model)

    # Final chunk
    yield _sse_chunk(None, comp_id, timestamp, model, finish_reason="stop")
    yield "data: [DONE]\n\n"


# ─── Health & Status ─────────────────────────────────────────────────────────

@app.get("/health")
async def health_check():
    """Basic health check — returns 200 if the service is running."""
    return {"status": "ok", "service": "JunieHub"}


@app.get("/ready")
async def readiness_check():
    """Readiness check — returns 200 if the service is fully initialized."""
    if config is None:
        raise HTTPException(status_code=503, detail="Service not initialized")
    if store is None:
        raise HTTPException(status_code=503, detail="Database not initialized")
    return {
        "status": "ready",
        "service": "JunieHub",
        "version": "1.0.0",
        "max_concurrent_workers": config.max_concurrent_workers,
        "isolation_mode": config.isolation_mode.value,
    }


# ─── Projects ────────────────────────────────────────────────────────────────

@app.get("/api/v1/projects")
async def list_projects_api(request: Request):
    """List all registered projects."""
    await require_auth(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Database not available")
    projects = store.list_projects()
    return [
        {
            "id": p.id,
            "name": p.name,
            "path": p.path,
            "created_at": p.created_at.isoformat(),
            "updated_at": p.updated_at.isoformat(),
        }
        for p in projects
    ]


@app.post("/api/v1/projects")
async def register_project(request: Request):
    """Register a new project."""
    await require_auth(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Database not available")
    body = await request.json()
    project_id = body.get("id") or str(uuid.uuid4())
    name = body.get("name", "")
    path = body.get("path", "")
    if not path:
        raise HTTPException(status_code=400, detail="Project path is required")
    # Validate path exists
    if not Path(path).is_dir():
        raise HTTPException(status_code=400, detail=f"Project path does not exist: {path}")
    if not name:
        name = Path(path).name
    project = store.upsert_project(project_id, name, path)
    return {"id": project.id, "name": project.name, "path": project.path}


# ─── Tasks ───────────────────────────────────────────────────────────────────

@app.get("/api/v1/tasks")
async def list_tasks_api(
    request: Request,
    project_id: Optional[str] = Query(None),
    status: Optional[str] = Query(None),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
):
    """List tasks with optional filtering."""
    await require_auth(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Database not available")
    tasks = store.list_tasks(project_id=project_id, status=status, limit=limit, offset=offset)
    return [
        {
            "id": t.id,
            "worker_id": t.worker_id,
            "parent_task_id": t.parent_task_id,
            "task_description": t.task_description,
            "project_path": t.project_path,
            "status": t.status,
            "exit_code": t.exit_code,
            "duration_seconds": t.duration_seconds,
            "start_time": t.start_time.isoformat(),
            "end_time": t.end_time.isoformat() if t.end_time else None,
            "error": t.error,
        }
        for t in tasks
    ]


@app.get("/api/v1/tasks/{task_id}")
async def get_task_api(request: Request, task_id: str):
    """Get a task by ID."""
    await require_auth(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Database not available")
    task = store.get_task(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task not found: {task_id}")
    return {
        "id": task.id,
        "worker_id": task.worker_id,
        "parent_task_id": task.parent_task_id,
        "task_description": task.task_description,
        "project_path": task.project_path,
        "worktree_path": task.worktree_path,
        "status": task.status,
        "exit_code": task.exit_code,
        "stdout": task.stdout,
        "stderr": task.stderr,
        "result_summary": task.result_summary,
        "changed_files": task.changed_files if isinstance(task.changed_files, list) else [],
        "error": task.error,
        "duration_seconds": task.duration_seconds,
        "start_time": task.start_time.isoformat(),
        "end_time": task.end_time.isoformat() if task.end_time else None,
    }


# ─── Workers ─────────────────────────────────────────────────────────────────

@app.get("/api/v1/workers")
async def list_workers_api(request: Request):
    """List active workers and their status."""
    await require_auth(request)
    registry = get_worker_registry()
    workers = await registry.get_active()
    return [
        {
            "worker_id": w.worker_id,
            "task_id": w.task_id,
            "status": w.status.value,
            "task_description": w.task_description,
            "project_path": w.project_path,
            "worktree_path": w.worktree_path,
            "pid": w.pid,
            "start_time": w.start_time.isoformat(),
        }
        for w in workers
    ]


@app.get("/api/v1/workers/{worker_id}")
async def get_worker_api(request: Request, worker_id: str):
    """Get a worker by ID."""
    await require_auth(request)
    registry = get_worker_registry()
    worker = await registry.get(worker_id)
    if worker is None:
        raise HTTPException(status_code=404, detail=f"Worker not found: {worker_id}")
    return {
        "worker_id": worker.worker_id,
        "task_id": worker.task_id,
        "status": worker.status.value,
        "task_description": worker.task_description,
        "project_path": worker.project_path,
        "worktree_path": worker.worktree_path,
        "pid": worker.pid,
        "start_time": worker.start_time.isoformat(),
        "end_time": worker.end_time.isoformat() if worker.end_time else None,
        "exit_code": worker.exit_code,
        "error": worker.error,
    }


# ─── Sessions ────────────────────────────────────────────────────────────────

@app.get("/api/v1/sessions")
async def list_sessions_api(
    request: Request,
    task_id: Optional[str] = Query(None),
    limit: int = Query(20, ge=1, le=100),
):
    """List session records."""
    await require_auth(request)
    if store is None:
        raise HTTPException(status_code=503, detail="Database not available")
    sessions = store.list_sessions(task_id=task_id, limit=limit)
    return sessions


# ─── Queue Status ────────────────────────────────────────────────────────────

@app.get("/api/v1/queue")
async def queue_status_api(request: Request):
    """Get queue and concurrency status."""
    await require_auth(request)
    registry = get_worker_registry()
    return {
        "max_concurrent_workers": config.max_concurrent_workers if config else 1,
        "active_workers": registry.active_count,
        "semaphore_active": _worker_semaphore.active_count,
        "junie_busy": _junie_busy,
    }


# ─── Routes ──────────────────────────────────────────────────────────────────

@app.get("/v1/models")
async def list_models(request: Request):
    """List available models. Returns junie-workflow."""
    await require_auth(request)
    return {
        "object": "list",
        "data": [
            {
                "id": "junie-workflow",
                "object": "model",
            }
        ],
    }


@app.post("/v1/chat/completions")
async def chat_completions(request: Request):
    """Handle chat completion requests. Routes through prompt builder and Junie, or bypasses to Ollama.

    NOTE: Streaming is post-completion. The Junie CLI does not support reliable
    incremental output, so SSE events are sent after Junie completes.
    """
    await require_auth(request)

    body = await request.json()
    messages = body.get("messages", [])
    stream = body.get("stream", False)

    if not messages:
        raise HTTPException(status_code=400, detail="No messages provided")

    # Check for bypass request (### Task: prefix)
    if is_bypass_request(messages):
        if stream:
            return StreamingResponse(
                stream_from_ollama_progress(messages),
                media_type="text/event-stream",
            )
        else:
            try:
                answer = await ollama_chat(messages, config)
                return make_completion_response(answer)
            except Exception as e:
                return make_completion_response(f"Ollama error: {e}")

    # Extract raw prompt from last user message
    raw_prompt = get_last_user_message(messages)
    if not raw_prompt:
        raise HTTPException(status_code=400, detail="No user message found")

    start_time = time.time()

    # Step 1: Optionally improve the prompt
    improved_prompt = raw_prompt
    fallback = False
    error_reason = ""

    if config and config.refine_prompt:
        # Load context for prompt refinement
        context = load_context(config)
        vault_map = load_vault_map(config)
        improved_prompt, fallback, error_reason = await refine_prompt(raw_prompt, config, context, vault_map)

    # Step 2: Run Junie with the prompt — check busy flag
    junie_answer = ""
    task_status = TaskStatus.COMPLETED

    # Check if Junie is already running
    global _junie_busy
    if _junie_busy:
        if stream:
            return StreamingResponse(
                stream_busy_event(),
                media_type="text/event-stream",
                status_code=503,
            )
        raise HTTPException(
            status_code=503,
            detail="Junie is currently busy. Please try again in a moment."
        )

    _junie_busy = True
    try:
        result = await run_junie_task(improved_prompt, config)
        junie_answer = result.answer
        if result.status != TaskStatus.COMPLETED:
            task_status = result.status
    except Exception as e:
        junie_answer = f"Error running Junie: {e}"
        task_status = TaskStatus.FAILED
        runs_logger.error(f"Junie execution error: {e}")
    finally:
        _junie_busy = False

    reply = format_reply(improved_prompt, junie_answer, fallback, error_reason, raw_prompt)

    duration = time.time() - start_time
    # Log only truncated previews, not full prompts
    raw_preview = raw_prompt[:80]
    improved_preview = improved_prompt[:80]
    runs_logger.info(
        f"Request completed in {duration:.1f}s | Raw: {raw_preview}... | "
        f"Improved: {improved_preview}... | Fallback: {fallback}"
    )

    # Step 3: Save session log (if it fails, we still return the answer)
    task_id = f"task-{uuid.uuid4().hex[:8]}"
    worker_id = f"worker-{uuid.uuid4().hex[:8]}"
    session_error = None
    try:
        session_log = SessionLog(
            raw_prompt=raw_prompt,
            refined_prompt=improved_prompt if (fallback or improved_prompt != raw_prompt) else None,
            answer=junie_answer,
            status=task_status,
            error=error_reason if error_reason else None,
        )
        await log_session(session_log, config)
    except Exception as e:
        session_error = str(e)
        runs_logger.error(f"Failed to save session log: {e}")

    # Step 3b: Persist task record in the database (if it fails, we still return the answer)
    if store is not None:
        try:
            from .persistent_store import TaskRecord

            now = datetime.now()
            task_error = None
            if task_status == TaskStatus.FAILED:
                task_error = junie_answer if junie_answer.startswith("Error") else session_error
            task_record = TaskRecord(
                id=task_id,
                project_id=None,
                worker_id=worker_id,
                parent_task_id=None,
                task_description=raw_prompt,
                project_path=str(config.workspace_dir) if config else "",
                worktree_path=None,
                status=task_status.value,
                exit_code=0 if task_status == TaskStatus.COMPLETED else 1,
                stdout=junie_answer,
                stderr=error_reason if error_reason else "",
                result_summary=junie_answer[:500] if junie_answer else None,
                changed_files="[]",
                error=task_error,
                duration_seconds=duration,
                start_time=now - datetime.timedelta(seconds=duration),
                end_time=now,
            )
            store.save_task(task_record)
            runs_logger.debug(f"Persisted task {task_id} as {task_status.value}")
        except Exception as e:
            runs_logger.warning(f"Failed to persist task {task_id}: {e}")

    # Step 4: Return response
    if stream:
        return StreamingResponse(
            stream_progress(improved_prompt, junie_answer, fallback, error_reason, raw_prompt),
            media_type="text/event-stream",
        )
    else:
        return make_completion_response(reply)
