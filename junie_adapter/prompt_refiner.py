import logging

import httpx

from .config import OrchestratorConfig

runs_logger = logging.getLogger("runs")

PROMPT_REWRITE_SYSTEM_BASE = """You rewrite rough requests into one precise task for an AI agent. The agent works in a workspace directory for project work, remembers context via MEMORY.md and session logs, and uses a vault for searchable notes. Keep my intent exactly.

Follow these rules:

- The workspace (WORKSPACE_PATH) is where the agent does project work. The vault (VAULT_PATH) contains the user's Obsidian notes and knowledge — read it for background when relevant.
- Before creating a new note, the agent must first search the vault for existing notes on the topic and extend them instead of creating duplicates.
- If the request is ambiguous (for example which project is meant), the agent must state its assumption in its answer instead of guessing silently.
- If the request is a question, the task must be read-only: answer in the chat and do not create or edit any files.
- Do not invent details the user did not give.

Format the rewritten task with each of the following in its own paragraph, separated by blank lines:

Goal: What the task aims to achieve.
Scope: Which folders or notes are in scope.
Constraints: Never delete files; write new files in the workspace unless told otherwise; do not edit notes outside the scope.
Expected Output: The format of the deliverable.

Return ONLY the rewritten task, no commentary."""


def build_prompt_system(context: str = "", vault_map_content: str = "") -> str:
    """Build the full promptbuilder system prompt, optionally including vault map and memory context."""
    system = PROMPT_REWRITE_SYSTEM_BASE

    if context:
        system += "\n\n## Context from previous work\n\n" + context

    if vault_map_content:
        system += "\n\n## Vault overview\n\n" + vault_map_content

    return system


async def ollama_chat(
    messages: list,
    config: OrchestratorConfig,
    model: str | None = None,
    try_think: bool = True,
) -> str:
    """Call Ollama /api/chat and return the assistant's message text.
    If try_think is True, sends "think": false and retries without it on HTTP 400."""
    model = model or config.ollama_model
    async with httpx.AsyncClient(timeout=120) as client:
        payload = {
            "model": model,
            "messages": messages,
            "stream": False,
        }
        if try_think:
            payload["think"] = False

        resp = await client.post(
            f"{config.ollama_url}/api/chat",
            json=payload,
        )

        # If HTTP 400 mentions "thinking", retry without "think": false
        if resp.status_code == 400 and try_think:
            text = resp.text.lower()
            if "think" in text:
                runs_logger.info(
                    "Ollama returned 400 mentioning thinking, retrying without 'think': false"
                )
                payload.pop("think", None)
                resp = await client.post(
                    f"{config.ollama_url}/api/chat",
                    json=payload,
                )

        resp.raise_for_status()
        body = resp.json()
    return body.get("message", {}).get("content", "")


async def ollama_chat_stream(
    messages: list, config: OrchestratorConfig, model: str | None = None
):
    """Call Ollama /api/chat with streaming and yield content chunks."""
    model = model or config.ollama_model
    async with (
        httpx.AsyncClient(timeout=120) as client,
        client.stream(
            "POST",
            f"{config.ollama_url}/api/chat",
            json={
                "model": model,
                "messages": messages,
                "stream": True,
            },
        ) as resp,
    ):
        resp.raise_for_status()
        async for line in resp.aiter_lines():
            if not line.strip():
                continue
            try:
                import json

                chunk = json.loads(line)
                content = chunk.get("message", {}).get("content", "")
                if content:
                    yield content
            except json.JSONDecodeError:
                continue


async def refine_prompt(
    raw: str, config: OrchestratorConfig, context: str = "", vault_map_content: str = ""
) -> tuple[str, bool, str]:
    """Rewrite a raw prompt using Ollama. Returns (improved_prompt, fallback_flag, error_reason).
    On error, returns (raw, True, reason) where reason describes the specific error."""
    try:
        system_prompt = build_prompt_system(context, vault_map_content)
        improved = await ollama_chat(
            [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": raw},
            ],
            config,
        )
        if improved.strip():
            return improved.strip(), False, ""
    except httpx.HTTPStatusError as e:
        status = e.response.status_code
        reason = f"model '{config.ollama_model}' returned HTTP {status}"
        runs_logger.warning(f"Ollama prompt rewrite failed: {reason}")
        return raw, True, reason
    except httpx.ConnectError:
        reason = f"connection to Ollama refused ({config.ollama_url})"
        runs_logger.warning(f"Ollama prompt rewrite failed: {reason}")
        return raw, True, reason
    except httpx.ConnectTimeout:
        reason = f"connection to Ollama timed out ({config.ollama_url})"
        runs_logger.warning(f"Ollama prompt rewrite failed: {reason}")
        return raw, True, reason
    except Exception as e:
        reason = f"Ollama error: {e}"
        runs_logger.warning(f"Ollama prompt rewrite failed: {reason}")
        return raw, True, reason
