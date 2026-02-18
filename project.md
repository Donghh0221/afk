# AFK — Code while AFK.

A remote control plane for AI coding agents.
Issue commands, observe progress, and intervene — without sitting at a terminal.

> Developers, get off your ass.

## Target Users

**Solo entrepreneurs — especially vibe coders.**

They don't read source code themselves. They tell AI what to do, check the results, and move on.
They run multiple projects simultaneously, issue instructions from their phone, and only care whether the output works.

For these people, the terminal is a bottleneck. AFK removes that bottleneck.

## Core Principles

- **Input is voice, output is text**: Optimized for human I/O bandwidth
- **Messenger is the control plane**: Starting with Telegram (MVP), control from anywhere
- **Session = isolated workspace**: Session isolation, concurrent multitasking
- **Messenger-agnostic architecture**: MessengerPort abstraction allows swapping Telegram/Slack/native app
- **STT-agnostic architecture**: STTPort abstraction allows swapping Whisper local/API/Deepgram
- **Always-on server**: Runs as a Mac mini daemon 24/7, accessible from any device

## Deployment Architecture

```
📱 Phone (on the go)     💻 MacBook (home/cafe)      🖥️ Mac mini (always ON)
│                        │                           │
│ Telegram voice         │ Telegram text              │ AFK Server (daemon)
│ → commands, approvals  │ + terminal client (future)  │ Claude Code ×N
│                        │ → detailed review, polish   │ Session state preserved
│                        │                           │
└────────────────────────┴───────────────────────────┘
         Seamlessly connected via Telegram multi-device
```

Mac mini serves as the always-on server. Phone/MacBook act as clients only.
Telegram natively supports multi-device, so no extra implementation needed for MVP.

**Daily scenario:**

```
[Morning, before heading out]
Phone voice: "Add payment feature to MyApp. Stripe integration, webhook handling"
→ Agent starts working on Mac mini

[Commuting]
Phone notification: ⚠️ Stripe API key is required
Phone voice: "Allow"
→ Agent continues working

[Arriving at cafe, open MacBook]
MacBook Telegram: Check progress, verify results via tunnel

[Back home]
MacBook terminal: afk attach → hands-on finishing touches (future)
```

## Tech Stack

- Claude Code headless mode (`--input-format stream-json --output-format stream-json`)
- Python + asyncio
- Telegram supergroup forum topics (MVP messenger)
- Whisper local (MVP STT, Mac M-chip base model)
- launchd daemon (Mac mini always-on)

---

## Phase 1 — MVP

Minimum functionality to talk to Claude Code without a terminal.

### 1.1 Project Registration

Register local folder paths with a name. Reference by name when creating sessions.

| Command | Description | Example |
|---|---|---|
| `/project add <path> <name>` | Register project | `/project add ~/projects/myapp MyApp` |
| `/project list` | List registered projects | |
| `/project remove <name>` | Unregister project | `/project remove MyApp` |

- Only available in General topic
- Persisted in local JSON file

### 1.2 Session Management

Session = one Claude Code subprocess + one Telegram forum topic.

| Command | Location | Description |
|---|---|---|
| `/new <project_name>` | General | Create new topic + session |
| `/sessions` | General | List all active sessions |
| `/stop` | Session topic | Stop session (kill process, preserve topic) |
| `/resume` | Session topic | Restore dead session (`--resume <session_id>`) |
| `/status` | Session topic | Query status (idle / running / waiting_permission) |

- Topic name on `/new`: `{project_name}-session-{number}`
- Multiple sessions per project supported

### 1.3 Prompt Delivery & Response

- **Text message** in session topic → forwarded as prompt to Claude Code
- **Voice message** in session topic → converted via STTPort (Whisper) → forwarded to Claude Code
  - Converted text shown for confirmation, then auto-forwarded
- Claude Code responses streamed in real-time (stream-json parsing)
- Messages over 4096 chars auto-split
- Cost info displayed on task completion

```
🎤 User: (voice message)
🤖 Bot:    🎤 "Add JWT auth to the login API"
           ⏳ Forwarding task...
🤖 Bot:    📝 Task started...
🤖 Bot:    [streaming response]
🤖 Bot:    ✅ Done ($0.05)
```

### 1.4 Permission Handling

When Claude Code requests tool permission, displayed as inline buttons:

```
⚠️ Tool execution request
🔧 Bash: npm init -y && npm install express

[✅ Allow] [❌ Deny] [🔓 Always Allow]
```

- Tool name + argument summary displayed
- Approval/denial forwarded to Claude Code
- 5-minute timeout, auto-deny on expiry
- Voice response ("allow"/"deny") also supported

### 1.5 Trust Levels

Can't use it if approval requests come every 5 minutes during a walk. Need adjustable autonomy for the situation.

| Level | Name | Auto-approval Scope | Use Case |
|---|---|---|---|
| 1 | 🔒 Strict | None (ask everything) | Production work, dangerous operations |
| 2 | 🔓 Normal | Auto-approve file read/write | General development (default) |
| 3 | 🚀 YOLO | Auto-approve everything (except `rm -rf`, `git push`) | Walk mode, vibe coding |

