#!/usr/bin/env bash
set -euo pipefail

# AFK Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/<owner>/afk/main/install.sh | bash

AFK_REPO="https://github.com/Donghh0221/afk.git"
AFK_DIR="${AFK_INSTALL_DIR:-$HOME/afk}"

# ── Colors ──────────────────────────────────────────────────────────────────
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
CYAN='\033[0;36m'
BOLD='\033[1m'
DIM='\033[2m'
RESET='\033[0m'

info()  { printf "${BLUE}▸${RESET} %s\n" "$*"; }
ok()    { printf "${GREEN}✓${RESET} %s\n" "$*"; }
warn()  { printf "${YELLOW}!${RESET} %s\n" "$*"; }
fail()  { printf "${RED}✗${RESET} %s\n" "$*"; exit 1; }

header() {
  echo ""
  printf "${BOLD}${CYAN}%s${RESET}\n" "$*"
  printf "${DIM}%s${RESET}\n" "$(printf '%.0s─' $(seq 1 ${#1}))"
}

# ── Platform check ──────────────────────────────────────────────────────────
header "AFK Installer"
echo ""

if [[ "$(uname -s)" != "Darwin" ]]; then
  warn "AFK is designed for macOS. It may work on Linux but is untested."
  read -rp "Continue anyway? [y/N] " yn
  [[ "$yn" =~ ^[Yy]$ ]] || exit 0
fi

# ── Prerequisites ───────────────────────────────────────────────────────────
header "Checking prerequisites"

# Git
if command -v git &>/dev/null; then
  ok "git $(git --version | cut -d' ' -f3)"
else
  fail "git is required. Install via: xcode-select --install"
fi

# Python 3.11+
check_python() {
  local py="$1"
  if command -v "$py" &>/dev/null; then
    local ver
    ver=$("$py" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null)
    local major minor
    major=$(echo "$ver" | cut -d. -f1)
    minor=$(echo "$ver" | cut -d. -f2)
    if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
      echo "$ver"
      return 0
    fi
  fi
  return 1
}

PYTHON_VER=""
for py in python3 python; do
  if ver=$(check_python "$py"); then
    PYTHON_VER="$ver"
    ok "Python $PYTHON_VER"
    break
  fi
done

if [[ -z "$PYTHON_VER" ]]; then
  fail "Python 3.11+ is required. Install via: brew install python@3.13"
fi

# uv
if command -v uv &>/dev/null; then
  ok "uv $(uv --version | cut -d' ' -f2)"
else
  warn "uv is not installed (recommended Python package manager)"
  read -rp "Install uv now? [Y/n] " yn
  if [[ ! "$yn" =~ ^[Nn]$ ]]; then
    info "Installing uv..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    # Source the env so uv is available in this session
    export PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"
    if command -v uv &>/dev/null; then
      ok "uv installed ($(uv --version | cut -d' ' -f2))"
    else
      fail "uv installation failed. Install manually: https://docs.astral.sh/uv/"
    fi
  else
    fail "uv is required. Install via: curl -LsSf https://astral.sh/uv/install.sh | sh"
  fi
fi

# Claude Code CLI
if command -v claude &>/dev/null; then
  ok "Claude Code CLI found"
else
  warn "Claude Code CLI not found in PATH"
  warn "AFK requires Claude Code CLI to run agent sessions."
  echo ""
  info "Install it via: npm install -g @anthropic-ai/claude-code"
  info "Then authenticate: claude"
  echo ""
  read -rp "Continue without Claude Code CLI? [y/N] " yn
  [[ "$yn" =~ ^[Yy]$ ]] || exit 0
fi

# ── Clone / Update ──────────────────────────────────────────────────────────
header "Installing AFK"

if [[ -d "$AFK_DIR" ]]; then
  info "AFK directory already exists at $AFK_DIR"
  read -rp "Update existing installation? [Y/n] " yn
  if [[ ! "$yn" =~ ^[Nn]$ ]]; then
    info "Pulling latest changes..."
    git -C "$AFK_DIR" pull --ff-only || warn "Could not pull (you may have local changes)"
  fi
else
  info "Cloning AFK to $AFK_DIR..."
  git clone "$AFK_REPO" "$AFK_DIR"
fi

cd "$AFK_DIR"

# ── Install dependencies ────────────────────────────────────────────────────
info "Installing Python dependencies..."
uv sync
ok "Dependencies installed"

# ── Environment setup ───────────────────────────────────────────────────────
header "Configuration"

if [[ -f "$AFK_DIR/.env" ]]; then
  info "Existing .env file found"
  read -rp "Reconfigure? [y/N] " yn
  if [[ ! "$yn" =~ ^[Yy]$ ]]; then
    SKIP_ENV=true
  fi
fi

