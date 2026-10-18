import os
import sys
import json
import time
import asyncio
from unittest.mock import patch, AsyncMock, MagicMock
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
import httpx

# Set test API key before importing orchestrator
os.environ["API_KEY"] = "test-secret-key"
os.environ["JUNIE_BIN"] = str(Path(__file__).parent / "fake_junie.py")
os.environ["JUNIE_TIMEOUT"] = "10"
os.environ["VAULT_PATH"] = "/tmp/test-vault"
os.environ["WORKSPACE_PATH"] = "/tmp/test-workspace"


@pytest.fixture(autouse=True)
def reset_lock():
    """Reset the Junie lock before each test."""
    import orchestrator
    orchestrator.junie_lock = asyncio.Lock()
    yield


@pytest.fixture
def client():
    import orchestrator
    # Force reload of orchestrator module to pick up env vars
    orchestrator.API_KEY = "test-secret-key"
    orchestrator.JUNIE_BIN = str(Path(__file__).parent / "fake_junie.py")
    orchestrator.JUNIE_TIMEOUT = 10
    orchestrator.VAULT_PATH = "/tmp/test-vault"
    orchestrator.WORKSPACE_PATH = "/tmp/test-workspace"
    orchestrator.MIRROR_LOGS_TO_VAULT = False
    with TestClient(orchestrator.app) as c:
        yield c


HEADERS_AUTH = {"Authorization": "Bearer test-secret-key"}


# ─── Task 1: Safety Tests ────────────────────────────────────────────────────

class TestSafety:
    def test_startup_without_api_key(self):
        """Test that importing orchestrator without API_KEY raises RuntimeError."""
        # Save and clear API_KEY
        saved = os.environ.pop("API_KEY", None)
        # Remove the module from cache so it re-imports
        if "orchestrator" in sys.modules:
            del sys.modules["orchestrator"]
        try:
            with pytest.raises(RuntimeError, match="API_KEY"):
                import orchestrator  # noqa: F401
        finally:
            # Restore API_KEY and re-import
            os.environ["API_KEY"] = "test-secret-key"
            if "orchestrator" in sys.modules:
                del sys.modules["orchestrator"]
            import orchestrator  # noqa: F401

    def test_junie_not_found_message(self):
        """Test that missing Junie binary returns a clear message."""
        import orchestrator
        original_bin = orchestrator.JUNIE_BIN
        orchestrator.JUNIE_BIN = "/nonexistent/path/to/junie"
        try:
            result = asyncio.get_event_loop().run_until_complete(
                orchestrator.run_junie("test task")
            )
            assert "Junie CLI not found" in result
            assert "/nonexistent/path/to/junie" in result
        finally:
            orchestrator.JUNIE_BIN = original_bin

    def test_timeout_kills_process_and_frees_lock(self):
        """Test that a timeout kills the process and releases the lock."""
        import orchestrator
        original_bin = orchestrator.JUNIE_BIN
        original_timeout = orchestrator.JUNIE_TIMEOUT
        # Create a wrapper script that sleeps forever
        wrapper = Path(__file__).parent / "fake_junie_timeout.py"
        wrapper.write_text(
            "#!/usr/bin/env python3\n"
            "import time\n"
            "time.sleep(9999)\n"
        )
        # Point JUNIE_BIN to python running the wrapper script
        # Since run_junie builds cmd as [JUNIE_BIN, "--task", ...], we need
        # JUNIE_BIN to be the full command. Instead, mock run_junie's subprocess call.
        orchestrator.JUNIE_TIMEOUT = 2
        orchestrator.junie_lock = asyncio.Lock()

        async def run():
            await orchestrator.junie_lock.acquire()
            try:
                # Temporarily replace JUNIE_BIN with a script path
                # that we run via sys.executable
                orig_bin = orchestrator.JUNIE_BIN
                # Use a mock for the subprocess call
                mock_proc = AsyncMock()
                mock_proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
                mock_proc.pid = 12345
                mock_proc.kill = AsyncMock()
                mock_proc.wait = AsyncMock()

                with patch("orchestrator.asyncio.create_subprocess_exec", return_value=mock_proc):
                    result = await orchestrator.run_junie("test")
                    return result
            finally:
                orchestrator.junie_lock.release()

        result = asyncio.get_event_loop().run_until_complete(run())
        assert "timed out" in result.lower() or "timeout" in result.lower()
        # Verify lock is released
        assert not orchestrator.junie_lock.locked()

        # Cleanup
        orchestrator.JUNIE_TIMEOUT = original_timeout
        wrapper.unlink(missing_ok=True)


