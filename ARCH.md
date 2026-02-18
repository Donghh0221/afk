# AFK — Architecture

## Design Principles

- **Messenger-agnostic**: Core logic depends only on the MessengerPort interface. Swappable between Telegram/Slack/native app.
- **STT-agnostic**: Speech recognition abstracted via STTPort. Swappable between Whisper API/local/Deepgram.
- **Always-on daemon**: Runs 24/7 as a launchd daemon on Mac mini. Accessible from any device.

## System Architecture

```
📱 Phone         💻 MacBook        🖥️ Mac mini (AFK Server)
│                │                 │
│ Telegram       │ Telegram        │ ┌──────────────────────────┐
│                │ + CLI (future)  │ │     AFK Daemon           │
│                │                 │ │                          │
└────────────────┴────────────────┤ │  ┌────────────────────┐  │
                                  │ │  │  MessengerPort     │  │
        Telegram Bot API          │ │  │  ┌──────────────┐  │  │
        ──────────────────────────┤ │  │  │ Telegram     │  │  │
                                  │ │  │  │ Adapter      │  │  │
                                  │ │  │  └──────────────┘  │  │
                                  │ │  └────────┬───────────┘  │
                                  │ │           │              │
                                  │ │  ┌────────▼───────────┐  │
                                  │ │  │    Orchestrator     │  │
                                  │ │  │                     │  │
                                  │ │  │  ┌──────────────┐   │  │
                                  │ │  │  │ STTPort      │   │  │
                                  │ │  │  │ (Whisper)    │   │  │
                                  │ │  │  └──────────────┘   │  │
                                  │ │  └────────┬────────────┘  │
                                  │ │           │              │
                                  │ │  ┌────────▼───────────┐  │
                                  │ │  │  Session Manager    │  │
                                  │ │  │                     │  │
                                  │ │  │  ┌──────┐ ┌──────┐ │  │
                                  │ │  │  │Ses A │ │Ses B │ │  │
                                  │ │  │  └──┬───┘ └──┬───┘ │  │
                                  │ │  └─────┼────────┼─────┘  │
                                  │ │        │        │        │
                                  │ └────────┼────────┼────────┘
                                  │          │        │
                                  │   ┌──────▼──┐ ┌──▼───────┐
                                  │   │Claude   │ │Claude    │
                                  │   │Code CLI │ │Code CLI  │
                                  │   └─────────┘ └──────────┘
```

## Module Structure

```
afk/
├── main.py                     # Entry point, component wiring, daemon startup/shutdown
├── config.py                   # Environment settings (tokens, group ID, dashboard port, OpenAI key)
│
├── messenger/                  # Messenger abstraction layer
│   ├── port.py                 # MessengerPort (abstract interface)
│   └── telegram/               # Telegram adapter (MVP)
│       └── adapter.py          # TelegramAdapter (forum topics, permission buttons, deep-links)
│
├── core/                       # Messenger-independent business logic
│   ├── orchestrator.py         # Message routing, command handling, Claude response processing
│   ├── session_manager.py      # Session lifecycle (create, stop, complete, restore, persist)
│   ├── claude_process.py       # Claude Code subprocess wrapper (stream-json protocol)
│   └── git_worktree.py         # Git worktree/branch operations, AI commit messages
│
├── voice/                      # Voice abstraction layer
│   ├── port.py                 # STTPort (abstract interface)
│   └── whisper_api.py          # OpenAI Whisper API implementation
│
├── dashboard/                  # Web dashboard
│   ├── server.py               # aiohttp web server + API routes
│   ├── message_store.py        # Per-session in-memory message history
│   └── index.html              # Single-page dashboard (HTML+CSS+JS)
│
├── storage/
│   └── project_store.py        # Project registration CRUD (JSON file)
│
└── data/                       # Runtime data (gitignored)
    ├── projects.json
    └── sessions.json
```

## Core Component Details

### 0. MessengerPort (`messenger/port.py`)

Abstract interface that all messenger adapters must implement. Core logic depends only on this interface.