- `/trust <level>` — change trust level per session
- High-risk commands (`rm`, `git push`, `curl | sh`, etc.) always require approval at all levels

### 1.6 Cost Control

Cost awareness fades when working remotely. Safety nets needed.

- Per-session real-time cost display (on task completion)
- `/cost` — query session cumulative cost
- Daily budget configurable (`/budget $20`)
- Auto-pause + notification on budget exceeded

### 1.7 Thrashing Detection

Prevent the agent from spinning its wheels and burning costs.

Detection conditions:
- Same file modified 3+ times
- Same error causes repeated test failures
- Cost spikes in a short period ($5+ in minutes)

On detection:
- Auto-pause
- Notification: "🔴 Agent has been stuck on the same error for 10 minutes. Want to intervene?"
- Current situation summary provided

---

## Phase 2 — Verification

Verify agent output remotely.

### 2.1 Local App Tunneling

- When agent starts a localhost server, auto-generate a public URL
- `/tunnel on` → send `https://abc123.tunnel.dev` link via Telegram
- Access directly from phone to verify
- Uses cloudflared or ngrok, auto-managed per session

### 2.2 Screenshots / Preview

- `/screenshot` → capture current local app via headless browser, send image
- Mobile/desktop viewport toggle
- "What does it look like now?" → instant screenshot

### 2.3 File Transfer

- Download files created/modified by agent via Telegram
- Upload files from Telegram → apply to project
- `/diff` → list of files changed in this session + diff summary

### 2.4 Mobile App Testing

Web apps can be verified via tunneling, but native mobile apps need framework-specific approaches.

| Framework | Method | Difficulty |
|---|---|---|
| React Native (Expo) | Expo dev server + tunneling → verify on phone via Expo Go | Low |
| Flutter | Web build + tunneling (mobile UI approximation) | Medium |
| Native Android | APK build → send file via messenger → install on phone | Medium |
| Native iOS | Simulator screenshot → send / TestFlight automation | High |

Additional options:
- Simulator/emulator screen capture → send as image
- Cloud device farm integration (BrowserStack, Firebase Test Lab)

### 2.5 Terminal Client

CLI tool for directly accessing sessions from MacBook.

```bash
afk list                        # List active sessions
afk attach MyApp-session-1      # Attach to session (like tmux attach)
afk detach                      # Detach from session
```

- Connect from MacBook to sessions running on Mac mini
- Richer interface than Telegram (full terminal)
- Ideal for detailed review, debugging, finishing touches

---

## Phase 3 — Observation

Real-time tracking of what the agent is doing.

### 3.1 Real-time Log Streaming

- Forward stdout/stderr from agent-launched servers/processes to Telegram
- Auto-notification on errors: "🔴 Server crash — TypeError at line 42"
- `/logs on|off` to toggle

### 3.2 Git Activity Monitoring

- Summary notification on each agent commit
- `/log` → recent commit history
- Change statistics (file count, lines added/deleted)

### 3.3 Checkpoints & Rollback

- `/checkpoint` → save current state as git commit/stash
- `/rollback` → restore to previous checkpoint
- "Start over from here" when agent messes up

---

## Phase 4 — Quality Gates

Automated verification of agent work.

### 4.1 Automated Testing

- Auto-run tests on code changes
- Test failure → Telegram notification + option to auto-feedback to agent
- `/test` → manual test trigger

### 4.2 Lint / Type Check

- Lint result summary after code changes
- Option to auto-forward errors to agent ("fix this")

### 4.3 Process Control

- `/ps` → list child processes created by session
- `/kill <pid>` → force-kill specific process
- Remotely restart/stop dev servers launched by agent

---

## Phase 5 — Orchestration

Multi-agent coordination.

### 5.1 Parallel Session Coordination

- Run frontend + backend sessions concurrently
- Inject one session's output as context to another
- `/broadcast "API spec changed"` → batch forward to related sessions

### 5.2 Inter-session Dependencies

- "Start frontend after backend API is complete"
- Pipeline definitions

### 5.3 Environment / Secret Management

- Agent says "need API key" → securely input via Telegram
- Manage `.env` through Telegram conversation

---

## Telegram Group Structure (MVP)

```
📱 Supergroup (Forum mode ON)
│
├── 📌 General              ← Project/session management
├── 💬 MyApp-session-1      ← Claude Code session
├── 💬 MyApp-session-2      ← Same project, second session
└── 💬 Backend-session-1    ← Different project session
```

### Initial Setup (one-time)

1. Create a group in Telegram
2. Convert to supergroup + enable "Topics"
3. Add bot to group as admin (with topic create/manage permissions)
4. Set bot token and group ID in config

### Notification Strategy

- Log-like messages (streaming responses, status changes) → `disable_notification=True` (silent)
- Important messages (permission requests, errors, task completion) → normal notification