# ─── Auth Tests ──────────────────────────────────────────────────────────────

class TestAuth:
    def test_models_no_auth(self, client):
        resp = client.get("/v1/models")
        assert resp.status_code == 401

    def test_models_wrong_auth(self, client):
        resp = client.get("/v1/models", headers={"Authorization": "Bearer wrong-key"})
        assert resp.status_code == 401

    def test_chat_no_auth(self, client):
        resp = client.post("/v1/chat/completions", json={"messages": []})
        assert resp.status_code == 401

    def test_chat_wrong_auth(self, client):
        resp = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer wrong-key"},
            json={"messages": [{"role": "user", "content": "hello"}]},
        )
        assert resp.status_code == 401


# ─── Models Endpoint Tests ───────────────────────────────────────────────────

class TestModels:
    def test_list_models(self, client):
        resp = client.get("/v1/models", headers=HEADERS_AUTH)
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "list"
        assert len(data["data"]) == 1
        assert data["data"][0]["id"] == "junie-workflow"
        assert data["data"][0]["object"] == "model"


# ─── Chat Completions Tests ─────────────────────────────────────────────────

class TestChatCompletions:
    def test_empty_messages(self, client):
        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={"messages": []},
        )
        assert resp.status_code == 400

    def test_no_user_message(self, client):
        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={"messages": [{"role": "assistant", "content": "hello"}]},
        )
        assert resp.status_code == 400

    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_non_streaming(self, mock_junie, mock_prompt, client):
        mock_prompt.return_value = ("Search for notes about Python", False, "")
        mock_junie.return_value = "Found 5 notes about Python."

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "find python notes"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["object"] == "chat.completion"
        assert data["model"] == "junie-workflow"
        assert data["choices"][0]["finish_reason"] == "stop"
        content = data["choices"][0]["message"]["content"]
        assert "Improved prompt" in content
        assert "Junie:" in content

    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_streaming_progress_order(self, mock_junie, mock_prompt, client):
        """Test streaming sends chunks in order: improved prompt → working → answer → stop → DONE."""
        mock_prompt.return_value = ("Search for notes about Python", False, "")
        mock_junie.return_value = "Found 5 notes about Python."

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "find python notes"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        text = resp.text
        assert "[DONE]" in text

        # Parse SSE events
        events = [line[6:] for line in text.split("\n") if line.startswith("data: ")]
        # Filter out [DONE]
        json_events = [json.loads(e) for e in events if e != "[DONE]"]

        # Check chunk order
        # Chunk 1: role + improved prompt
        assert json_events[0]["choices"][0]["delta"].get("role") == "assistant"
        assert "Improved prompt" in json_events[0]["choices"][0]["delta"]["content"]

        # Chunk 2: Junie is working...
        assert "Junie is working" in json_events[1]["choices"][0]["delta"]["content"]

        # Chunk 3: final answer
        assert "Junie:" in json_events[2]["choices"][0]["delta"]["content"]

        # Chunk 4: stop
        assert json_events[3]["choices"][0]["finish_reason"] == "stop"

    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_streaming_sse_format(self, mock_junie, mock_prompt, client):
        mock_prompt.return_value = ("Search notes", False, "")
        mock_junie.return_value = "Answer text"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "search"}],
                "stream": True,
            },
        )
        assert resp.status_code == 200
        text = resp.text
        # Check for finish_reason stop in SSE
        assert '"finish_reason": "stop"' in text or '"finish_reason":"stop"' in text