```python
class MessengerPort(Protocol):
    """Messenger abstract interface"""

    async def send_message(
        self, channel_id: str, text: str, silent: bool = False
    ) -> str:
        """
        Send a message.
        silent=True: no notification (for log-like messages)
        silent=False: with notification (for permission requests and important messages)
        Returns: message ID
        """
        ...

    async def edit_message(
        self, channel_id: str, message_id: str, text: str
    ) -> None:
        """Edit an existing message."""
        ...

    async def send_permission_request(
        self, channel_id: str, tool_name: str, tool_args: str,
        request_id: str
    ) -> str:
        """Display permission approval request with Allow/Deny buttons."""
        ...

    async def create_session_channel(self, name: str) -> str:
        """
        Create a session-dedicated channel.
        Telegram: forum topic, Slack: thread, native app: chat room
        Returns: channel_id
        """
        ...

    async def get_channel_link(self, channel_id: str) -> str:
        """Return a deep-link URL for the channel."""
        ...

    async def close_session_channel(self, channel_id: str) -> None:
        """Delete/close a session channel."""
        ...

    async def download_voice(self, file_id: str) -> str:
        """Download voice message. Returns: local file path"""
        ...

    async def start(self) -> None:
        """Start messenger connection"""
        ...

    async def stop(self) -> None:
        """Stop messenger connection"""
        ...
```

### 0.1 TelegramAdapter (`messenger/telegram/adapter.py`)

Telegram forum topic-based implementation.

```python
class TelegramAdapter(MessengerPort):
    """
    Mapping:
      channel_id → forum topic message_thread_id (prefixed with "tg_")
      send_message(silent=True) → disable_notification=True
      send_permission_request → InlineKeyboardMarkup + callback_query
      create_session_channel → create_forum_topic()
      get_channel_link → tg://privatepost deep-link
      close_session_channel → delete_forum_topic()
      download_voice → bot.get_file() + download to temp
    """
```

Notification strategy:
- `silent=True` → `disable_notification=True` (log-like: streaming responses, status changes)
- `silent=False` → normal notification (permission requests, errors, task completion)

Message handling:
- Messages over 4096 chars are split at newline boundaries
- Callback handlers for text, voice, commands, and permission button presses
- Deep-links use `tg://privatepost` scheme for reliable iOS app opening

### 1. STTPort (`voice/port.py`)

Speech recognition abstraction. STT engine swappable independently from messenger.

```python
class STTPort(Protocol):
    """Voice-to-text abstract interface"""

    async def transcribe(self, audio_path: str) -> str:
        """Audio file → text. Format conversion handled internally."""
        ...

class WhisperAPISTT(STTPort):
    """OpenAI Whisper API implementation. Supports ogg/opus directly (no ffmpeg needed)."""

    def __init__(self, api_key: str):
        """Initialize OpenAI client"""

    async def transcribe(self, audio_path: str) -> str:
        """Upload audio to Whisper API → text"""
```

### 2. ClaudeProcess (`core/claude_process.py`)

Wrapper class managing Claude Code subprocess.

```python
class ClaudeProcess:
    """Manages a single Claude Code session"""

    async def start(self, project_path: str, session_id: str = None):
        """
        Run claude \
            --input-format stream-json \
            --output-format stream-json \
            --verbose
        as asyncio subprocess.
        If session_id provided, add --resume --session-id options.
        working directory = project_path
        """

    async def send_message(self, text: str):
        """
        Send user message to stdin in stream-json format:
        {"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
        """

    async def send_permission_response(self, request_id: str, allowed: bool):
        """Send permission response to stdin"""

    async def read_responses(self) -> AsyncIterator[dict]:
        """
        Read and parse stream-json lines from stdout.
        Yields each JSON object.
        Message types: init, system, assistant, result, etc.
        """

    async def stop(self):
        """Terminate gracefully with 5-second timeout, force-kill if needed"""

    @property
    def is_alive(self) -> bool:
        """Whether the process is alive"""

    @property
    def session_id(self) -> str:
        """Session ID extracted from init message (for resume)"""
```

