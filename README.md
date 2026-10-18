# Junie Orchestrator

A Python FastAPI service that connects [Open WebUI](https://github.com/open-webui/open-webui) to [Junie CLI](https://github.com/junie-ai/junie) through a prompt-improvement step using a local Ollama model. Everything runs locally — no paid APIs or cloud services.

## Flow

```
Open WebUI → orchestrator (OpenAI-compatible API)
  → 1) Prompt builder: local Ollama model rewrites the prompt
  → 2) Junie CLI runs headless on your Obsidian vault with the improved prompt
  → 3) Junie's answer returns to Open WebUI as a chat reply
```

The improved prompt runs immediately with no approval step.

## Before you start

- **Linux machine or VM** — This guide assumes Debian/Ubuntu-style Linux (it uses `systemd`, `ufw`, and `cifs` for mounting).
- **Junie CLI installed and logged in** — Install Junie CLI and log in as the **same Linux user** you'll run the orchestrator as. Junie needs access to its saved login session in your home directory.
- **Ollama installed** — You need Ollama running on this machine. `setup.sh` can pull the model for you, but it doesn't install Ollama itself.
- **Obsidian vault reachable** — Your vault must be accessible on this machine (for example, a mounted NAS share). See the next section.

## Mounting your vault

Before running `setup.sh`, your Obsidian vault must be mounted on this machine. This is **required** — the orchestrator needs to read and write your notes.

### One-time mount

```bash
sudo mount -t cifs //nas-ip/share /mnt/vault -o username=...,password=...
```

### Persistent mount (survives reboots)

Add an entry to `/etc/fstab`:

```
//nas-ip/share /mnt/vault cifs username=...,password=...,x-systemd.automount 0 0
```

Then run `sudo mount -a` to apply.

**Tip:** Start with a scratch copy of your vault for your first runs, so you can verify the orchestrator's behavior before running against your real vault.

## Quick Start

Get up and running in three steps:

```bash
# 1. Clone the repo
git clone https://github.com/LukaG712/JunieOrchestrator.git
cd JunieOrchestrator

# 2. Run the setup script (installs dependencies, configures .env, pulls the model)
./setup.sh

# 3. Start the server
./start.sh
```

> **Important:** Make sure the service runs as the **same Linux user** that logged in to Junie — Junie needs access to its saved login session in your home directory.

Then in Open WebUI, add a new **OpenAI API** connection with:

- **Base URL**: `http://<linux-machine-ip>:8000/v1`
- **API Key**: The key printed by `setup.sh`

That's it — you're ready to chat.

## Manual Setup (Advanced)

If you prefer to set things up by hand, or need to troubleshoot, follow the steps below.

### Prerequisites

- Python 3.11+
- [Junie CLI](https://github.com/junie-ai/junie) installed and in your PATH
- [Ollama](https://ollama.ai/) running locally with a model pulled (default: `gemma4:12b`)

### Setup

```bash
# Clone or copy the project
cd /path/to/orchestrator

# Create a virtual environment and install dependencies
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Configure environment variables
cp .env.example .env
# Edit .env and set at least API_KEY
```

### Environment Variables

| Variable | Default | Description |
|---|---|---|
| `OLLAMA_URL` | `http://localhost:11434` | Ollama API base URL |
| `OLLAMA_MODEL` | `gemma4:12b` | Model for prompt improvement |
| `VAULT_PATH` | `/mnt/vault` | Path to your Obsidian vault |
| `VAULT_MAP_PATH` | `vault_map.md` | Path to optional vault map file |
| `API_KEY` | *(required)* | Shared secret for Bearer token auth |
| `JUNIE_TIMEOUT` | `1800` | Timeout in seconds for Junie runs |
| `PORT` | `8000` | Port for uvicorn |
| `JUNIE_BIN` | *(none)* | Override Junie binary path (for testing) |

**Important:** The service refuses to start if `API_KEY` is unset or empty.

### Running

#### Development

```bash
source .venv/bin/activate
uvicorn orchestrator:app --host 0.0.0.0 --port 8000
```

Or simply run `./start.sh`.

#### Production (systemd)

The easiest way is to run the included script, which fills in your user and paths automatically:

```bash
sudo ./install_service.sh
```

Or create `/etc/systemd/system/junie-orchestrator.service` manually:

```ini
[Unit]
Description=Junie Orchestrator Service
After=network.target

[Service]
Type=simple
User=your-user
Environment=HOME=/home/your-user
WorkingDirectory=/path/to/orchestrator
EnvironmentFile=/path/to/orchestrator/.env
ExecStart=/path/to/orchestrator/.venv/bin/uvicorn orchestrator:app --host 0.0.0.0 --port 8000
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
```

**Important:** The service must run as the **same Linux user** that logged in to Junie, with `HOME` set correctly. Junie needs access to its saved login session, which lives in the user's home directory. Replace `your-user` with your actual username.

Then enable and start:

```bash
sudo systemctl daemon-reload
sudo systemctl enable junie-orchestrator
sudo systemctl start junie-orchestrator
```

## Open WebUI Configuration

### Adding the Connection

1. Go to **Admin Settings** → **Connections**
2. Add a new **OpenAI API** connection
3. Set the following:
   - **Base URL**: `http://<linux-machine-ip>:8000/v1`
   - **API Key**: The value you set for `API_KEY` in `.env`
4. Save and test the connection

### Setting the Task Model

Open WebUI sends background requests (chat title and tag generation) that should bypass Junie. To ensure these use a different model:

1. Go to **Admin Settings** → **Interface**
2. Set the **Task Model** to a different model (e.g., your Ollama model directly)
3. This ensures background tasks don't consume Junie resources

The orchestrator also detects `### Task:` prefixed messages and forwards them directly to Ollama, providing an extra layer of protection.

### Open WebUI Timeout

Long Junie runs may exceed Open WebUI's client-side timeout. Check Open WebUI's documentation for the `AIOHTTP_CLIENT_TIMEOUT` setting (or equivalent) and increase it to match or exceed your `JUNIE_TIMEOUT` value. The default is 1800 seconds (30 minutes).

## Vault Map (Optional)

Create a `vault_map.md` file next to `orchestrator.py` (or set `VAULT_MAP_PATH` to point to it) to help the prompt builder understand your vault structure. See `vault_map.example.md` for a template.

When present, the vault map is appended to the prompt improvement system prompt under the heading "Vault overview", giving the AI context about your folder structure and note types.

## API Endpoints

### GET /v1/models

Returns the available model list.

**Auth:** `Authorization: Bearer <API_KEY>` required.

**Response:**
```json
{
  "object": "list",
  "data": [
    {
      "id": "junie-workflow",
      "object": "model"
    }
  ]
}
```

### POST /v1/chat/completions

Process a chat completion request through the orchestrator pipeline.

**Auth:** `Authorization: Bearer <API_KEY>` required.

**Request:**
```json
{
  "messages": [
    {"role": "user", "content": "Find all notes about project planning"}
  ],
  "stream": false
}
```

**Response (non-streaming):**
```json
{
  "id": "chatcmpl-1234567890",
  "object": "chat.completion",
  "created": 1234567890,
  "model": "junie-workflow",
  "choices": [
    {
      "index": 0,
      "message": {
        "role": "assistant",
        "content": "> Improved prompt: Search the Obsidian vault for all notes related to project planning...\n\nJunie: Found 5 relevant notes..."
      },
      "finish_reason": "stop"
    }
  ]
}
```

**Response (streaming):** Server-Sent Events (SSE) with progress chunks:
1. First chunk: the improved prompt
2. Second chunk: "Junie is working..."
3. Third chunk: the final answer
4. Final chunk: `finish_reason: "stop"` followed by `data: [DONE]`

## Additional Setup (Advanced)

Before running the orchestrator, you may need to:

1. **Open the firewall** for the orchestrator port:
   ```bash
   sudo ufw allow 8000/tcp
   ```
   Or configure your cloud/VM firewall rules accordingly.

### Ollama on a Remote Machine

If Ollama runs on a different machine than the orchestrator:

1. Set `OLLAMA_URL` to the Ollama machine's address, e.g., `http://192.168.1.100:11434`.
2. On the Ollama machine, make Ollama listen on the network:
   ```bash
   OLLAMA_HOST=0.0.0.0 ollama serve
   ```
   Or set `OLLAMA_HOST=0.0.0.0` in your Ollama service configuration.
3. Allow Ollama's port (default 11434) in the firewall for your home network only:
   ```bash
   sudo ufw allow from 192.168.1.0/24 to any port 11434
   ```

## Testing

Run the test suite (uses a mock Junie, no real Junie calls):

```bash
pytest tests/ -v
```

The tests use `tests/fake_junie.py` as a mock Junie binary and mock Ollama calls, so you don't need Ollama or Junie running to run tests.

## Project Structure

```
orchestrator.py           # Main FastAPI app (all routes, services, utilities)
.env.example              # Environment variable template
setup.sh                  # One-time setup script (prerequisites, venv, .env, API key)
start.sh                  # Start the server (activates venv, runs uvicorn)
install_service.sh        # Install as a systemd background service
vault_map.example.md      # Example vault map file
requirements.txt          # Python dependencies
README.md                 # This file
tests/
  __init__.py
  test_orchestrator.py    # Pytest test suite
  fake_junie.py           # Mock Junie script for testing
logs/                     # Created at runtime; contains runs.log
```

## Reply Format

The orchestrator returns markdown with two parts:

1. **Improved prompt** — shown as a blockquote (`> Improved prompt: ...`)
2. **Junie's answer** — shown as `Junie: ...`

If Ollama fails, the reply states the specific reason (e.g., `model 'gemma4:12b' returned HTTP 404` or `connection to Ollama refused`).

## Rate Limiting

If Junie returns a rate limit error (`429` or `RESOURCE_EXHAUSTED`), the orchestrator automatically retries up to 3 times with increasing delays (30s, 60s, 90s). After all retries are exhausted, it returns a friendly "rate limit hit, try again later" message.

## Single-Run Lock

Only one Junie process runs at a time. If a request arrives while Junie is busy, it waits for the lock. This prevents resource contention and ensures clean vault access.

## Logging

Every request is logged to `logs/runs.log` with:
- Timestamp
- Raw prompt
- Improved prompt
- Junie exit code
- Request duration

## Troubleshooting

### Junie not logged in (service running as the wrong user)

If you get errors like "Junie not logged in" or "authentication failed", the service is probably running as a different user than the one that logged in to Junie. Check with `sudo systemctl status junie-orchestrator` and make sure the `User=` line matches the Linux user that ran `junie login`. Junie stores its login session in the user's home directory.

### Vault path is empty or not found

If Junie returns empty results or complains about a missing vault, your NAS share may not be mounted. Check with `ls /mnt/vault` (or your `VAULT_PATH`). If the directory is empty, run `sudo mount -a` to apply your `/etc/fstab` entries, or mount the share manually.

### Open WebUI times out on long runs

Junie can take several minutes to complete complex tasks. If Open WebUI shows a timeout error, increase the `AIOHTTP_CLIENT_TIMEOUT` setting in Open WebUI to match or exceed your `JUNIE_TIMEOUT` value (default 1800 seconds / 30 minutes). See Open WebUI's documentation for details.