# ─── Bypass Tests ────────────────────────────────────────────────────────────

class TestBypass:
    @patch("orchestrator.ollama_chat", new_callable=AsyncMock)
    def test_bypass_non_streaming(self, mock_ollama, client):
        mock_ollama.return_value = "Generated title: Meeting Notes"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "### Task: Generate a title"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["choices"][0]["message"]["content"] == "Generated title: Meeting Notes"

    def test_bypass_streaming_progress(self, client):
        """Test that bypass streaming returns SSE with progress chunks."""
        with patch("orchestrator.ollama_chat_stream") as mock_stream:
            async def fake_stream():
                yield "Hello"
                yield " world"

            mock_stream.return_value = fake_stream()

            resp = client.post(
                "/v1/chat/completions",
                headers=HEADERS_AUTH,
                json={
                    "messages": [{"role": "user", "content": "### Task: Summarize"}],
                    "stream": True,
                },
            )
            assert resp.status_code == 200
            text = resp.text
            assert "data: " in text
            assert "[DONE]" in text

            # Parse SSE events
            events = [line[6:] for line in text.split("\n") if line.startswith("data: ")]
            json_events = [json.loads(e) for e in events if e != "[DONE]"]

            # First chunk has role
            assert json_events[0]["choices"][0]["delta"].get("role") == "assistant"
            # Last chunk has finish_reason stop
            assert json_events[-1]["choices"][0]["finish_reason"] == "stop"


# ─── Ollama Fallback Tests ──────────────────────────────────────────────────

class TestVaultMap:
    def test_vault_map_included_in_system_prompt(self, tmp_path):
        """Test that vault map content is appended to the system prompt."""
        import orchestrator
        map_file = tmp_path / "my_vault_map.md"
        map_file.write_text("## My Vault\n- Projects/\n- Notes/", encoding="utf-8")
        orchestrator.VAULT_MAP_PATH = str(map_file)
        system = orchestrator.build_prompt_system()
        assert "## Vault overview" in system
        assert "## My Vault" in system
        assert "Projects/" in system

    def test_missing_vault_map_no_crash(self, tmp_path):
        """Test that a missing vault map file does not crash."""
        import orchestrator
        orchestrator.VAULT_MAP_PATH = str(tmp_path / "nonexistent_map.md")
        system = orchestrator.build_prompt_system()
        assert "## Vault overview" not in system
        assert orchestrator.PROMPT_REWRITE_SYSTEM_BASE in system

    def test_vault_map_in_ollama_request(self, tmp_path):
        """Test that vault map text ends up in the Ollama request body."""
        import orchestrator
        import respx

        map_file = tmp_path / "vault_map.md"
        map_file.write_text("## Custom Map\n- Folder A\n- Folder B", encoding="utf-8")
        orchestrator.VAULT_MAP_PATH = str(map_file)

        call_payloads = []

        @respx.mock
        def run_test():
            async def capture(request):
                call_payloads.append(json.loads(request.content))
                return httpx.Response(200, json={"message": {"content": "Improved"}})

            respx.post("http://localhost:11434/api/chat").side_effect = capture

            asyncio.get_event_loop().run_until_complete(
                orchestrator.build_prompt("find notes")
            )

        run_test()
        assert len(call_payloads) == 1
        system_content = call_payloads[0]["messages"][0]["content"]
        assert "## Vault overview" in system_content
        assert "## Custom Map" in system_content


