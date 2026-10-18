import os
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
    with TestClient(orchestrator.app) as c:
        yield c


HEADERS_AUTH = {"Authorization": "Bearer test-secret-key"}


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
        mock_prompt.return_value = ("Search for notes about Python", False)
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
    def test_streaming(self, mock_junie, mock_prompt, client):
        mock_prompt.return_value = ("Search for notes about Python", False)
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
        assert "data: " in text
        assert "[DONE]" in text
        # Parse SSE events
        events = [line for line in text.split("\n") if line.startswith("data: ")]
        assert len(events) >= 2  # At least content chunk + finish chunk

    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_streaming_sse_format(self, mock_junie, mock_prompt, client):
        mock_prompt.return_value = ("Search notes", False)
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

    def test_bypass_streaming(self, client):
        """Test that bypass streaming returns SSE format."""
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


# ─── Ollama Fallback Tests ──────────────────────────────────────────────────

class TestOllamaFallback:
    @patch("orchestrator.ollama_chat", side_effect=httpx.ConnectError("Connection refused"))
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_fallback_to_raw_prompt(self, mock_junie, mock_ollama, client):
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
        assert "Ollama was unreachable" in content
        assert "find notes" in content  # Raw prompt used


# ─── Lock Tests ──────────────────────────────────────────────────────────────

class TestLock:
    @patch("orchestrator.build_prompt", new_callable=AsyncMock)
    @patch("orchestrator.run_junie", new_callable=AsyncMock)
    def test_lock_basic(self, mock_junie, mock_prompt, client):
        """Test that the lock allows one request through successfully."""
        mock_prompt.return_value = ("Task", False)
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
        mock_prompt.return_value = ("Search notes", False)
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
            ["python", str(fake_junie)],
            capture_output=True,
            text=True,
            timeout=5,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout)
        assert "answer" in output
