import asyncio
import json
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv

import httpx

from .config import OrchestratorConfig
from .junie_runner import run_junie_task
from .memory_loader import load_context, load_vault_map
from .models import SessionLog, TaskStatus
from .obsidian import mirror_to_vault
from .prompt_refiner import refine_prompt, ollama_chat, ollama_chat_stream
from .session_logger import log_session

runs_logger = logging.getLogger("runs")

# Load environment variables from .env file if present
load_dotenv()

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Junie Orchestrator")

# Async lock for single-run Junie enforcement
junie_lock = asyncio.Lock()

# Global config, set on startup
config: Optional[OrchestratorConfig] = None


@app.on_event("startup")
async def startup():
    global config
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


# ─── Auth ────────────────────────────────────────────────────────────────────

async def require_auth(request: Request):
    """Check Bearer token auth. Raise 401 if missing or wrong."""
    if not config or not config.api_key:
        raise HTTPException(status_code=500, detail="API_KEY not configured")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != config.api_key:
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
    """Stream with progress: improved prompt → 'Junie is working...' → final answer → stop → [DONE]."""
    timestamp = int(time.time())
    comp_id = f"chatcmpl-{timestamp}"

    # Chunk 1: role + improved prompt
    reply = format_reply(improved_prompt, junie_answer, fallback, error_reason, raw_prompt)
    yield _sse_chunk(reply, comp_id, timestamp, model, role="assistant")

    # Chunk 2: stop
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
    """Handle chat completion requests. Routes through prompt builder and Junie, or bypasses to Ollama."""
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

    # Step 2: Run Junie with the prompt
    junie_answer = ""
    if not await junie_lock.acquire():
        # This shouldn't happen with asyncio.Lock, but just in case
        junie_answer = "Junie is busy. Please try again in a moment."
    else:
        try:
            result = await run_junie_task(improved_prompt, config)
            junie_answer = result.answer
        finally:
            junie_lock.release()

    reply = format_reply(improved_prompt, junie_answer, fallback, error_reason, raw_prompt)

    duration = time.time() - start_time
    runs_logger.info(
        f"Request completed in {duration:.1f}s | Raw: {raw_prompt[:80]}... | "
        f"Improved: {improved_prompt[:80]}... | Fallback: {fallback}"
    )

    # Step 3: Save session log (if it fails, we still return the answer)
    try:
        session_log = SessionLog(
            raw_prompt=raw_prompt,
            refined_prompt=improved_prompt if (fallback or improved_prompt != raw_prompt) else None,
            answer=junie_answer,
            status=TaskStatus.COMPLETED if not fallback else TaskStatus.COMPLETED,
            error=error_reason if error_reason else None,
        )
        await log_session(session_log, config)
    except Exception as e:
        runs_logger.error(f"Failed to save session log: {e}")

    # Step 4: Return response
    if stream:
        return StreamingResponse(
            stream_progress(improved_prompt, junie_answer, fallback, error_reason, raw_prompt),
            media_type="text/event-stream",
        )
    else:
        return make_completion_response(reply)
