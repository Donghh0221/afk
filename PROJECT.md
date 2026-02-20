# AFK — Project

Vision, current status, and roadmap. For usage see [README.md](README.md), for architecture see [ARCH.md](ARCH.md).

## Vision

A remote control plane for AI work sessions — not just coding, but any file-based deliverable production (code, writing, research, data analysis). The user issues commands from their phone or any control plane, the agent runs on a server back home.

## Target Users

Solo entrepreneurs, freelancers, one-person agencies — anyone who uses AI to produce real deliverables. They issue instructions from their phone, check results, and move on. The terminal is a bottleneck; AFK removes it.

## Current Status

### Done

- Telegram control plane (forum topics as session channels)
- Web control plane (localhost:7777) with REST API + SSE
- Multi-session support with git worktree isolation
- Claude Code agent adapter (stream-json protocol)
- OpenAI Codex agent adapter
- OpenAI Deep Research agent adapter
- Voice input via Whisper API
- EventBus-based typed event system
- Session persistence and daemon restart recovery
- Orphan worktree cleanup on startup
- Tunnel capability (cloudflared)
- Workspace templates (nextjs, research, writing, coding)
- Per-channel message history persistence (JSONL)
- `/complete` with auto-commit + rebase merge
- Permission request flow (Allow/Deny buttons)
- Install script with launchd daemon setup

### Known Limitations

- No agent crash auto-restart (session dies if agent process crashes)
- No Telegram reconnection recovery
- No cost tracking or budget limits per session
- No multi-agent orchestration across sessions
- Templates are built-in only (no external registry)
- Web control plane has no authentication

## Roadmap

### Phase 1: Daily-drivable

- Agent crash auto-restart
- Telegram reconnection recovery
- `afk init` interactive wizard
- Docker image
- Core unit tests (commands, session_manager, events)
- Better error messages

### Phase 2: Work-specific features

- Multi-agent orchestration (cross-session coordination)
- Deliverable review automation (`diff_reviewer` capability)
- Cost management (`cost_guard` capability)
- Auto-test on code changes (`test_runner` capability)

### Phase 3: Workspace expansion + ecosystem

- Writing / research / data analysis workspace types
- Expanded template system
- Agent runtime expansion (Aider, etc. via AgentPort)
- Control plane expansion (Slack, Discord via ControlPlanePort)
- Capability plugin architecture

### Phase 4: Community ecosystem

- Capability registry (`afk capability install <name>`)
- Template registry (`afk template install <name>`)
- Community-contributed capabilities and templates

## Non-goals

- **General-purpose AI assistant** — AFK does not send emails, make payments, or take actions on your behalf. It focuses exclusively on producing file-based project deliverables: code, research artifacts, and documents.
- **Custom LLM integration layer** — AFK does not replace or reinvent LLM runtimes. It orchestrates existing agent runtimes (Claude Code, Codex, etc.) and wraps them in a unified session lifecycle.
- **Cloud-hosted service** — All state, logs, worktrees, and artifacts live on your local filesystem. The only outbound requests are to the LLM APIs and agent runtimes you configure.
