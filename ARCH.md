# AFK — Architecture

## Design Principles

- **Agent-agnostic**: Core logic depends only on the `AgentPort` interface. Swappable between Claude Code/Codex/any agent runtime.
- **Control-plane-agnostic**: Core logic depends only on the `ControlPlanePort` interface. Swappable between Telegram/Slack/CLI/native app.
- **STT-agnostic**: Speech recognition abstracted via `STTPort`. Swappable between Whisper API/local/Deepgram.
- **Event-driven**: All agent output flows as typed events through an `EventBus`. Control planes subscribe and render.
- **Single entry point**: All control planes call the `Commands` API — never session manager or agent directly.
- **Always-on daemon**: Runs 24/7 as a launchd daemon on Mac mini. Accessible from any device.

## System Architecture

```
📱 Phone         💻 MacBook        🖥️ Mac mini (AFK Server)
│                │                 │
│ Telegram       │ Telegram        │ ┌──────────────────────────────────┐
│                │ + CLI (future)  │ │          AFK Daemon              │
│                │                 │ │                                  │
└────────────────┴────────────────┤ │  ┌──────────────────────────┐    │
                                  │ │  │  ControlPlanePort        │    │
       Telegram Bot API           │ │  │  ┌────────────────────┐  │    │
       ───────────────────────────┤ │  │  │ TelegramAdapter    │  │    │
                                  │ │  │  └────────────────────┘  │    │
                                  │ │  └──────────┬───────────────┘    │
                                  │ │             │                    │
                                  │ │  ┌──────────▼───────────────┐    │
                                  │ │  │   Orchestrator            │    │
                                  │ │  │   (messenger → Commands)  │    │
                                  │ │  └──────────┬───────────────┘    │
                                  │ │             │                    │
                                  │ │  ┌──────────▼───────────────┐    │
                                  │ │  │   Commands API            │    │
                                  │ │  │   (single entry point)    │    │
                                  │ │  │                           │    │
                                  │ │  │  ┌─────────┐ ┌─────────┐ │    │
                                  │ │  │  │ STTPort │ │ Tunnel  │ │    │
                                  │ │  │  │(Whisper)│ │Capabilty│ │    │
                                  │ │  │  └─────────┘ └─────────┘ │    │
                                  │ │  └──────────┬───────────────┘    │
                                  │ │             │                    │
                                  │ │  ┌──────────▼───────────────┐    │
                                  │ │  │  Session Manager          │    │
                                  │ │  │  ┌────────┐ ┌────────┐   │    │
                                  │ │  │  │ Ses A  │ │ Ses B  │   │    │
                                  │ │  │  └───┬────┘ └───┬────┘   │    │
                                  │ │  └──────┼──────────┼────────┘    │
                                  │ │         │          │             │
                                  │ │  ┌──────▼──┐ ┌────▼─────┐       │
                                  │ │  │AgentPort│ │AgentPort │       │
                                  │ │  │(Claude  │ │(Claude   │       │
                                  │ │  │ Code)   │ │ Code)    │       │
                                  │ │  └─────────┘ └──────────┘       │
                                  │ │                                  │
                                  │ │  EventBus ──► EventRenderer      │
                                  │ │              (→ Telegram msgs)   │
                                  │ └──────────────────────────────────┘
```

## 3-Layer Architecture

```
┌─────────────────────────────────────────────────────────────┐
│  Layer 1: Core (AFK Kernel)                                 │
│  commands.py, events.py, session_manager.py, git_worktree.py│
│  Never imports Telegram, Claude, cloudflared                 │
├─────────────────────────────────────────────────────────────┤
│  Layer 2: Ports (Abstract Interfaces)                       │
│  AgentPort, ControlPlanePort, STTPort                       │
│  Protocol definitions only — no implementations              │
├─────────────────────────────────────────────────────────────┤
│  Layer 3: Adapters + Capabilities                           │
│  ClaudeCodeAgent, TelegramAdapter, WhisperAPISTT            │
│  TunnelCapability, EventRenderer                            │
└─────────────────────────────────────────────────────────────┘
```

