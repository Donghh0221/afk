# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AFK ("Away From Keyboard") is a Python daemon that serves as a remote control plane for AI coding agents. Built for solo entrepreneurs and vibe coders who want to issue commands via Telegram (voice or text) from any device, while a Mac mini runs sessions 24/7.

## Tech Stack

- **Python 3.11+** with **asyncio** (event-driven)
- **python-telegram-bot[ext]>=21.0** — Telegram Bot API (asyncio native)
- **aiohttp>=3.9** — web dashboard server
- **python-dotenv>=1.0** — environment variable loading
- **openai>=1.0** — Whisper API for voice transcription (optional, only if API key is set)
- **Claude Code CLI** — headless mode via `--input-format stream-json --output-format stream-json`
- **cloudflared** — quick tunnels for remote verification (optional)
- **uv** — Python package manager (uv.lock)

## Architecture

3-layer hexagonal (port-adapter) architecture with three abstraction boundaries:

- **AgentPort** (`ports/agent.py`) — abstract interface for agent runtimes. MVP adapter: `adapters/claude_code/agent.py` (Claude Code CLI).
- **ControlPlanePort** (`ports/control_plane.py`) — abstract interface for control plane integrations. MVP adapter: `messenger/telegram/adapter.py` (forum topics for session isolation).
- **STTPort** (`ports/stt.py`) — abstract interface for speech-to-text. MVP adapter: `adapters/whisper/stt.py` (OpenAI Whisper API).

### Boundary Rules

1. `core/` never imports from `adapters/`, `messenger/`, `capabilities/`, or any external tool (Telegram, Claude, cloudflared)
2. `ports/` contains only Protocol definitions — no implementations
3. `adapters/` contains all external integrations
4. `capabilities/` contains pluggable session-level features
5. `core.commands` is the single entry point for all control planes
6. All agent output flows as typed events through EventBus

### Core Components (`core/`)

- **Commands** (`commands.py`) — single entry point (facade) for all control planes. Returns plain dataclasses, never messenger-specific objects.
- **EventBus** (`events.py`) — asyncio-based typed pub/sub. Agent output → events → control plane rendering.
- **Orchestrator** (`orchestrator.py`) — thin glue layer: registers messenger callbacks, delegates to Commands API.
- **SessionManager** (`session_manager.py`) — manages session lifecycle (create/stop/complete), each session = one agent subprocess + one control plane channel + one git worktree. Publishes events to EventBus.
- **GitWorktree** (`git_worktree.py`) — git worktree/branch management for session isolation. Commit message generation is injected via `commit_message_fn` (no Claude CLI dependency in core).

### Adapters

- **ClaudeCodeAgent** (`adapters/claude_code/agent.py`) — implements AgentPort, wraps Claude Code subprocess using stream-json protocol.
- **commit_helper** (`adapters/claude_code/commit_helper.py`) — generates commit messages using Claude Code CLI `-p` mode.
- **EventRenderer** (`adapters/telegram/renderer.py`) — subscribes to EventBus events, renders them as Telegram messages.
- **WhisperAPISTT** (`adapters/whisper/stt.py`) — implements STTPort using OpenAI Whisper API.

### Capabilities

- **TunnelCapability** (`capabilities/tunnel/tunnel.py`) — dev server detection + cloudflared tunneling per session. Registered as cleanup callback with SessionManager.

### Data Flow

```
Text message  → TelegramAdapter → Orchestrator → Commands → SessionManager → Agent (stdin)
Voice message → TelegramAdapter → Orchestrator → Commands → STT (transcribe) → SessionManager → Agent (stdin)

Agent (stdout) → SessionManager._read_loop → EventBus.publish(typed events)
  → EventRenderer → messenger.send_message (to Telegram)
  → MessageStore (for dashboard)
```

### Storage (`storage/`)

- `data/projects.json` — registered project names → local paths
- `data/sessions.json` — session state for daemon restart recovery

## Module Structure