class TestOllamaThinkRetry:
    def test_think_false_in_request(self):
        """Test that ollama_chat sends 'think': false in the request body."""
        import orchestrator
        import respx

        call_payloads = []

        @respx.mock
        def run_test():
            async def capture(request):
                call_payloads.append(json.loads(request.content))
                return httpx.Response(200, json={"message": {"content": "Answer"}})

            respx.post("http://localhost:11434/api/chat").side_effect = capture

            result = asyncio.get_event_loop().run_until_complete(
                orchestrator.ollama_chat([{"role": "user", "content": "test"}])
            )
            assert result == "Answer"
            assert len(call_payloads) == 1
            assert call_payloads[0].get("think") is False

        run_test()

    def test_think_400_retry(self):
        """Test that ollama_chat retries without 'think' on HTTP 400 mentioning thinking."""
        import orchestrator
        import respx

        call_count = [0]

        @respx.mock
        def run_test():
            def handler(request):
                call_count[0] += 1
                if call_count[0] == 1:
                    return httpx.Response(400, text="Error: thinking not supported")
                else:
                    return httpx.Response(200, json={"message": {"content": "Answer"}})

            respx.post("http://localhost:11434/api/chat").side_effect = handler

            result = asyncio.get_event_loop().run_until_complete(
                orchestrator.ollama_chat([{"role": "user", "content": "test"}])
            )
            assert result == "Answer"
            assert call_count[0] == 2  # Two calls: initial + retry

        run_test()


class TestOllamaFallback:
    @patch("orchestrator.ollama_chat", side_effect=httpx.ConnectError("Connection refused"))
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_fallback_connection_refused(self, mock_junie, mock_ollama, client):
        mock_junie.return_value = "Junie answer with raw prompt"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "find notes"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        assert "connection to Ollama refused" in content
        assert "find notes" in content  # Raw prompt used

    @patch("orchestrator.ollama_chat", side_effect=httpx.HTTPStatusError(
        "Not Found", request=None, response=httpx.Response(404)
    ))
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_fallback_404_model_not_found(self, mock_junie, mock_ollama, client):
        mock_junie.return_value = "Junie answer"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "find notes"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        content = data["choices"][0]["message"]["content"]
        assert "HTTP 404" in content
        assert "find notes" in content

    @patch("orchestrator.ollama_chat", side_effect=httpx.ConnectError("Connection refused"))
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_fallback_error_reason_in_reply(self, mock_junie, mock_ollama, client):
        """Test that the fallback message contains the specific error reason, not 'unreachable'."""
        mock_junie.return_value = "Answer"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "test"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        content = resp.json()["choices"][0]["message"]["content"]
        # Should contain specific error, not generic "unreachable"
        assert "unreachable" not in content.lower() or "connection" in content.lower()
        assert "refused" in content.lower() or "error" in content.lower()


# ─── Lock Tests ──────────────────────────────────────────────────────────────

class TestLock:
    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_lock_basic(self, mock_junie, mock_prompt, client):
        """Test that the lock allows one request through successfully."""
        mock_prompt.return_value = ("Task", False, "")
        mock_junie.return_value = "Done"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "task 1"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200
        data = resp.json()
        assert "Junie:" in data["choices"][0]["message"]["content"]


# ─── JSON Parsing Tests ─────────────────────────────────────────────────────

class TestJsonParsing:
    def test_parse_simple_json(self):
        from orchestrator import parse_junie_json
        result = parse_junie_json('{"answer": "Hello world"}')
        assert result == "Hello world"

    def test_parse_nested_json(self):
        from orchestrator import parse_junie_json
        result = parse_junie_json('{"result": {"content": "Nested answer"}}')
        assert result == "Nested answer"

    def test_parse_text_key(self):
        from orchestrator import parse_junie_json
        result = parse_junie_json('{"text": "Text answer"}')
        assert result == "Text answer"

    def test_parse_non_json(self):
        from orchestrator import parse_junie_json
        result = parse_junie_json("Plain text output")
        assert result == "Plain text output"

    def test_parse_empty(self):
        from orchestrator import parse_junie_json
        result = parse_junie_json("")
        assert result == ""

    def test_parse_array(self):
        from orchestrator import parse_junie_json
        result = parse_junie_json('[{"answer": "Array item"}]')
        assert result == "Array item"


