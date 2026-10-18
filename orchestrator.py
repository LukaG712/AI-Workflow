import os
import sys
import json
import time
import asyncio
import logging
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import JSONResponse, StreamingResponse
from dotenv import load_dotenv
import httpx

# Load environment variables from .env file if present
load_dotenv()

# ─── Configuration ───────────────────────────────────────────────────────────

OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:14b")
VAULT_PATH = os.getenv("VAULT_PATH", "/mnt/vault")
API_KEY = os.getenv("API_KEY", "")
JUNIE_TIMEOUT = int(os.getenv("JUNIE_TIMEOUT", "1800"))
PORT = int(os.getenv("PORT", "8000"))
JUNIE_BIN = os.getenv("JUNIE_BIN", "junie")

# ─── Logging ─────────────────────────────────────────────────────────────────

LOG_DIR = Path("logs")
LOG_DIR.mkdir(exist_ok=True)
RUNS_LOG = LOG_DIR / "runs.log"

runs_logger = logging.getLogger("runs")
runs_logger.setLevel(logging.INFO)
runs_logger.addHandler(logging.FileHandler(str(RUNS_LOG)))
runs_logger.propagate = False

# ─── App ─────────────────────────────────────────────────────────────────────

app = FastAPI(title="Junie Orchestrator")

# Async lock for single-run Junie enforcement
junie_lock = asyncio.Lock()

# ─── Auth ────────────────────────────────────────────────────────────────────

async def require_auth(request: Request):
    """Check Bearer token auth. Raise 401 if missing or wrong."""
    if not API_KEY:
        raise HTTPException(status_code=500, detail="API_KEY not configured")
    auth = request.headers.get("Authorization", "")
    if not auth.startswith("Bearer ") or auth[7:] != API_KEY:
        raise HTTPException(status_code=401, detail="Unauthorized")

# ─── Ollama helpers ──────────────────────────────────────────────────────────

PROMPT_REWRITE_SYSTEM = (
    "You rewrite rough requests into one precise task for an AI agent that works "
    "inside an Obsidian vault of markdown files. Keep my intent exactly. Add: "
    "the goal, which folders or notes are in scope, constraints (never delete files; "
    "write new files in the _ai/ folder unless told otherwise; do not edit notes "
    "outside the scope), and the expected output format. Do not invent facts. "
    "Return ONLY the rewritten task, no commentary."
)


async def ollama_chat(messages: list, model: Optional[str] = None) -> str:
    """Call Ollama /api/chat and return the assistant's message text."""
    model = model or OLLAMA_MODEL
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": False,
            },
        )
        resp.raise_for_status()
        body = resp.json()
    return body.get("message", {}).get("content", "")


async def ollama_chat_stream(messages: list, model: Optional[str] = None):
    """Call Ollama /api/chat with streaming and yield content chunks."""
    model = model or OLLAMA_MODEL
    async with httpx.AsyncClient(timeout=120) as client:
        async with client.stream(
            "POST",
            f"{OLLAMA_URL}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
        ) as resp:
            resp.raise_for_status()
            async for line in resp.aiter_lines():
                if not line.strip():
                    continue
                try:
                    chunk = json.loads(line)
                    content = chunk.get("message", {}).get("content", "")
                    if content:
                        yield content
                except json.JSONDecodeError:
                    continue


# ─── Prompt builder ──────────────────────────────────────────────────────────

async def build_prompt(raw: str) -> tuple[str, bool]:
    """Rewrite a raw prompt using Ollama. Returns (improved_prompt, fallback_flag).
    On error, returns (raw, True) to indicate fallback."""
    try:
        improved = await ollama_chat(
            [
                {"role": "system", "content": PROMPT_REWRITE_SYSTEM},
                {"role": "user", "content": raw},
            ]
        )
        if improved.strip():
            return improved.strip(), False
    except Exception as e:
        runs_logger.warning(f"Ollama prompt rewrite failed: {e}")
    return raw, True


# ─── Junie runner ────────────────────────────────────────────────────────────

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
            for key in ("answer", "result", "content", "text", "output", "response", "message"):
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


