# AFK — Code while AFK

A remote control plane for Claude Code. Issue commands via Telegram (voice or text) from any device, while your Mac mini runs coding sessions 24/7.

## Why

Terminals are a bottleneck. You sit at a desk, type prompts, wait for responses, approve permissions — one session at a time, one screen at a time.

AFK breaks that loop:

- **Work from anywhere.** Send a voice message from your phone while commuting. Claude Code runs on your Mac mini back home.
- **Run multiple sessions.** Each Telegram forum topic is an isolated Claude Code session. Start one for frontend, another for backend, check in when you want.
- **Stay in control without being present.** Permission requests arrive as push notifications with approve/deny buttons. No terminal window required.
- **See what's happening.** A built-in web dashboard shows live session activity, message history, and daemon logs — all at `localhost:7777`.

The target user is a solo entrepreneur or vibe coder who tells AI what to build, checks results, and moves on. AFK makes that workflow mobile.

## How It Works

```
Phone / MacBook                         Mac mini (always on)
│                                       │
│  Telegram voice or text  ────────────►│  AFK daemon
│                                       │    ├── Telegram bot (receives messages)
│                                       │    ├── Orchestrator (routes to sessions)
│                                       │    ├── Session Manager
│  ◄──── streaming responses,           │    │   ├── Session A → Claude Code subprocess
│        permission buttons,             │    │   └── Session B → Claude Code subprocess
│        completion notifications        │    └── Dashboard (localhost:7777)
│                                       │
```

1. You send a message (text or voice) in a Telegram forum topic
2. AFK routes it to the Claude Code subprocess tied to that topic
3. Claude Code streams responses back — forwarded to Telegram silently
4. When Claude Code needs permission to run a tool, you get a notification with Allow/Deny buttons
5. On completion, you see cost and duration

## Prerequisites

- **macOS** (tested on Apple Silicon)
- **Python 3.11+**
- **Claude Code CLI** installed and authenticated (`claude` must be in PATH)
- **Telegram Bot** — create one via [@BotFather](https://t.me/BotFather)
- **Telegram Supergroup** with Topics (forum mode) enabled, bot added as admin

## Setup

### 1. Create Telegram Bot & Group

1. Message [@BotFather](https://t.me/BotFather) on Telegram → `/newbot` → save the **bot token**
2. Create a new Telegram group
3. Convert it to a supergroup: Group Settings → scroll down → "Topics" → enable
4. Add your bot to the group as **admin** (needs permissions: manage topics, send messages)
5. Get the **group ID**: add [@raw_data_bot](https://t.me/raw_data_bot) to the group, it will print the chat ID (negative number)

### 2. Install AFK

```bash
git clone <repo-url> && cd afk

# Using uv (recommended)
uv sync

# Or using pip
pip install -e .
```

### 3. Configure Environment

Create a `.env` file or export these variables:

```bash
export AFK_TELEGRAM_BOT_TOKEN="your-bot-token-here"
export AFK_TELEGRAM_GROUP_ID="-100xxxxxxxxxx"
# Optional
export AFK_DASHBOARD_PORT="7777"
```

### 4. Run

```bash
# With uv
uv run afk

# Or directly
python -m afk.main
```

AFK starts the Telegram bot and the dashboard server. You'll see:

```
AFK is running. Press Ctrl+C to stop.
Dashboard running at http://localhost:7777
```

### 5. Run as a Daemon (optional)

To keep AFK running 24/7 on a Mac mini, create a launchd plist:

```bash
cat > ~/Library/LaunchAgents/com.afk.daemon.plist << 'EOF'
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.afk.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>/path/to/uv</string>
        <string>run</string>
        <string>afk</string>
    </array>
    <key>WorkingDirectory</key>
    <string>/path/to/afk</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AFK_TELEGRAM_BOT_TOKEN</key>
        <string>your-token</string>
        <key>AFK_TELEGRAM_GROUP_ID</key>
        <string>-100xxxxxxxxxx</string>
    </dict>
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
EOF

launchctl load ~/Library/LaunchAgents/com.afk.daemon.plist
```

## Usage

All interaction happens in your Telegram supergroup.

### Register a Project

In the **General** topic:

```
/project add ~/projects/myapp MyApp
/project list
/project remove MyApp
```

### Start a Session

In the **General** topic:

```
/new MyApp
```

This creates a new forum topic (`MyApp-session-1`) and starts a Claude Code subprocess pointed at your project directory.

### Send Prompts

Switch to the session topic and type (or voice-message) your instructions:

```
Add Stripe payment integration with webhook handling
```

Claude Code works on it. You'll see streaming tool calls and responses. When it's done:

```
✅ Done ($0.0523, 12.3s)
```

### Manage Sessions

```
/sessions          # List all active sessions (General topic)
/status            # Check current session state (session topic)
/stop              # Stop current session (session topic)
```

### Dashboard

Open `http://localhost:7777` in a browser to see:

- Active sessions with live state indicators
- Per-session message history (user prompts, assistant responses, tool calls)
- Daemon log viewer

## Architecture

Hexagonal (port-adapter) pattern with two abstraction boundaries:

- **MessengerPort** — abstract interface for messenger integrations. Telegram is the MVP adapter; Slack/Discord/native app can be added without touching core logic.
- **STTPort** — abstract interface for speech-to-text (planned). Whisper local is the MVP implementation.

```
afk/
├── main.py              # Entry point, wires everything together
├── config.py            # Environment variables → Config dataclass
├── messenger/
│   ├── port.py          # MessengerPort protocol
│   └── telegram/
│       └── adapter.py   # Telegram bot (forum topics, inline buttons)
├── core/
│   ├── orchestrator.py  # Routes messages/commands to sessions
│   ├── session_manager.py  # Session lifecycle (create/stop/query)
│   └── claude_process.py   # Claude Code subprocess (stream-json stdin/stdout)
├── dashboard/
│   ├── server.py        # aiohttp web server + REST API
│   ├── message_store.py # In-memory per-session message history
│   └── index.html       # Single-page dashboard UI
├── storage/
│   └── project_store.py # Project name → path registry (JSON file)
└── data/                # Runtime data (gitignored)
```

## License

TBD
