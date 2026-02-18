# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AFK ("Away From Keyboard") is a Python daemon that serves as a remote control plane for Claude Code. Built for solo entrepreneurs and vibe coders who want to issue commands to Claude Code via Telegram (voice or text) from any device, while a Mac mini runs sessions 24/7.

## Tech Stack

- **Python 3.11+** with **asyncio** (event-driven)
- **python-telegram-bot[ext]>=21.0** — Telegram Bot API (asyncio native)
- **openai-whisper** — local speech-to-text on Mac M-chip
- **ffmpeg-python** — audio format conversion
- **Claude Code CLI** — headless mode via `--input-format stream-json --output-format stream-json`
- **launchd** — Mac daemon management

## Architecture

Hexagonal (port-adapter) architecture with two key abstraction boundaries:

- **MessengerPort** (`messenger/port.py`) — abstract interface for messenger integrations. Core logic depends only on this protocol. MVP adapter: `messenger/telegram/adapter.py` (forum topics for session isolation).
- **STTPort** (`voice/port.py`) — abstract interface for speech-to-text. MVP implementation: `voice/whisper_local.py`.

### Core Components (`core/`)

- **Orchestrator** (`orchestrator.py`) — routes incoming messages (text/voice) to the correct session
- **SessionManager** (`session_manager.py`) — manages session lifecycle (create/resume/stop), each session = one Claude Code subprocess + one Telegram forum topic
- **ClaudeProcess** (`claude_process.py`) — wraps Claude Code subprocess using stream-json protocol (JSONL on stdin/stdout)
- **PermissionBridge** (`permission_bridge.py`) — translates Claude Code tool permission requests into messenger UI buttons; integrates with TrustManager for auto-approval
- **TrustManager** (`trust_manager.py`) — three trust levels: 1=Strict (ask everything), 2=Normal (auto-approve read/write), 3=YOLO (auto-approve all except dangerous patterns like `rm -rf`, `git push`, `curl | sh`, `sudo`)
- **CostTracker** (`cost_tracker.py`) — per-session and daily cost tracking, budget limits, thrashing detection (repeated file edits, error loops, cost spikes)

### Data Flow

```
User message → TelegramAdapter → Orchestrator → SessionManager → ClaudeProcess (stdin)
ClaudeProcess (stdout) → parse stream-json → route by type:
  assistant → send to messenger (silent)
  tool_use  → PermissionBridge → auto-approve or ask user → respond to Claude
  result    → CostTracker.record() → send cost summary
```

### Storage (`storage/`)

- `data/projects.json` — registered project names → local paths
- `data/sessions.json` — session state for daemon restart recovery (channel_id, claude_session_id, trust_level, cost)

## Module Structure

```
afk/
├── main.py              # Entry point, adapter selection, daemon startup
├── config.py            # Environment settings (tokens, group ID, paths)
├── messenger/
│   ├── port.py          # MessengerPort protocol
│   └── telegram/        # Telegram adapter (forum topics)
├── core/                # Messenger-independent business logic
├── dashboard/
│   ├── message_store.py # Per-session in-memory message history
│   ├── server.py        # aiohttp web server + API routes
│   └── index.html       # Single-page dashboard (HTML+CSS+JS)
├── voice/
│   ├── port.py          # STTPort protocol
│   └── whisper_local.py
├── storage/
│   └── project_store.py
└── data/                # Runtime data (gitignored)
```

## Development Notes

- Session naming convention: `{project_name}-session-{number}`
- Session states: `idle`, `running`, `waiting_permission`, `stopped`
- Telegram channel IDs are prefixed with `tg_`
- Permission timeout: 300 seconds, auto-deny on expiry
- Telegram messages over 4096 chars must be split
- Silent notifications for log-like messages, normal notifications for permission requests/errors/completion
- Detailed architecture spec: `ARCH.md`; product spec with phased roadmap: `PROJECT.md`