### 3. SessionManager (`core/session_manager.py`)

Manages the entire session pool with persistence.

```python
class Session:
    name: str               # "{project}-{YYMMDD-HHMMSS}"
    project_name: str       # "MyApp"
    project_path: str       # "/Users/me/projects/myapp"
    worktree_path: str      # "/Users/me/projects/myapp/.afk-worktrees/{name}"
    channel_id: str         # Messenger channel ID (prefixed with "tg_")
    process: ClaudeProcess  # Subprocess instance
    claude_session_id: str  # Claude Code session ID (for resume)
    state: str              # "idle" | "running" | "waiting_permission" | "stopped"
    verbose: bool           # Show full tool input/output
    created_at: float       # Unix timestamp

class SessionManager:
    def __init__(self, messenger: MessengerPort):
        """Injected with MessengerPort for messenger-independent operation"""

    async def create_session(self, project_name, project_path) -> Session:
        """Create worktree + forum topic + Claude process"""

    async def stop_session(self, channel_id: str):
        """Stop Claude process, remove worktree, delete branch, close channel"""

    async def complete_session(self, channel_id: str):
        """Commit changes (AI message) → rebase onto main → merge → cleanup"""

    def get_session(self, channel_id: str) -> Session | None:
        """Look up session by channel ID"""

    def list_sessions(self) -> list[Session]:
        """List all active sessions"""

    async def cleanup_orphan_worktrees(self, project_store):
        """Detect and remove orphaned worktrees from crashed sessions"""
```

### 4. GitWorktree (`core/git_worktree.py`)

Git operations for session isolation. All functions are async.

```python
async def create_worktree(project_path, worktree_path, branch_name):
    """Create a new worktree on an isolated branch"""

async def remove_worktree(project_path, worktree_path, branch_name):
    """Remove worktree and delete branch"""

async def commit_worktree_changes(worktree_path, session_name):
    """Stage all changes + commit with AI-generated message (Claude CLI -p)"""

async def merge_branch_to_main(project_path, branch_name, worktree_path):
    """Rebase session branch onto main, then fast-forward merge"""

async def list_afk_worktrees(project_path):
    """Find all afk/* worktrees for orphan detection"""
```

### 5. Orchestrator (`core/orchestrator.py`)

Central message router and command dispatcher.

```python
class Orchestrator:
    """Routes messages and commands to sessions"""

    # Command handlers
    async def _handle_project_command(channel_id, args)   # /project add|list|remove
    async def _handle_new_command(channel_id, args)        # /new <project> [-v|--verbose]
    async def _handle_sessions_command(channel_id, args)   # /sessions
    async def _handle_stop_command(channel_id, args)       # /stop
    async def _handle_complete_command(channel_id, args)    # /complete
    async def _handle_status_command(channel_id, args)      # /status
    async def _handle_unknown_command(channel_id, text)     # help text

    # Message handlers
    async def _handle_text(channel_id, text)                # forward to session
    async def _handle_voice(channel_id, file_id)            # transcribe → forward
    async def _handle_permission_response(channel_id, request_id, choice)

    # Claude response processing
    async def _handle_claude_message(session, msg)          # route by type
    async def _handle_assistant_message(session, msg)       # parse/display
```

## Data Flow

### Text Prompt

```
User text message
  → TelegramAdapter → orchestrator._handle_text(channel_id, text)
  → SessionManager.get_session(channel_id)
  → Session.process.send_message(text)
  → read_responses() loop:
      ├── assistant → messenger.send_message(channel_id, text, silent=True)
      ├── tool_use → messenger.send_permission_request(channel_id, tool, args, request_id)
      └── result → messenger.send_message(result)
```

### Voice Prompt

```
User voice message
  → TelegramAdapter → orchestrator._handle_voice(channel_id, file_id)
  → messenger.download_voice(file_id) → audio file
  → STTPort.transcribe(audio) → text
  → messenger.send_message(channel_id, "🎤 {text}", silent=True)
  → Session.process.send_message(text)
  → (same as text from here)
```