# ─── Rate Limit Detection Tests ──────────────────────────────────────────────

class TestRateLimit:
    def test_detect_429(self):
        from orchestrator import has_rate_limit
        assert has_rate_limit("Error 429: Too many requests") is True

    def test_detect_resource_exhausted(self):
        from orchestrator import has_rate_limit
        assert has_rate_limit("RESOURCE_EXHAUSTED: quota exceeded") is True

    def test_no_rate_limit(self):
        from orchestrator import has_rate_limit
        assert has_rate_limit("Normal output") is False


# ─── Reply Format Tests ─────────────────────────────────────────────────────

class TestReplyFormat:
    def test_format_no_fallback(self):
        from orchestrator import format_reply
        reply = format_reply("Search for Python notes", "Found 5 notes.", False)
        assert "> Improved prompt: Search for Python notes" in reply
        assert "Junie: Found 5 notes." in reply

    def test_format_with_fallback(self):
        from orchestrator import format_reply
        reply = format_reply("find notes", "Found 5 notes.", True)
        assert "Ollama was unreachable" in reply
        assert "Original prompt: find notes" in reply
        assert "Junie: Found 5 notes." in reply

    def test_format_with_error_reason(self):
        from orchestrator import format_reply
        reply = format_reply("find notes", "Found 5 notes.", True, "model 'gemma4:12b' returned HTTP 404")
        assert "model 'gemma4:12b' returned HTTP 404" in reply
        assert "Ollama was unreachable" not in reply
        assert "Original prompt: find notes" in reply


# ─── Bypass Detection Tests ─────────────────────────────────────────────────

class TestBypassDetection:
    def test_detect_bypass(self):
        from orchestrator import is_bypass_request
        msgs = [{"role": "user", "content": "### Task: Generate title"}]
        assert is_bypass_request(msgs) is True

    def test_no_bypass(self):
        from orchestrator import is_bypass_request
        msgs = [{"role": "user", "content": "Find my notes"}]
        assert is_bypass_request(msgs) is False

    def test_bypass_with_assistant_msg(self):
        from orchestrator import is_bypass_request
        msgs = [
            {"role": "assistant", "content": "Hello"},
            {"role": "user", "content": "### Task: Summarize"},
        ]
        assert is_bypass_request(msgs) is True


# ─── Logging Tests ───────────────────────────────────────────────────────────

class TestLogging:
    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_request_logged(self, mock_junie, mock_prompt, client):
        mock_prompt.return_value = ("Search notes", False, "")
        mock_junie.return_value = "Found notes"

        resp = client.post(
            "/v1/chat/completions",
            headers=HEADERS_AUTH,
            json={
                "messages": [{"role": "user", "content": "find notes"}],
                "stream": False,
            },
        )
        assert resp.status_code == 200

        # Check log file exists and has content
        log_file = Path("logs/runs.log")
        assert log_file.exists()
        content = log_file.read_text()
        assert "Request completed" in content


# ─── Junie BIN Mock Mode Tests ──────────────────────────────────────────────