async def run_junie(task: str) -> str:
    """Run Junie CLI as a subprocess. Returns the answer text."""
    cmd = [
        JUNIE_BIN,
        "--task", task,
        "--project", VAULT_PATH,
        "--output-format", "json",
    ]

    retry_delays = [30, 60, 90]
    last_error = ""

    for attempt, delay in enumerate([0] + retry_delays):
        if attempt > 0:
            runs_logger.info(f"Rate limit retry {attempt}/3 after {delay}s delay")
            await asyncio.sleep(delay)

        try:
            result = await asyncio.to_thread(
                subprocess.run,
                cmd,
                capture_output=True,
                text=True,
                timeout=JUNIE_TIMEOUT,
            )
            output = result.stdout + result.stderr
            parsed = parse_junie_json(output)

            if has_rate_limit(output):
                last_error = output
                runs_logger.warning(f"Rate limit detected in Junie output (attempt {attempt + 1})")
                continue

            runs_logger.info(f"Junie exit code: {result.returncode}")
            if parsed:
                return parsed
            else:
                return f"Junie returned empty output (exit code {result.returncode})"

        except subprocess.TimeoutExpired:
            last_error = f"Junie timed out after {JUNIE_TIMEOUT}s"
            runs_logger.error(last_error)
            return last_error
        except Exception as e:
            last_error = str(e)
            runs_logger.error(f"Junie subprocess error: {e}")
            if attempt == 0:
                return f"Junie error: {e}"
            continue

    return f"Rate limit hit after {len(retry_delays)} retries. Try again later."


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

def format_reply(improved_prompt: str, junie_answer: str, fallback: bool) -> str:
    """Format the final reply as markdown with blockquote and answer."""
    lines = []
    if fallback:
        lines.append(f"> **Note:** Ollama was unreachable. Using original prompt.")
        lines.append(f"> Original prompt: {improved_prompt}")
    else:
        lines.append(f"> Improved prompt: {improved_prompt}")
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


async def stream_completion(content: str, model: str = "junie-workflow"):
    """Generate SSE events for streaming response."""
    data = json.dumps({
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": content},
                "finish_reason": None,
            }
        ],
    })
    yield sse_event(data)

    # Final chunk with finish_reason
    data = json.dumps({
        "id": f"chatcmpl-{int(time.time())}",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    })
    yield sse_event(data)
    yield "data: [DONE]\n\n"


async def stream_from_ollama(messages: list, model: str = "junie-workflow"):
    """Stream directly from Ollama, wrapping in OpenAI SSE format."""
    timestamp = int(time.time())
    comp_id = f"chatcmpl-{timestamp}"

    # Initial chunk with role
    data = json.dumps({
        "id": comp_id,
        "object": "chat.completion.chunk",
        "created": timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {"role": "assistant", "content": ""},
                "finish_reason": None,
            }
        ],
    })
    yield sse_event(data)

    # Stream content from Ollama
    async for chunk_content in ollama_chat_stream(messages):
        data = json.dumps({
            "id": comp_id,
            "object": "chat.completion.chunk",
            "created": timestamp,
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": chunk_content},
                    "finish_reason": None,
                }
            ],
        })
        yield sse_event(data)

    # Final chunk
    data = json.dumps({
        "id": comp_id,
        "object": "chat.completion.chunk",
        "created": timestamp,
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": "stop",
            }
        ],
    })
    yield sse_event(data)
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
                stream_from_ollama(messages),
                media_type="text/event-stream",
            )
        else:
            try:
                answer = await ollama_chat(messages)
                return make_completion_response(answer)
            except Exception as e:
                return make_completion_response(f"Ollama error: {e}")

    # Extract raw prompt from last user message
    raw_prompt = get_last_user_message(messages)
    if not raw_prompt:
        raise HTTPException(status_code=400, detail="No user message found")

    start_time = time.time()

    # Step 1: Improve the prompt
    improved_prompt, fallback = await build_prompt(raw_prompt)

    # Step 2: Run Junie with the improved prompt
    if not await junie_lock.acquire():
        # This shouldn't happen with asyncio.Lock, but just in case
        reply = "Junie is busy. Please try again in a moment."
    else:
        try:
            junie_answer = await run_junie(improved_prompt)
            reply = format_reply(improved_prompt, junie_answer, fallback)
        finally:
            junie_lock.release()

    duration = time.time() - start_time
    runs_logger.info(
        f"Request completed in {duration:.1f}s | Raw: {raw_prompt[:80]}... | "
        f"Improved: {improved_prompt[:80]}... | Fallback: {fallback}"
    )

    # Step 3: Return response
    if stream:
        return StreamingResponse(
            stream_completion(reply),
            media_type="text/event-stream",
        )
    else:
        return make_completion_response(reply)


# ─── Main ────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("orchestrator:app", host="0.0.0.0", port=PORT, reload=False)