```
afk/
├── main.py                          # Entry point, wires everything together
├── ports/                           # Abstract interfaces (Protocol definitions only)
│   ├── agent.py                     # AgentPort protocol
│   ├── control_plane.py             # ControlPlanePort protocol
│   └── stt.py                       # STTPort protocol
├── core/                            # Business logic (never imports adapters)
│   ├── commands.py                  # Commands API — single entry point
│   ├── events.py                    # EventBus + typed event dataclasses
│   ├── orchestrator.py              # Thin glue: messenger callbacks → Commands
│   ├── session_manager.py           # Session lifecycle, publishes events
│   ├── git_worktree.py              # Git worktree/branch operations
│   └── config.py                    # CoreConfig
├── adapters/                        # Concrete implementations of ports
│   ├── claude_code/
│   │   ├── agent.py                 # ClaudeCodeAgent (implements AgentPort)
│   │   └── commit_helper.py         # AI commit message generation
│   ├── telegram/
│   │   ├── config.py                # TelegramConfig
│   │   └── renderer.py              # EventRenderer (EventBus → Telegram)
│   └── whisper/
│       ├── config.py                # WhisperConfig
│       └── stt.py                   # WhisperAPISTT (implements STTPort)
├── capabilities/
│   └── tunnel/
│       └── tunnel.py                # TunnelCapability (dev server + cloudflared)
├── messenger/                       # Telegram bot (ControlPlanePort implementation)
│   ├── port.py                      # MessengerPort protocol (legacy alias)
│   └── telegram/
│       └── adapter.py               # TelegramAdapter (forum topics, inline buttons)
├── dashboard/
│   ├── server.py                    # aiohttp web server + API routes
│   ├── message_store.py             # Per-session in-memory message history
│   └── index.html                   # Single-page dashboard (HTML+CSS+JS)
├── storage/
│   └── project_store.py             # Project name → path registry
└── data/                            # Runtime data (gitignored)
```

## Telegram Commands

- `/project add|list|remove` — register project names to local paths
- `/new <project_name> [-v|--verbose]` — create new session (worktree + branch + forum topic); `-v`/`--verbose` shows full tool input/output
- `/sessions` — list active sessions with state indicators
- `/stop` — stop current session's agent process and clean up worktree
- `/complete` — auto-commit worktree changes, merge into main, cleanup
- `/status` — check current session state (name, agent alive, project, worktree, tunnel URL)
- `/tunnel` — start dev server + cloudflared tunnel; `/tunnel stop` to stop
- Unknown commands display help with the list of available commands

## Environment Variables

- `AFK_TELEGRAM_BOT_TOKEN` (required) — Telegram bot token
- `AFK_TELEGRAM_GROUP_ID` (required) — Telegram group/supergroup ID
- `AFK_DASHBOARD_PORT` (optional, default: 7777) — web dashboard port
- `AFK_OPENAI_API_KEY` or `OPENAI_API_KEY` (optional) — enables voice message transcription via Whisper API

## Development Notes

- Session naming convention: `{project_name}-{YYMMDD-HHMMSS}` (e.g. `myapp-260218-143022`)
- Branch naming: `afk/{session_name}` (e.g. `afk/myapp-260218-143022`)
- Worktree directory: `.afk-worktrees/{session_name}`
- Each session runs in an isolated git worktree with its own branch
- `/complete` auto-commits uncommitted changes (commit message generated by Claude Code CLI `-p` mode via injected `commit_message_fn`), then rebases onto main + fast-forward merge
- Orphan worktrees from crashed sessions are cleaned up on daemon startup
- Telegram messages over 4096 chars are split at newline boundaries
- Silent notifications for assistant (log-like) messages, normal notifications for results/errors
- Deep-links use `https://t.me/c/{group_id}/{channel_id}` scheme
- Voice support is conditionally enabled only when OpenAI API key is configured
- Capabilities register cleanup callbacks with SessionManager — cleanup runs on `/stop` or `/complete`
- Detailed architecture spec: `ARCH.md`; product spec with phased roadmap: `PROJECT.md`
