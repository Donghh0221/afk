# AFK — Architecture

## Design Principles

- **Messenger-agnostic**: Core logic depends only on the MessengerPort interface. Swappable between Telegram/Slack/native app.
- **STT-agnostic**: Speech recognition abstracted via STTPort. Swappable between Whisper local/API.
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
├── main.py                     # Entry point, adapter selection, daemon startup
├── config.py                   # Environment settings (tokens, group ID, paths, etc.)
│
├── messenger/                  # Messenger abstraction layer
│   ├── __init__.py
│   ├── port.py                 # MessengerPort (abstract interface)
│   └── telegram/               # Telegram adapter (MVP)
│       ├── __init__.py
│       ├── adapter.py          # TelegramAdapter (MessengerPort implementation)
│       ├── handlers.py         # Telegram-specific handlers (commands, callbacks)
│       └── formatter.py        # Telegram message formatting (markdown, splitting)
│
├── core/                       # Messenger-independent business logic
│   ├── __init__.py
│   ├── orchestrator.py         # Handler logic (project/session/message routing)
│   ├── session_manager.py      # Session lifecycle (create, query, stop, restore)
│   ├── claude_process.py       # Claude Code subprocess wrapper
│   ├── permission_bridge.py    # Permission request ↔ MessengerPort bridge
│   ├── trust_manager.py        # Trust level management, risk assessment
│   └── cost_tracker.py         # Cost tracking, budget management, thrashing detection
│
├── voice/                      # Voice abstraction layer
│   ├── __init__.py
│   ├── port.py                 # STTPort (abstract interface)
│   └── whisper_local.py        # Whisper local implementation (MVP)
│
├── storage/
│   ├── __init__.py
│   └── project_store.py        # Project registration CRUD (JSON file)
│
├── data/                       # Runtime data (gitignored)
│   ├── projects.json
│   └── sessions.json
│
└── requirements.txt
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

    async def send_permission_request(
        self, channel_id: str, tool_name: str, tool_args: str,
        options: list[str]
    ) -> str:
        """
        Display permission approval request with choices.
        Returns: user's selected option
        """
        ...

    async def create_session_channel(self, name: str) -> str:
        """
        Create a session-dedicated channel.
        Telegram: forum topic, Slack: thread, native app: chat room
        Returns: channel_id
        """
        ...

    async def send_file(self, channel_id: str, file_path: str) -> None:
        """Send a file"""
        ...

    async def download_voice(self, voice_file_id: str) -> str:
        """Download voice message. Returns: local file path"""
        ...

    def on_text_message(self, callback: Callable) -> None:
        """Register text message callback"""
        ...

    def on_voice_message(self, callback: Callable) -> None:
        """Register voice message callback"""
        ...

    def on_command(self, command: str, callback: Callable) -> None:
        """Register command handler"""
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
      channel_id → forum topic message_thread_id
      send_message(silent=True) → disable_notification=True
      send_permission_request → InlineKeyboardMarkup + callback_query
      create_session_channel → create_forum_topic()
    """
```

Notification strategy:
- `silent=True` → `disable_notification=True` (log-like: streaming responses, status changes)
- `silent=False` → normal notification (permission requests, errors, task completion)

### 1. STTPort (`voice/port.py`)

Speech recognition abstraction. STT engine swappable independently from messenger.

```python
class STTPort(Protocol):
    """Voice-to-text abstract interface"""

    async def transcribe(self, audio_path: str) -> str:
        """Audio file → text. Format conversion handled internally."""
        ...

class WhisperLocalSTT(STTPort):
    """Whisper local model (MVP). Base model recommended for Mac M-chip."""

    def __init__(self, model_name: str = "base"):
        """Load model"""

    async def transcribe(self, audio_path: str) -> str:
        """ffmpeg (ogg→wav) → Whisper → text"""
```

### 2. ClaudeProcess (`core/claude_process.py`)

Wrapper class managing Claude Code subprocess.

```python
class ClaudeProcess:
    """Manages a single Claude Code session"""

    async def start(self, project_path: str, session_id: str = None):
        """
        Run claude -p \
            --input-format stream-json \
            --output-format stream-json \
            --verbose
        as asyncio subprocess.
        If session_id provided, add --resume option.
        working directory = project_path
        """

    async def send_message(self, text: str):
        """
        Send user message to stdin in stream-json format:
        {"type":"user","message":{"role":"user","content":[{"type":"text","text":"..."}]}}
        """

    async def read_responses(self) -> AsyncIterator[dict]:
        """
        Read and parse stream-json lines from stdout.
        Yields each JSON object.
        Message types: init, assistant, result, etc.
        """

    async def stop(self):
        """Stop the process"""

    @property
    def is_alive(self) -> bool:
        """Whether the process is alive"""

    @property
    def session_id(self) -> str:
        """Session ID extracted from init message (for resume)"""
```

### 3. SessionManager (`core/session_manager.py`)

Manages the entire session pool.

```python
class Session:
    name: str               # "MyApp-session-1"
    project_name: str       # "MyApp"
    project_path: str       # "~/projects/myapp"
    channel_id: str         # Messenger channel ID (Telegram: topic_id, etc.)
    process: ClaudeProcess  # Subprocess instance
    claude_session_id: str  # Claude Code session ID (for resume)
    state: str              # "idle" | "running" | "waiting_permission" | "stopped"
    trust_level: int        # 1=strict, 2=normal, 3=yolo

class SessionManager:
    def __init__(self, messenger: MessengerPort):
        """Injected with MessengerPort for messenger-independent operation"""

    async def create_session(self, project_name, project_path) -> Session:
        """messenger.create_session_channel() → create session + start Claude Code"""

    async def stop_session(self, channel_id: str):
        """Stop session"""

    async def resume_session(self, channel_id: str):
        """Restore session using saved session_id"""

    def get_session_by_channel(self, channel_id: str) -> Session | None:
        """Look up session by channel ID"""

    def list_sessions(self) -> list[Session]:
        """List all active sessions"""
```

### 4. PermissionBridge (`core/permission_bridge.py`)

Forwards Claude Code permission requests to users via MessengerPort.
Integrates with TrustManager to determine auto-approval.

```python
class PermissionBridge:
    def __init__(self, messenger: MessengerPort, trust_manager: TrustManager):
        """Injected with MessengerPort + TrustManager"""

    async def handle_permission_request(self, session: Session, tool_request: dict) -> str:
        """
        1. Check auto-approval with TrustManager
        2. If auto-approved, return immediately
        3. Otherwise, call messenger.send_permission_request()
        4. Wait for user response (timeout=300s)
        5. Auto-deny on timeout
        Returns: "allow" | "deny" | "always_allow"
        """
```

### 5. TrustManager (`core/trust_manager.py`)

Auto-approval decisions based on trust level.

```python
class TrustManager:
    # Dangerous patterns blocked at all levels
    ALWAYS_ASK = ["rm -rf", "git push", "curl | sh", "sudo"]

    def should_auto_approve(self, trust_level: int, tool_name: str, tool_args: str) -> bool:
        """
        Level 1 (Strict): deny all
        Level 2 (Normal): auto-approve Read, Write
        Level 3 (YOLO):   approve all except ALWAYS_ASK
        """
```

### 6. CostTracker (`core/cost_tracker.py`)

Cost tracking + budget limits + thrashing detection.

```python
class CostTracker:
    async def record(self, session: Session, cost_usd: float):
        """Record cost"""

    def get_session_cost(self, channel_id: str) -> float:
        """Session cumulative cost"""

    def get_daily_cost(self) -> float:
        """Today's total cost"""

    def is_over_budget(self) -> bool:
        """Whether budget is exceeded"""

    def detect_thrashing(self, session: Session) -> bool:
        """
        Thrashing detection:
        - Same file modified 3+ times
        - Cost spike in short period
        - Same error repeated
        """