if [[ "${SKIP_ENV:-}" != "true" ]]; then
  echo ""
  info "Let's set up your environment variables."
  info "You'll need a Telegram bot token and group ID."
  echo ""
  printf "${DIM}  How to get these:${RESET}\n"
  printf "${DIM}  1. Bot token: Message @BotFather on Telegram → /newbot${RESET}\n"
  printf "${DIM}  2. Group ID:  Create a supergroup with Topics enabled,${RESET}\n"
  printf "${DIM}     add @raw_data_bot to get the chat ID (negative number)${RESET}\n"
  echo ""

  read -rp "Telegram Bot Token: " BOT_TOKEN
  read -rp "Telegram Group ID (e.g. -100xxxxxxxxxx): " GROUP_ID

  echo ""
  read -rp "OpenAI API Key for voice transcription (optional, press Enter to skip): " OPENAI_KEY
  read -rp "Dashboard port (default: 7777): " DASHBOARD_PORT
  DASHBOARD_PORT="${DASHBOARD_PORT:-7777}"

  cat > "$AFK_DIR/.env" << EOF
AFK_TELEGRAM_BOT_TOKEN="${BOT_TOKEN}"
AFK_TELEGRAM_GROUP_ID="${GROUP_ID}"
AFK_DASHBOARD_PORT="${DASHBOARD_PORT}"
EOF

  if [[ -n "$OPENAI_KEY" ]]; then
    echo "AFK_OPENAI_API_KEY=\"${OPENAI_KEY}\"" >> "$AFK_DIR/.env"
  fi

  ok ".env file created"
fi

# ── Daemon setup (optional) ────────────────────────────────────────────────
header "Daemon Setup (optional)"

echo ""
info "AFK can run as a background daemon via launchd (starts on login, restarts on crash)."
read -rp "Set up launchd daemon? [y/N] " yn

if [[ "$yn" =~ ^[Yy]$ ]]; then
  UV_PATH="$(which uv)"
  PLIST_PATH="$HOME/Library/LaunchAgents/com.afk.daemon.plist"

  # Source .env to get env vars for the plist
  set -a
  source "$AFK_DIR/.env"
  set +a

  mkdir -p "$HOME/Library/LaunchAgents"

  cat > "$PLIST_PATH" << EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
    <key>Label</key>
    <string>com.afk.daemon</string>
    <key>ProgramArguments</key>
    <array>
        <string>${UV_PATH}</string>
        <string>run</string>
        <string>afk</string>
    </array>
    <key>WorkingDirectory</key>
    <string>${AFK_DIR}</string>
    <key>EnvironmentVariables</key>
    <dict>
        <key>AFK_TELEGRAM_BOT_TOKEN</key>
        <string>${AFK_TELEGRAM_BOT_TOKEN}</string>
        <key>AFK_TELEGRAM_GROUP_ID</key>
        <string>${AFK_TELEGRAM_GROUP_ID}</string>
        <key>AFK_DASHBOARD_PORT</key>
        <string>${AFK_DASHBOARD_PORT:-7777}</string>$(if [[ -n "${AFK_OPENAI_API_KEY:-}" ]]; then cat << INNER

        <key>AFK_OPENAI_API_KEY</key>
        <string>${AFK_OPENAI_API_KEY}</string>
INNER
fi)
        <key>PATH</key>
        <string>/usr/local/bin:/usr/bin:/bin:/opt/homebrew/bin:${HOME}/.local/bin:${HOME}/.cargo/bin</string>
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

  ok "Launchd plist created at $PLIST_PATH"

  read -rp "Start the daemon now? [Y/n] " yn
  if [[ ! "$yn" =~ ^[Nn]$ ]]; then
    launchctl load "$PLIST_PATH" 2>/dev/null || true
    ok "Daemon started"
    info "Logs: tail -f /tmp/afk.out.log /tmp/afk.err.log"
  else
    info "Start later with: launchctl load $PLIST_PATH"
  fi
fi

# ── Done ────────────────────────────────────────────────────────────────────
header "Installation complete!"
echo ""
printf "  ${BOLD}To run AFK:${RESET}\n"
printf "    cd %s && uv run afk\n" "$AFK_DIR"
echo ""
printf "  ${BOLD}Telegram setup:${RESET}\n"
printf "    1. Add your bot to a supergroup as admin\n"
printf "    2. Enable Topics (forum mode) in group settings\n"
printf "    3. Send /project add ~/myproject MyProject\n"
printf "    4. Send /new MyProject to start a session\n"
echo ""
printf "  ${BOLD}Dashboard:${RESET}\n"
printf "    http://localhost:${AFK_DASHBOARD_PORT:-7777}\n"
echo ""
printf "  ${BOLD}Docs:${RESET}\n"
printf "    %s/README.md\n" "$AFK_DIR"
echo ""
ok "Happy coding while AFK!"