**Boundary rules:**
1. `core/` never imports from `adapters/`, `messenger/`, `capabilities/`
2. `ports/` contains only Protocol definitions (no implementations)
3. `adapters/` contains all external integrations
4. `capabilities/` contains pluggable session-level features
5. `core.commands` is the single entry point for all control planes
6. All agent output flows as typed events through EventBus

## Module Structure

```
afk/
├── main.py                          # Entry point, component wiring, daemon startup/shutdown
│
├── ports/                           # Abstract interfaces (Protocol definitions only)
│   ├── agent.py                     # AgentPort protocol
│   ├── control_plane.py             # ControlPlanePort protocol
│   └── stt.py                       # STTPort protocol
│
├── core/                            # Business logic (agent/messenger-independent)
│   ├── commands.py                  # Commands API — single entry point for all control planes
│   ├── events.py                    # EventBus (asyncio pub/sub) + typed event dataclasses
│   ├── orchestrator.py              # Thin glue: wires messenger callbacks to Commands API
│   ├── session_manager.py           # Session lifecycle (create, stop, complete, persist)
│   └── git_worktree.py              # Git worktree/branch operations
│
├── adapters/                        # Concrete implementations of ports
│   ├── claude_code/
│   │   ├── agent.py                 # ClaudeCodeAgent (implements AgentPort)
│   │   └── commit_helper.py         # AI commit message generation via Claude CLI
│   ├── telegram/
│   │   ├── config.py                # TelegramConfig (bot_token, group_id)
│   │   └── renderer.py              # EventRenderer: EventBus events → Telegram messages
│   └── whisper/
│       └── stt.py                   # WhisperAPISTT (implements STTPort)
│
├── capabilities/                    # Pluggable session-level features
│   └── tunnel/
│       └── tunnel.py                # TunnelCapability (dev server + cloudflared tunneling)
│
├── messenger/                       # Telegram bot adapter (implements ControlPlanePort)
│   └── telegram/
│       └── adapter.py               # TelegramAdapter (forum topics, permission buttons, deep-links)
│
├── dashboard/                       # Web dashboard
│   ├── server.py                    # aiohttp web server + API routes
│   ├── message_store.py             # Per-session in-memory message history
│   └── index.html                   # Single-page dashboard (HTML+CSS+JS)
│
├── storage/
│   └── project_store.py             # Project registration CRUD (JSON file)
│
└── data/                            # Runtime data (gitignored)
    ├── projects.json
    └── sessions.json
```

## Core Component Details

### 0. Ports (`ports/`)

Abstract interfaces that define boundaries between layers. All Protocol definitions, no implementations.

#### AgentPort (`ports/agent.py`)

Abstract interface for agent runtimes. Any AI coding agent can implement this protocol.

```python
@runtime_checkable
class AgentPort(Protocol):
    @property
    def session_id(self) -> str | None: ...
    @property
    def is_alive(self) -> bool: ...
    async def start(self, working_dir: str, session_id: str | None = None) -> None: ...
    async def send_message(self, text: str) -> None: ...
    async def send_permission_response(self, request_id: str, allowed: bool) -> None: ...
    async def read_responses(self) -> AsyncIterator[dict]: ...
    async def stop(self) -> None: ...
```

#### ControlPlanePort (`ports/control_plane.py`)

Abstract interface for control plane integrations (Telegram, CLI, Web, etc.).

```python
class ControlPlanePort(Protocol):
    async def send_message(self, channel_id: str, text: str, *, silent: bool = False) -> str: ...
    async def edit_message(self, channel_id: str, message_id: str, text: str) -> None: ...
    async def send_permission_request(self, channel_id: str, tool_name: str, tool_args: str, request_id: str) -> None: ...
    async def create_session_channel(self, name: str) -> str: ...
    def get_channel_link(self, channel_id: str) -> str | None: ...
    async def close_session_channel(self, channel_id: str) -> None: ...
    async def download_voice(self, file_id: str) -> str: ...
    async def start(self) -> None: ...
    async def stop(self) -> None: ...
```