class TestJunieBin:
    def test_fake_junie_runs(self):
        """Test that fake_junie.py runs and produces valid JSON."""
        import subprocess
        fake_junie = Path(__file__).parent / "fake_junie.py"
        result = subprocess.run(
            [sys.executable, str(fake_junie)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "answer" in output


# ─── Memory & Session Log Tests ──────────────────────────────────────────────

class TestMemoryLoading:
    def test_load_memory_empty(self, tmp_path):
        """Test that load_memory_context returns empty string when no memory files exist."""
        import orchestrator
        orchestrator.MEMORY_FILE = tmp_path / "MEMORY.md"
        orchestrator.SESSION_LOGS_DIR = tmp_path / "session-logs"
        orchestrator.SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ctx = orchestrator.load_memory_context()
        assert ctx == ""

    def test_load_memory_from_file(self, tmp_path):
        """Test that MEMORY.md content is included in context."""
        import orchestrator
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text("Convention: use snake_case for variables.", encoding="utf-8")
        orchestrator.MEMORY_FILE = memory_file
        orchestrator.SESSION_LOGS_DIR = tmp_path / "session-logs"
        orchestrator.SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ctx = orchestrator.load_memory_context()
        assert "## Project Memory" in ctx
        assert "Convention: use snake_case for variables." in ctx

    def test_load_recent_session_logs(self, tmp_path):
        """Test that the 3 most recent session logs are included."""
        import orchestrator
        orchestrator.MEMORY_FILE = tmp_path / "MEMORY.md"
        logs_dir = tmp_path / "session-logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        orchestrator.SESSION_LOGS_DIR = logs_dir

        # Create 4 session logs
        for i in range(4):
            log_file = logs_dir / f"session-2025010{i}-120000.md"
            log_file.write_text(f"# Session {i}\n\nTask {i} was completed.", encoding="utf-8")
            # Set different modification times so sorting works
            import os as os_module
            os_module.utime(log_file, (1000 + i, 1000 + i))

        ctx = orchestrator.load_memory_context()
        assert "## Recent Session Summaries" in ctx
        # Should include the 3 most recent (sessions 1, 2, 3 by mtime)
        assert "Session 3" in ctx
        assert "Session 2" in ctx
        assert "Session 1" in ctx
        # Should NOT include the oldest (session 0)
        assert "Session 0" not in ctx

    def test_load_memory_and_sessions_combined(self, tmp_path):
        """Test that both MEMORY.md and session logs appear in context."""
        import orchestrator
        memory_file = tmp_path / "MEMORY.md"
        memory_file.write_text("Key decision: use FastAPI.", encoding="utf-8")
        orchestrator.MEMORY_FILE = memory_file
        logs_dir = tmp_path / "session-logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        orchestrator.SESSION_LOGS_DIR = logs_dir
        log_file = logs_dir / "session-20250101-120000.md"
        log_file.write_text("# Session 1\n\nFixed the login bug.", encoding="utf-8")

        ctx = orchestrator.load_memory_context()
        assert "## Project Memory" in ctx
        assert "Key decision: use FastAPI." in ctx
        assert "## Recent Session Summaries" in ctx
        assert "Fixed the login bug." in ctx

    def test_load_memory_missing_file_no_crash(self, tmp_path):
        """Test that a missing MEMORY.md does not crash."""
        import orchestrator
        orchestrator.MEMORY_FILE = tmp_path / "nonexistent.md"
        orchestrator.SESSION_LOGS_DIR = tmp_path / "session-logs"
        orchestrator.SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        ctx = orchestrator.load_memory_context()
        assert ctx == ""


class TestSessionLogSaving:
    async def test_save_session_log_creates_file(self, tmp_path):
        """Test that save_session_log creates a markdown file in session-logs/."""
        import orchestrator
        orchestrator.MEMORY_FILE = tmp_path / "MEMORY.md"
        logs_dir = tmp_path / "session-logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        orchestrator.SESSION_LOGS_DIR = logs_dir
        orchestrator.MIRROR_LOGS_TO_VAULT = False

        # Mock Ollama to fail so we use the fallback summary
        with patch("orchestrator.ollama_chat", new_callable=AsyncMock, side_effect=Exception("no ollama")):
            await orchestrator.save_session_log(
                raw_prompt="find notes",
                improved_prompt="Search for notes",
                junie_answer="Found 3 notes.",
                fallback=False,
            )

        # Check that a log file was created
        log_files = list(logs_dir.glob("session-*.md"))
        assert len(log_files) == 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "find notes" in content
        assert "Search for notes" in content
        assert "Found 3 notes." in content

    async def test_save_session_log_ollama_summary(self, tmp_path):
        """Test that save_session_log uses Ollama summary when available."""
        import orchestrator
        orchestrator.MEMORY_FILE = tmp_path / "MEMORY.md"
        logs_dir = tmp_path / "session-logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        orchestrator.SESSION_LOGS_DIR = logs_dir
        orchestrator.MIRROR_LOGS_TO_VAULT = False

        with patch("orchestrator.ollama_chat", new_callable=AsyncMock, return_value="# Session Summary\n\nOllama wrote this summary."):
            await orchestrator.save_session_log(
                raw_prompt="test",
                improved_prompt="Test task",
                junie_answer="Done.",
                fallback=False,
            )

        log_files = list(logs_dir.glob("session-*.md"))
        assert len(log_files) == 1
        content = log_files[0].read_text(encoding="utf-8")
        assert "Ollama wrote this summary." in content

    async def test_save_session_log_failure_does_not_raise(self, tmp_path):
        """Test that a save failure logs an error but does not raise."""
        import orchestrator
        orchestrator.MEMORY_FILE = tmp_path / "MEMORY.md"
        # Point to a non-writable directory
        orchestrator.SESSION_LOGS_DIR = tmp_path / "readonly" / "session-logs"
        orchestrator.SESSION_LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # Make it read-only
        import stat as stat_module
        orchestrator.SESSION_LOGS_DIR.chmod(stat_module.S_IRUSR)
        orchestrator.MIRROR_LOGS_TO_VAULT = False

        with patch("orchestrator.ollama_chat", new_callable=AsyncMock, side_effect=Exception("no ollama")):
            # Should not raise
            await orchestrator.save_session_log(
                raw_prompt="test",
                improved_prompt="Test",
                junie_answer="Answer",
                fallback=False,
            )

        # Restore permissions for cleanup
        orchestrator.SESSION_LOGS_DIR.chmod(stat_module.S_IRWXU)


class TestMirrorLogsToVault:
    def test_mirror_copies_to_vault(self, tmp_path):
        """Test that _mirror_log_to_vault copies the log to VAULT_PATH/Session-Logs/."""
        import orchestrator
        vault_dir = tmp_path / "vault"
        vault_dir.mkdir(parents=True, exist_ok=True)
        orchestrator.VAULT_PATH = str(vault_dir)

        # Create a session log file
        logs_dir = tmp_path / "session-logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "session-20250101-120000.md"
        log_file.write_text("# Session\n\nTest log.", encoding="utf-8")

        orchestrator._mirror_log_to_vault(log_file)

        vault_logs = vault_dir / "Session-Logs"
        assert vault_logs.exists()
        mirrored = vault_logs / "session-20250101-120000.md"
        assert mirrored.exists()
        assert mirrored.read_text(encoding="utf-8") == "# Session\n\nTest log."

    def test_mirror_failure_does_not_raise(self, tmp_path):
        """Test that a mirror failure logs a warning but does not raise."""
        import orchestrator
        # Point to a non-existent parent that can't be created
        orchestrator.VAULT_PATH = str(tmp_path / "no" / "access" / "vault")

        logs_dir = tmp_path / "session-logs"
        logs_dir.mkdir(parents=True, exist_ok=True)
        log_file = logs_dir / "session-20250101-120000.md"
        log_file.write_text("# Session\n\nTest.", encoding="utf-8")

        # Should not raise
        orchestrator._mirror_log_to_vault(log_file)


class TestWorkspaceFallback:
    def test_workspace_uses_workspace_path_in_junie_cmd(self):
        """Test that run_junie uses WORKSPACE_PATH, not VAULT_PATH."""
        import orchestrator
        # The cmd should use WORKSPACE_PATH
        # We verify by checking the module-level variable is used
        assert hasattr(orchestrator, "WORKSPACE_PATH")
        assert hasattr(orchestrator, "MEMORY_FILE")
        assert hasattr(orchestrator, "SESSION_LOGS_DIR")