```

## Data Flow

### Text Prompt

```
User text message
  → TelegramAdapter → orchestrator.on_text_message(channel_id, text)
  → SessionManager.get_session_by_channel(channel_id)
  → Session.process.send_message(text)
  → read_responses() loop:
      ├── assistant → messenger.send_message(channel_id, text, silent=True)
      ├── tool_use → PermissionBridge (TrustManager check → auto-approve or button)
      └── result → CostTracker.record() → messenger.send_message(cost summary)
```

### Voice Prompt

```
User voice message
  → TelegramAdapter → orchestrator.on_voice_message(channel_id, file_id)
  → messenger.download_voice(file_id) → audio file
  → STTPort.transcribe(audio) → text
  → messenger.send_message(channel_id, "🎤 {text}", silent=True)
  → Session.process.send_message(text)
  → (same as text from here)
```

### Permission Handling

```
Permission request detected in read_responses()
  → TrustManager.should_auto_approve() check
  ├── Auto-approve → forward directly to Claude Code
  └── Manual approval needed:
      → Session.state = "waiting_permission"
      → messenger.send_permission_request(channel_id, tool, args, options)
      → Wait for user response (timeout=300s)
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
    "name": "MyApp-session-1",
    "project_name": "MyApp",
    "project_path": "/Users/me/projects/myapp",
    "channel_id": "tg_12345",
    "claude_session_id": "550e8400-e29b-41d4-a716-446655440000",
    "state": "stopped",
    "trust_level": 2,
    "total_cost_usd": 1.23
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
openai-whisper                     # Voice → text (local)
ffmpeg-python                      # Audio format conversion
```

System requirements:
- Claude Code CLI installed
- ffmpeg installed (`brew install ffmpeg`)
- Python 3.11+

## Claude Code Headless Mode Reference

### Starting a Session

```bash
claude -p \
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
- **assistant**: Claude's text response or tool use request
- **result**: Conversation complete, includes cost/stats

### Session Resume

```bash
claude -p --resume <session_id> \
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
├── whisper_local.py        # MVP (local)
├── whisper_api.py          # OpenAI Whisper API
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