#### STTPort (`ports/stt.py`)

Speech-to-text abstract interface. STT engine swappable independently from control plane.

```python
class STTPort(Protocol):
    async def transcribe(self, audio_path: str) -> str: ...
```

### 1. EventBus + Events (`core/events.py`)

Asyncio-based typed pub/sub. Publishers call `publish(event)`, subscribers iterate via `iter_events(EventType)`.

```python
class EventBus:
    def subscribe(self, event_type: type[T]) -> asyncio.Queue[T]: ...
    def unsubscribe(self, event_type: type[T], queue: asyncio.Queue) -> None: ...
    def publish(self, event: object) -> None: ...
    async def iter_events(self, event_type: type[T]) -> AsyncIterator[T]: ...
```

Event types:
- `AgentSystemEvent(channel_id, agent_session_id)` — agent session ready
- `AgentAssistantEvent(channel_id, content_blocks, session_name, verbose)` — agent output (text, tool use, tool result)
- `AgentResultEvent(channel_id, cost_usd, duration_ms)` — task completed
- `AgentStoppedEvent(channel_id, session_name)` — agent process stopped unexpectedly
- `SessionCreatedEvent(channel_id, session_name, project_name, ...)` — new session created

### 2. Commands API (`core/commands.py`)

Single entry point for all control planes. Returns plain dataclasses, never messenger-specific objects.

```python
class Commands:
    def __init__(self, session_manager, project_store, message_store, stt=None, tunnel=None): ...

    # Project commands
    def cmd_add_project(self, name: str, path: str) -> tuple[bool, str]: ...
    def cmd_list_projects(self) -> dict[str, dict]: ...
    def cmd_remove_project(self, name: str) -> tuple[bool, str]: ...
    def cmd_get_project(self, name: str) -> dict | None: ...

    # Session commands
    async def cmd_new_session(self, project_name: str, verbose: bool = False) -> Session: ...
    async def cmd_send_message(self, channel_id: str, text: str) -> bool: ...
    async def cmd_send_voice(self, channel_id: str, audio_path: str) -> tuple[bool, str]: ...
    def cmd_get_session(self, channel_id: str) -> Session | None: ...
    def cmd_list_sessions(self) -> list[SessionInfo]: ...
    async def cmd_stop_session(self, channel_id: str) -> bool: ...
    async def cmd_complete_session(self, channel_id: str) -> tuple[bool, str]: ...
    def cmd_get_status(self, channel_id: str) -> SessionStatus | None: ...
    async def cmd_permission_response(self, channel_id: str, request_id: str, allowed: bool) -> bool: ...

    # Tunnel commands
    async def cmd_start_tunnel(self, channel_id: str) -> str: ...
    async def cmd_stop_tunnel(self, channel_id: str) -> bool: ...
    def cmd_get_tunnel_url(self, channel_id: str) -> str | None: ...
```

### 3. Session Manager (`core/session_manager.py`)

Manages the session pool. Each session = one agent subprocess + one control plane channel + one git worktree.

```python
@dataclass
class Session:
    name: str               # "{project}-{YYMMDD-HHMMSS}"
    project_name: str       # "MyApp"
    project_path: str       # "/Users/me/projects/myapp"
    worktree_path: str      # "/Users/me/projects/myapp/.afk-worktrees/{name}"
    channel_id: str         # Control plane channel ID
    agent: AgentPort        # Agent runtime instance
    agent_session_id: str   # Agent session ID (for resume)
    state: str              # "idle" | "running" | "waiting_permission" | "stopped"
    verbose: bool           # Show full tool input/output
    created_at: float       # Unix timestamp

class SessionManager:
    def __init__(self, messenger, data_dir, event_bus=None, agent_factory=None, commit_message_fn=None): ...

    def add_cleanup_callback(self, callback: SessionCleanupFn) -> None: ...
    async def create_session(self, project_name, project_path) -> Session: ...
    async def stop_session(self, channel_id: str) -> bool: ...
    async def complete_session(self, channel_id: str) -> tuple[bool, str]: ...
    def get_session(self, channel_id: str) -> Session | None: ...
    def list_sessions(self) -> list[Session]: ...
    async def cleanup_orphan_worktrees(self, project_store): ...
```