### Permission Handling

```
Permission request detected in read_responses()
  → Session.state = "waiting_permission"
  → messenger.send_permission_request(channel_id, tool, args, request_id)
  → Wait for user button press (Allow/Deny)
  → Forward result to Claude Code stdin
  → Session.state = "running"
```

## Data Storage

### projects.json

```json
{
  "MyApp": {
    "path": "/Users/me/projects/myapp",
    "created_at": "2025-02-18T10:00:00"
  }
}
```

### sessions.json

For session recovery. Referenced on AFK daemon restart.

```json
{
  "tg_12345": {
    "name": "myapp-260218-143022",
    "project_name": "MyApp",
    "project_path": "/Users/me/projects/myapp",
    "worktree_path": "/Users/me/projects/myapp/.afk-worktrees/myapp-260218-143022",
    "channel_id": "tg_12345",
    "claude_session_id": "550e8400-e29b-41d4-a716-446655440000",
    "state": "stopped"
  }
}
```

## Mac mini Daemon

### launchd Configuration

```xml
<!-- ~/Library/LaunchAgents/com.afk.daemon.plist -->
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.afk.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/usr/local/bin/python3</string>
        <string>/path/to/afk/main.py</string>
    </array>
    <key>RunAtLoad</key>
    <true/>
    <key>KeepAlive</key>
    <true/>
    <key>StandardOutPath</key>
    <string>/tmp/afk.out.log</string>
    <key>StandardErrorPath</key>
    <string>/tmp/afk.err.log</string>
</dict>
</plist>
```

- Auto-start on boot
- Auto-restart on crash (`KeepAlive`)
- Restore sessions from sessions.json on restart (`--resume`)

## Dependencies

```
python-telegram-bot[ext]>=21.0    # Telegram bot (asyncio native)
aiohttp>=3.9                      # Web dashboard server
python-dotenv>=1.0                # Environment variable loading
openai>=1.0                       # Whisper API for voice transcription (optional)
```

System requirements:
- Claude Code CLI installed
- Python 3.11+

## Claude Code Headless Mode Reference

### Starting a Session

```bash
claude \
  --input-format stream-json \
  --output-format stream-json \
  --verbose
```

### stdin Input Format (JSONL)

```json
{"type":"user","message":{"role":"user","content":[{"type":"text","text":"Create an API with Express"}]}}
```

### stdout Output Format (JSONL)

Each line is an independent JSON object:
- **init**: Session initialization, includes session_id
- **system**: System messages
- **assistant**: Claude's text response or tool use request
- **result**: Conversation complete, includes cost/stats

### Session Resume

```bash
claude --resume --session-id <session_id> \
  --input-format stream-json \
  --output-format stream-json
```

## Future Extension Points

### Adding Messenger Adapters

```
messenger/
├── port.py                 # Abstract interface (unchanged)
├── telegram/adapter.py     # MVP
├── slack/adapter.py        # Slack thread-based
├── discord/adapter.py      # Discord thread-based
└── native/adapter.py       # WebSocket native app (full notification control)
```

Native app adapter advantages:
- Full notification control (log: silent, permission request: push, error: urgent)
- Custom UI (diff viewer, file browser, terminal view)
- Offline queuing

### Adding STT Adapters

```
voice/
├── port.py                 # Abstract interface (unchanged)
├── whisper_api.py          # OpenAI Whisper API (current)
├── whisper_local.py        # Whisper local model
└── deepgram.py             # Deepgram API
```

### Other

- **Tunneling**: cloudflared integration for remote access to local apps
- **Screenshots**: Playwright headless for app preview capture
- **Terminal client**: `afk attach` — direct session connection from MacBook
- **Log streaming**: Child process stdout/stderr forwarding
- **Git monitoring**: watchdog for .git change detection
- **Test automation**: Auto-run pytest/jest on code changes
- **Multi-agent**: Inter-session message broker
