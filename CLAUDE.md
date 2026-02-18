# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

AFK ("Away From Keyboard") is a Python daemon that serves as a remote control plane for Claude Code. Built for solo entrepreneurs and vibe coders who want to issue commands to Claude Code via Telegram (voice or text) from any device, while a Mac mini runs sessions 24/7.

## Tech Stack

- **Python 3.11+** with **asyncio** (event-driven)
- **python-telegram-bot[ext]>=21.0** — Telegram Bot API (asyncio native)
- **aiohttp>=3.9** — web dashboard server
- **python-dotenv>=1.0** — environment variable loading
- **Claude Code CLI** — headless mode via `--input-format stream-json --output-format stream-json`
- **uv** — Python package manager (uv.lock)

## Architecture

Hexagonal (port-adapter) architecture with a key abstraction boundary:

- **MessengerPort** (`messenger/port.py`) — abstract interface for messenger integrations. Core logic depends only on this protocol. MVP adapter: `messenger/telegram/adapter.py` (forum topics for session isolation).

### Core Components (`core/`)

- **Orchestrator** (`orchestrator.py`) — routes incoming messages (text/voice) to the correct session; handles Telegram commands (`/project`, `/new`, `/sessions`, `/stop`, `/complete`, `/status`)
- **SessionManager** (`session_manager.py`) — manages session lifecycle (create/resume/stop), each session = one Claude Code subprocess + one Telegram forum topic + one git worktree
- **ClaudeProcess** (`claude_process.py`) — wraps Claude Code subprocess using stream-json protocol (JSONL on stdin/stdout)
- **GitWorktree** (`git_worktree.py`) — git worktree/branch management for session isolation; handles worktree creation, commit, merge back to main, and cleanup

### Data Flow

```
User message → TelegramAdapter → Orchestrator → SessionManager → ClaudeProcess (stdin)
ClaudeProcess (stdout) → parse stream-json → route by type:
  assistant → send to messenger (silent)
  result    → send to messenger
```

### Storage (`storage/`)

- `data/projects.json` — registered project names → local paths
- `data/sessions.json` — session state for daemon restart recovery (channel_id, claude_session_id, trust_level, cost)

## Module Structure

```
afk/
├── main.py              # Entry point, daemon startup, shutdown handling
├── config.py            # Environment settings (tokens, group ID, dashboard port)
├── messenger/
│   ├── port.py          # MessengerPort protocol
│   └── telegram/
│       └── adapter.py   # Telegram bot (forum topics for session isolation)
├── core/
│   ├── orchestrator.py  # Message routing & command handling
│   ├── session_manager.py # Session lifecycle management
│   ├── claude_process.py  # Claude Code subprocess wrapper
│   └── git_worktree.py   # Git worktree operations for session isolation
├── dashboard/
│   ├── server.py        # aiohttp web server + API routes
│   ├── message_store.py # Per-session in-memory message history
│   └── index.html       # Single-page dashboard (HTML+CSS+JS)
├── storage/
│   └── project_store.py # Project name → path registry
└── data/                # Runtime data (gitignored)
```

## Telegram Commands

- `/project add|list|remove` — register project names to local paths
- `/new <project_name> [--verbose]` — create new session (worktree + branch + forum topic)
- `/sessions` — list active sessions
- `/stop` — stop current session's Claude process
- `/complete` — commit worktree changes, merge session branch into main, cleanup
- `/status` — check current session state

## Environment Variables

- `AFK_TELEGRAM_BOT_TOKEN` (required) — Telegram bot token
- `AFK_TELEGRAM_GROUP_ID` (required) — Telegram group/supergroup ID
- `AFK_DASHBOARD_PORT` (optional, default: 7777) — web dashboard port

## Development Notes

- Session naming convention: `AFK-session-{number}` (branch) / `.afk-worktrees/AFK-session-{number}` (worktree directory)
- Each session runs in an isolated git worktree with its own branch
- `/complete` merges the session branch back to main via rebase + fast-forward
- Orphan worktrees from crashed sessions are cleaned up on daemon startup
- Telegram channel IDs are prefixed with `tg_`
- Telegram messages over 4096 chars must be split
- Silent notifications for log-like messages, normal notifications for errors/completion
- Detailed architecture spec: `ARCH.md`; product spec with phased roadmap: `PROJECT.md`