The `_read_loop` publishes typed events to the `EventBus` instead of calling callbacks directly. Cleanup callbacks (e.g., tunnel teardown) are registered via `add_cleanup_callback()`.

### 4. Orchestrator (`core/orchestrator.py`)

Thin glue layer: registers messenger callbacks and delegates to the Commands API.

```python
class Orchestrator:
    def __init__(self, messenger: ControlPlanePort, commands: Commands): ...

    # Registers callbacks on messenger for:
    # text, voice, /project, /new, /sessions, /stop, /complete, /status, /tunnel
    # Each callback delegates to self._cmd.cmd_*() methods
```

### 5. GitWorktree (`core/git_worktree.py`)

Git operations for session isolation. All functions are async. No Claude CLI dependency — commit message generation is injected via `commit_message_fn`.

```python
CommitMessageFn = Callable[[str], Awaitable[str]]  # worktree_path → message

async def create_worktree(project_path, worktree_path, branch_name): ...
async def remove_worktree(project_path, worktree_path, branch_name): ...
async def commit_worktree_changes(worktree_path, session_name, commit_message_fn=None): ...
async def merge_branch_to_main(project_path, branch_name, worktree_path): ...
async def list_afk_worktrees(project_path): ...
```

### 6. Adapters

#### ClaudeCodeAgent (`adapters/claude_code/agent.py`)

Implements `AgentPort`. Wraps Claude Code CLI subprocess using stream-json protocol.

```python
class ClaudeCodeAgent:
    async def start(self, working_dir: str, session_id: str = None): ...
    async def send_message(self, text: str): ...
    async def send_permission_response(self, request_id: str, allowed: bool): ...
    async def read_responses(self) -> AsyncIterator[dict]: ...
    async def stop(self): ...
```

#### TelegramAdapter (`messenger/telegram/adapter.py`)

Implements `ControlPlanePort`. Uses Telegram forum topics for session isolation.

```python
class TelegramAdapter:
    def __init__(self, config: TelegramConfig): ...
    # Implements all ControlPlanePort methods
    # Registers text/voice/command callbacks via set_on_* methods
```

Notification strategy:
- `silent=True` → `disable_notification=True` (log-like: streaming responses, status changes)
- `silent=False` → normal notification (permission requests, errors, task completion)

#### EventRenderer (`adapters/telegram/renderer.py`)

Subscribes to EventBus events and renders them as Telegram messages.

```python
class EventRenderer:
    def __init__(self, event_bus, messenger, message_store): ...
    def start(self) -> None:  # starts background tasks
    def stop(self) -> None:   # cancels background tasks
```

Handles: `AgentSystemEvent`, `AgentAssistantEvent`, `AgentResultEvent`, `AgentStoppedEvent`

#### WhisperAPISTT (`adapters/whisper/stt.py`)

Implements `STTPort`. Uses OpenAI Whisper API. Supports ogg/opus directly (no ffmpeg needed).

### 7. Capabilities

#### TunnelCapability (`capabilities/tunnel/tunnel.py`)

Pluggable session-level feature. Manages dev server detection and cloudflared tunnel per session.

```python
class TunnelCapability:
    async def start_tunnel(self, channel_id: str, worktree_path: str) -> str: ...
    async def stop_tunnel(self, channel_id: str) -> bool: ...
    def get_tunnel(self, channel_id: str) -> TunnelProcess | None: ...
    async def cleanup_session(self, channel_id: str) -> None: ...
```

Registered as a cleanup callback with SessionManager — tunnels are automatically torn down when sessions stop or complete.

## Data Flow

### Text Prompt

```
User text message
  → TelegramAdapter → Orchestrator._handle_text(channel_id, text)
  → Commands.cmd_send_message(channel_id, text)
  → SessionManager.send_to_session(channel_id, text)
  → Session.agent.send_message(text)
  → agent read_responses() → SessionManager._publish_agent_event()
  → EventBus.publish(AgentAssistantEvent | AgentResultEvent)
  → EventRenderer → messenger.send_message(channel_id, ...)
```

