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

---

## Phase 2 — Remote Verification

The core problem: AFK lets you **command** agents remotely, but you can't **verify** what they built without walking back to the terminal. Verification must work across project types (web, iOS, Android) and across parallel sessions.

### Design Decisions

**Session ↔ Tunnel relationship**

Each session runs in an isolated worktree. A session may or may not run a dev server. When it does, the port belongs to that session.

- **1 tunnel per session** — each `/tunnel` is scoped to the session topic where it's called
- Tunnel lifecycle is tied to the session: `/stop` or `/complete` kills the tunnel too
- Multiple sessions can each have their own tunnel simultaneously (different ports, different URLs)
- If a session doesn't run a server, no tunnel is needed

**Project type determines verification method**

Not all projects are web apps. The verification strategy depends on what the agent is building:

| Project Type | Verification Method | How it Works |
|---|---|---|
| Web (React, Next, Express, etc.) | **Tunnel** | cloudflared quick tunnel → public HTTPS URL → open in phone browser |
| React Native (Expo) | **Tunnel + Expo Go** | Expo dev server tunneled → scan QR or open URL in Expo Go app |
| iOS (Swift/SwiftUI) | **Simulator Screenshot** | Build & run on iOS Simulator → capture screenshot → send via Telegram |
| Android (Kotlin/Compose) | **Emulator Screenshot + APK** | Build & run on emulator → screenshot, or build APK → send via Telegram → install |
| Flutter | **Web build + Tunnel** or **Simulator Screenshot** | Flutter web for quick check, or simulator/emulator for native feel |
| CLI / Backend / Library | **Output capture** | Run command → capture stdout/stderr → send result via Telegram |

**Incremental approach**: Start with Tunnel (covers web + Expo), then Screenshot (covers iOS/Android simulators), then Build Artifact transfer (APK/IPA). Each layer adds coverage for more project types.

### 2.1 Tunneling (`/tunnel`)

Expose a localhost port to a public HTTPS URL via cloudflared quick tunnel.

```
/tunnel 3000
→ Starts cloudflared tunnel → https://xxx.trycloudflare.com
→ URL sent to session topic
→ Open on phone, verify immediately

/tunnel
→ (no port specified) Auto-scan common ports (3000, 5173, 8080, 4200, 8000, 19000)
→ Tunnel the first open port found

/tunnel off
→ Kill tunnel process for this session
```

Implementation:
- cloudflared subprocess per session, managed alongside ClaudeProcess
- Parse URL from cloudflared stderr (`INF |  https://xxx.trycloudflare.com`)
- No account/config needed (quick tunnel mode)
- System requirement: `brew install cloudflared`

Edge cases:
- Port not yet open when `/tunnel` is called → retry with backoff, or watch for port
- Session uses multiple ports (e.g. frontend 3000 + backend 8000) → `/tunnel 3000`, `/tunnel 8000` both allowed per session
- Claude restarts the dev server on a different port → user re-runs `/tunnel`

### 2.2 Screenshot (`/screenshot`)

Capture what's running locally and send as an image via Telegram.

```
/screenshot
→ Captures localhost:{tunneled_port} via headless browser
→ Sends image to session topic

/screenshot http://localhost:8080/dashboard
→ Captures specific URL

/screenshot simulator
→ Captures iOS Simulator or Android Emulator screen
```

Implementation:
- **Web**: Playwright headless → screenshot → send via `messenger.send_photo()`
- **iOS Simulator**: `xcrun simctl io booted screenshot` → send image
- **Android Emulator**: `adb exec-out screencap -p` → send image
- Viewport: mobile (390×844) by default, `/screenshot --desktop` for 1280×720

### 2.3 Diff & File Transfer (`/diff`)

Review code changes before merging.

```
/diff
→ git diff summary for this session's worktree
→ Files changed, lines added/deleted
→ Sent as formatted message (or file if too long)

/file path/to/file.ts
→ Download a specific file from the worktree via Telegram
```

### 2.4 Build Artifacts

For native mobile apps, send installable artifacts.

```
/build
→ Detect project type, run appropriate build command
→ Send artifact via Telegram

Web:     Build → deploy preview (or just use tunnel)
Android: ./gradlew assembleDebug → send APK
iOS:     xcodebuild → send to TestFlight (requires setup)
Expo:    eas build → send URL
```

This is the hardest to generalize. Start with Android APK (straightforward), defer iOS/TestFlight.

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