### Voice Prompt

```
User voice message
  → TelegramAdapter → Orchestrator._handle_voice(channel_id, file_id)
  → messenger.download_voice(file_id) → audio file
  → Commands.cmd_send_voice(channel_id, audio_path)
  → STTPort.transcribe(audio_path) → text
  → SessionManager.send_to_session(channel_id, text)
  → (same event flow as text from here)
```

### Permission Handling

```
Permission request detected in agent read_responses()
  → EventBus.publish(AgentAssistantEvent with tool_use block)
  → EventRenderer → messenger.send_permission_request(...)
  → User presses Allow/Deny button
  → Orchestrator._handle_permission_response(channel_id, request_id, choice)
  → Commands.cmd_permission_response(...)
  → SessionManager.send_permission_response(...)
  → Session.agent.send_permission_response(request_id, allowed)
```

### Session Complete

```
/complete command
  → Orchestrator → Commands.cmd_complete_session(channel_id)
  → SessionManager.complete_session():
      1. Run cleanup callbacks (stops tunnel, etc.)
      2. Stop agent process
      3. commit_worktree_changes(commit_message_fn=generate_commit_message)
      4. merge_branch_to_main (rebase + ff-merge)
      5. delete_branch
      6. close_session_channel
```

## Wiring (`main.py`)

```python
# Core infrastructure
event_bus = EventBus()
messenger = TelegramAdapter(telegram_config)

# Session manager publishes events via EventBus
session_manager = SessionManager(
    messenger, data_dir,
    event_bus=event_bus,
    agent_factory=ClaudeCodeAgent,
    commit_message_fn=generate_commit_message,
)

# Capability cleanup registered with session manager
tunnel_capability = TunnelCapability()
session_manager.add_cleanup_callback(tunnel_capability.cleanup_session)

# Commands API — single entry point
commands = Commands(session_manager, project_store, message_store, stt=stt, tunnel=tunnel_capability)

# EventRenderer subscribes to EventBus, renders to messenger
renderer = EventRenderer(event_bus, messenger, message_store)
renderer.start()

# Orchestrator wires messenger callbacks to Commands
orchestrator = Orchestrator(messenger, commands)
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
  "12345": {
    "name": "myapp-260218-143022",
    "project_name": "MyApp",
    "project_path": "/Users/me/projects/myapp",
    "worktree_path": "/Users/me/projects/myapp/.afk-worktrees/myapp-260218-143022",
    "channel_id": "12345",
    "agent_session_id": "550e8400-e29b-41d4-a716-446655440000",
    "state": "stopped"
  }
}
```

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
- cloudflared (optional, for `/tunnel`)

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

### Adding Agent Runtimes

Implement `AgentPort` from `ports/agent.py`:

```
adapters/
├── claude_code/agent.py     # Claude Code CLI (current)
├── codex/agent.py           # OpenAI Codex CLI
├── aider/agent.py           # Aider
└── custom/agent.py          # Custom agent wrapper
```

### Adding Control Planes

Implement `ControlPlanePort` from `ports/control_plane.py`:

```
adapters/
├── telegram/                # Telegram forum topics (current)
├── slack/adapter.py         # Slack thread-based
├── discord/adapter.py       # Discord thread-based
├── cli/adapter.py           # Terminal client
└── web/adapter.py           # WebSocket native app
```

### Adding STT Adapters

Implement `STTPort` from `ports/stt.py`:

```
adapters/
├── whisper/stt.py           # OpenAI Whisper API (current)
├── whisper_local/stt.py     # Whisper local model
└── deepgram/stt.py          # Deepgram API
```

### Adding Capabilities

Register cleanup callbacks with SessionManager:

```
capabilities/
├── tunnel/tunnel.py         # Dev server tunneling (current)
├── screenshot/screenshot.py # App preview capture
└── test_runner/runner.py    # Auto-run tests on changes
```
