#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────────────────────
# HushClaw Installer  —  macOS & Linux
#
# Usage:
#   bash install.sh              # install + start in background
#   bash install.sh --update     # stop old process, update, restart in background
#   bash install.sh --start-only # skip install, start existing installation in background
#   bash install.sh --stop       # stop the running server and exit
#   bash install.sh --uninstall  # stop + remove installation (prompts to keep/delete data)
#   bash install.sh --uninstall --purge  # uninstall AND delete all data (memory, config)
#   bash install.sh --foreground # install + start in foreground (debug mode)
#   bash install.sh --skill-force-official # force overwrite bundled skills
#   bash install.sh --update --backup-before-overwrite
#
# Environment overrides:
#   HUSHCLAW_HOME=<dir>   installation directory  (default: ~/.hushclaw)
#   HUSHCLAW_PORT=<port>  server port             (default: 8765)
#   HUSHCLAW_HOST=<host>  bind address            (default: 0.0.0.0)
#   HUSHCLAW_NO_BROWSER=1 skip browser auto-open
#   HUSHCLAW_PYTHON=<cmd_or_abs_path> force Python executable (e.g. python3)
# ─────────────────────────────────────────────────────────────────────────────

set -euo pipefail

ORIGINAL_ARGS=("$@")

REPO_URL="https://github.com/CNTWDev/hushclaw.git"
INSTALL_DIR="${HUSHCLAW_HOME:-$HOME/.hushclaw}"
PORT="${HUSHCLAW_PORT:-8765}"
BIND="${HUSHCLAW_HOST:-0.0.0.0}"
API_PORT=$((PORT + 1))   # Secondary API port used by the POST proxy.
NO_BROWSER="${HUSHCLAW_NO_BROWSER:-}"
PYTHON_OVERRIDE="${HUSHCLAW_PYTHON:-}"

PID_FILE="$INSTALL_DIR/hushclaw.pid"
LOG_FILE="$INSTALL_DIR/hushclaw.log"

# ── Terminal colours ──────────────────────────────────────────────────────────
if [[ -t 1 ]]; then
  RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
  BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'
else
  RED=''; GREEN=''; YELLOW=''; BLUE=''; CYAN=''; BOLD=''; NC=''
fi

info()    { echo -e "${CYAN}  ▸${NC}  $*"; }
ok()      { echo -e "${GREEN}  ✓${NC}  $*"; }
warn()    { echo -e "${YELLOW}  !${NC}  $*"; }
error()   { echo -e "${RED}  ✗${NC}  $*" >&2; }
die()     { error "$*"; exit 1; }
section() { echo -e "\n${BOLD}${BLUE}══ $* ${NC}"; }
detail()  { echo -e "${BLUE}    ·${NC}  $*"; }
detail_ok() { echo -e "${GREEN}    ✓${NC}  $*"; }
detail_warn() { echo -e "${YELLOW}    !${NC}  $*"; }

render_structured_line() {
  local line="$1"
  case "$line" in
    ok\|*) detail_ok "${line#ok|}" ;;
    warn\|*) detail_warn "${line#warn|}" ;;
    summary\|*) detail "${line#summary|}" ;;
    info\|*) detail "${line#info|}" ;;
    *) detail "$line" ;;
  esac
}

render_skill_sync_line() {
  local line="$1"
  case "$line" in
    "[installed]"*) detail_ok "Installed ${line#"[installed] "}" ;;
    "[updated]"*) detail_ok "Updated ${line#"[updated] "}" ;;
    "[forced_updated]"*) detail_warn "Replaced ${line#"[forced_updated] "}" ;;
    "[skipped_dirty]"*) detail_warn "Preserved local copy ${line#"[skipped_dirty] "}" ;;
    "[skipped_error]"*) detail_warn "Skipped ${line#"[skipped_error] "}" ;;
    summary\ *) detail "Summary: ${line#summary }" ;;
    *) detail "$line" ;;
  esac
}

# ── Parse args ────────────────────────────────────────────────────────────────
MODE="install"
FOREGROUND=false
OVERWRITE_INSTALL=false
BACKUP_BEFORE_OVERWRITE=false
SKILL_POLICY=""          # resolved after arg parsing
SKILL_POLICY_EXPLICIT=false
PURGE_DATA=false
DISTRO="personal"
DISTRO_EXPLICIT=false
while [[ $# -gt 0 ]]; do
  case "$1" in
    --update)     MODE="update";     shift ;;
    --start-only) MODE="start";      shift ;;
    --stop)       MODE="stop";       shift ;;
    --uninstall)  MODE="uninstall";  shift ;;
    --purge)      PURGE_DATA=true;   shift ;;
    --foreground) FOREGROUND=true;   shift ;;
    --overwrite-install) OVERWRITE_INSTALL=true; shift ;;
    --backup-before-overwrite) BACKUP_BEFORE_OVERWRITE=true; shift ;;
    --skill-force-official) SKILL_POLICY="force_official"; SKILL_POLICY_EXPLICIT=true; shift ;;
    --skill-preserve-local) SKILL_POLICY="preserve_skip";  SKILL_POLICY_EXPLICIT=true; shift ;;
    --distro)
      if [[ $# -lt 2 ]]; then die "--distro requires a value: personal"; fi
      DISTRO="$2"; DISTRO_EXPLICIT=true; shift 2 ;;
    --distro=*)   DISTRO="${1#--distro=}"; DISTRO_EXPLICIT=true; shift ;;
    --help|-h)
      echo "Usage: $0 [--update | --start-only | --stop | --uninstall [--purge] | --foreground | --distro personal | --overwrite-install | --backup-before-overwrite | --skill-force-official | --skill-preserve-local]"
      echo "  (no flag)           Install HushClaw and start server in background"
      echo "  --update            Stop old process, pull latest code, restart in background"
      echo "  --start-only        Skip install, start existing installation in background"
      echo "  --stop              Stop the running HushClaw server and exit"
      echo "  --uninstall         Stop server, remove installation files (prompts about data)"
      echo "  --uninstall --purge Remove installation AND all data (memory.db, config) — no prompt"
      echo "  --foreground        Install and start server in foreground (debug mode)"
      echo "  --distro personal   Run as personal assistant (default)"
      echo "  --skill-force-official Force overwrite bundled skills even if locally modified"
      echo "  --skill-preserve-local Keep locally modified bundled skills (default for fresh install)"
      exit 0
      ;;
    *) die "Unknown argument: $1. Use --help for usage." ;;
  esac
done

# Validate distro
case "$DISTRO" in
  personal) ;;
  *) die "Unknown distro: $DISTRO. Supported values: personal" ;;
esac

web_path_for_distro() {
  echo "/personal"
}

# Resolve skill update policy: explicit flag > env var > mode-based default
# --update (upgrade path) defaults to force_official so new bundled skills always land
if [ "$SKILL_POLICY_EXPLICIT" = "false" ]; then
  if [ -n "${HUSHCLAW_SKILL_POLICY:-}" ]; then
    SKILL_POLICY="$HUSHCLAW_SKILL_POLICY"
  elif [ "$MODE" = "update" ]; then
    SKILL_POLICY="force_official"
  else
    SKILL_POLICY="preserve_skip"
  fi
fi

# ── Process management helpers ────────────────────────────────────────────────

find_running_pid() {
  # 1. Check PID file first, verify process is alive
  if [[ -f "$PID_FILE" ]]; then
    local pid
    pid=$(cat "$PID_FILE")
    if kill -0 "$pid" 2>/dev/null; then
      echo "$pid"
      return
    fi
    rm -f "$PID_FILE"   # stale PID file
  fi
  # 2. Fallback: scan common command-line shapes (handles cross-script restarts)
  local pattern pid=""
  for pattern in \
    "hushclaw serve" \
    "hushclaw.*serve" \
    "python.*hushclaw.*serve"; do
    pid=$(pgrep -f "$pattern" 2>/dev/null | head -1 || true)
    if [[ -n "$pid" ]]; then
      echo "$pid"
      return
    fi
  done

  # 3. Final fallback: detect whichever process is actively listening on the
  # configured HushClaw port. This covers packaged installs where the process
  # name shows up as plain "Python" rather than "hushclaw serve".
  if command -v lsof &>/dev/null; then
    pid=$(lsof -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null | head -1 || true)
    if [[ -n "$pid" ]]; then
      echo "$pid"
      return
    fi
  fi
  # fuser -n tcp is Linux-only; macOS fuser has different syntax and always
  # has lsof available, so skip this block on macOS to avoid garbage output.
  if [[ "${OS_NAME:-}" != "macOS" ]] && command -v fuser &>/dev/null; then
    pid=$(fuser -n tcp "$PORT" 2>/dev/null | awk '{print $1}' || true)
    if [[ -n "$pid" ]]; then
      echo "$pid"
      return
    fi
  fi
}

stop_server() {
  local pid="$1"

  # macOS: unload LaunchAgent first (prevents KeepAlive from re-launching)
  if [[ "${OS_NAME:-}" == "macOS" ]]; then
    local plist="$HOME/Library/LaunchAgents/com.hushclaw.server.plist"
    if [[ -f "$plist" ]]; then
      info "Unloading HushClaw LaunchAgent…"
      launchctl unload "$plist" 2>/dev/null || true
      ok "LaunchAgent unloaded"
      rm -f "$PID_FILE"
      return
    fi
  fi

  # For systemd-managed services, use systemctl stop (prevents Restart=always from re-launching)
  if [[ "${OS_NAME:-}" == "Linux" ]] && command -v systemctl &>/dev/null; then
    if [[ "$(id -u)" -eq 0 ]] && systemctl is-active --quiet hushclaw 2>/dev/null; then
      info "Stopping HushClaw via systemctl…"
      systemctl stop hushclaw 2>/dev/null || true
      ok "Server stopped"
      rm -f "$PID_FILE"
      return
    elif systemctl --user is-active --quiet hushclaw 2>/dev/null; then
      info "Stopping HushClaw via systemctl --user…"
      systemctl --user stop hushclaw 2>/dev/null || true
      ok "Server stopped"
      rm -f "$PID_FILE"
      return
    fi
  fi

  # Fallback: kill by PID
  info "Stopping HushClaw (PID $pid)…"
  kill -SIGTERM "$pid" 2>/dev/null || true
  local i=0
  while kill -0 "$pid" 2>/dev/null && (( i++ < 20 )); do sleep 0.5; done
  if kill -0 "$pid" 2>/dev/null; then
    kill -SIGKILL "$pid" 2>/dev/null || true
    warn "Force-killed PID $pid"
  else
    ok "Server stopped gracefully"
  fi
  rm -f "$PID_FILE"
}

# ── --stop mode: early exit ───────────────────────────────────────────────────
if [[ "$MODE" == "stop" ]]; then
  pid=$(find_running_pid)
  if [[ -z "$pid" ]]; then
    die "HushClaw is not running."
  fi
  stop_server "$pid"
  ok "HushClaw stopped."
  exit 0
fi

# ── --uninstall mode ──────────────────────────────────────────────────────────
if [[ "$MODE" == "uninstall" ]]; then
  section "Uninstalling HushClaw"

  # Determine data directory
  if [[ "$(uname -s)" == "Darwin" ]]; then
    _DATA_DIR="$HOME/Library/Application Support/hushclaw"
  else
    _DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw"
  fi

  # 1. Stop service
  pid=$(find_running_pid)
  [[ -n "$pid" ]] && stop_server "$pid"

  # Disable + remove systemd service (Linux)
  if [[ "$(uname -s)" == "Linux" ]] && command -v systemctl &>/dev/null; then
    if [[ "$(id -u)" -eq 0 ]]; then
      if systemctl is-enabled --quiet hushclaw 2>/dev/null; then
        systemctl disable hushclaw 2>/dev/null || true
        info "Disabled system service"
      fi
      if [[ -f /etc/systemd/system/hushclaw.service ]]; then
        rm -f /etc/systemd/system/hushclaw.service
        systemctl daemon-reload 2>/dev/null || true
        ok "Removed /etc/systemd/system/hushclaw.service"
      fi
    else
      if systemctl --user is-enabled --quiet hushclaw 2>/dev/null; then
        systemctl --user disable hushclaw 2>/dev/null || true
        info "Disabled user service"
      fi
      _user_svc="$HOME/.config/systemd/user/hushclaw.service"
      if [[ -f "$_user_svc" ]]; then
        rm -f "$_user_svc"
        systemctl --user daemon-reload 2>/dev/null || true
        ok "Removed user service file"
      fi
    fi
  fi

  # Unload + remove launchd agent (macOS)
  if [[ "$(uname -s)" == "Darwin" ]]; then
    _plist="$HOME/Library/LaunchAgents/com.hushclaw.server.plist"
    if [[ -f "$_plist" ]]; then
      launchctl unload "$_plist" 2>/dev/null || true
      rm -f "$_plist"
      ok "Removed LaunchAgent"
    fi
  fi

  # 2. Remove symlink
  for _bin in "$HOME/.local/bin/hushclaw" /usr/local/bin/hushclaw; do
    if [[ -L "$_bin" || -f "$_bin" ]]; then
      rm -f "$_bin"
      ok "Removed $_bin"
    fi
  done

  # 3. Remove installation directory (venv, repo, etc.)
  if [[ -d "$INSTALL_DIR" ]]; then
    rm -rf "$INSTALL_DIR"
    ok "Removed installation directory: $INSTALL_DIR"
  else
    warn "Installation directory not found: $INSTALL_DIR"
  fi

  # 4. Handle data directory (memory, config, skills)
  if [[ -d "$_DATA_DIR" ]]; then
    if [[ "$PURGE_DATA" == true ]]; then
      rm -rf "$_DATA_DIR"
      ok "Removed data directory: $_DATA_DIR"
    elif [[ -t 0 ]]; then
      echo ""
      echo -e "  ${BOLD}Data directory found:${NC} $_DATA_DIR"
      echo -e "  ${YELLOW}  Contains: memory.db (all memories), hushclaw.toml (config), installed skills${NC}"
      echo ""
      printf "  Delete data too? This is permanent. [y/N] "
      read -r _ans
      if [[ "${_ans,,}" == "y" ]]; then
        rm -rf "$_DATA_DIR"
        ok "Removed data directory: $_DATA_DIR"
      else
        ok "Data directory kept: $_DATA_DIR"
        info "To remove later: rm -rf $(printf '%q' "$_DATA_DIR")"
      fi
    else
      warn "Data directory kept (non-interactive): $_DATA_DIR"
      info "To remove: rm -rf $(printf '%q' "$_DATA_DIR")  (or re-run with --purge)"
    fi
  fi

  echo ""
  ok "HushClaw uninstalled."
  exit 0
fi

# ── Banner ────────────────────────────────────────────────────────────────────
echo -e "${BOLD}${CYAN}"
cat <<'EOF'
    __  __           __    ________
   / / / /_  _______/ /_  / ____/ /___ __      __
  / /_/ / / / / ___/ __ \/ /   / / __ `/ | /| / /
 / __  / /_/ (__  ) / / / /___/ / /_/ /| |/ |/ /
/_/ /_/\__,_/____/_/ /_/\____/_/\__,_/ |__/|__/
EOF
echo -e "${NC}"
echo -e "  ${BOLD}Lightweight AI Agent Framework with Persistent Memory${NC}"
echo -e ""
echo -e "  ${BLUE}───────────────────────────────────────────────────────${NC}"
echo -e "  ${CYAN}https://github.com/CNTWDev/hushclaw${NC}  ${BLUE}·${NC}  tuanweishi@gmail.com"
echo -e "  ${BLUE}───────────────────────────────────────────────────────${NC}"
echo -e ""

# ── OS detection ──────────────────────────────────────────────────────────────
OS="$(uname -s)"
ARCH="$(uname -m)"
case "$OS" in
  Darwin)  OS_NAME="macOS" ;;
  Linux)   OS_NAME="Linux" ;;
  *)       die "Unsupported OS: $OS. Use install.ps1 for Windows." ;;
esac
info "Platform: ${BOLD}$OS_NAME${NC} ($ARCH)"

# ── Linux: show distro info ───────────────────────────────────────────────────
if [[ "$OS_NAME" == "Linux" ]] && [[ -f /etc/os-release ]]; then
  # shellcheck disable=SC1091
  PRETTY_NAME=""
  source /etc/os-release 2>/dev/null || true
  [[ -n "${PRETTY_NAME:-}" ]] && info "Distro:   ${BOLD}${PRETTY_NAME}${NC}"
fi

# ── Deployment mode ───────────────────────────────────────────────────────────
info "Mode:     ${BOLD}$DISTRO${NC}"

if [[ "$OS_NAME" == "macOS" ]]; then
  USER_DATA_DIR="$HOME/Library/Application Support/hushclaw"
  USER_CONFIG_DIR="$HOME/Library/Application Support/hushclaw"
else
  USER_DATA_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw"
  USER_CONFIG_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/hushclaw"
fi
INSTALL_STATE_FILE="$INSTALL_DIR/install-state.json"

repo_is_dirty() {
  [[ -d "$INSTALL_DIR/repo/.git" ]] || return 1
  ! git -C "$INSTALL_DIR/repo" diff --quiet --ignore-submodules -- 2>/dev/null && return 0
  ! git -C "$INSTALL_DIR/repo" diff --cached --quiet --ignore-submodules -- 2>/dev/null && return 0
  [[ -n "$(git -C "$INSTALL_DIR/repo" ls-files --others --exclude-standard 2>/dev/null)" ]] && return 0
  return 1
}

list_dirty_files() {
  git -C "$INSTALL_DIR/repo" status --short 2>/dev/null || true
}

backup_root_dir() {
  local ts
  ts="$(date +%Y%m%d-%H%M%S)"
  echo "$INSTALL_DIR/backups/$ts"
}

backup_user_data() {
  local root="$1"
  mkdir -p "$root"
  if [[ -d "$USER_DATA_DIR" ]]; then
    cp -R "$USER_DATA_DIR" "$root/data"
  fi
  if [[ -d "$USER_CONFIG_DIR" && "$USER_CONFIG_DIR" != "$USER_DATA_DIR" ]]; then
    cp -R "$USER_CONFIG_DIR" "$root/config"
  fi
  if [[ -f "$INSTALL_STATE_FILE" ]]; then
    cp "$INSTALL_STATE_FILE" "$root/install-state.json"
  fi
  cat > "$root/manifest.json" <<EOF
{
  "created_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "data_dir": "$(printf '%s' "$USER_DATA_DIR")",
  "config_dir": "$(printf '%s' "$USER_CONFIG_DIR")",
  "install_state": "$(printf '%s' "$INSTALL_STATE_FILE")"
}
EOF
}

backup_repo_overlay() {
  local root="$1"
  mkdir -p "$root"
  git -C "$INSTALL_DIR/repo" status --short > "$root/git-status.txt" 2>/dev/null || true
  git -C "$INSTALL_DIR/repo" diff > "$root/git-diff.patch" 2>/dev/null || true
  git -C "$INSTALL_DIR/repo" diff --cached > "$root/git-diff-cached.patch" 2>/dev/null || true
  git -C "$INSTALL_DIR/repo" ls-files --others --exclude-standard > "$root/untracked.txt" 2>/dev/null || true
}

write_install_state() {
  local backup_path="${1:-}"
  local result="${2:-ok}"
  local current_commit=""
  if [[ -d "$INSTALL_DIR/repo/.git" ]]; then
    current_commit="$(git -C "$INSTALL_DIR/repo" rev-parse --short HEAD 2>/dev/null || true)"
  fi
  mkdir -p "$INSTALL_DIR"
  cat > "$INSTALL_STATE_FILE" <<EOF
{
  "last_upgrade_at": "$(date -u +"%Y-%m-%dT%H:%M:%SZ")",
  "current_commit": "$(printf '%s' "$current_commit")",
  "backup_path": "$(printf '%s' "$backup_path")",
  "overwrite_install_used": $([[ "$OVERWRITE_INSTALL" == true ]] && echo true || echo false),
  "backup_before_overwrite": $([[ "$BACKUP_BEFORE_OVERWRITE" == true ]] && echo true || echo false),
  "last_upgrade_result": "$(printf '%s' "$result")"
}
EOF
}

# ── Linux: detect package manager ─────────────────────────────────────────────
PKG_MGR=""
if [[ "$OS_NAME" == "Linux" ]]; then
  if   command -v apt-get &>/dev/null; then PKG_MGR="apt"
  elif command -v dnf     &>/dev/null; then PKG_MGR="dnf"
  elif command -v pacman  &>/dev/null; then PKG_MGR="pacman"
  elif command -v zypper  &>/dev/null; then PKG_MGR="zypper"
  fi
  [[ -n "$PKG_MGR" ]] && info "Package manager: ${BOLD}${PKG_MGR}${NC}"
fi

# ── Privilege wrapper: use sudo only when not already root ────────────────────
run_as_root() {
  if [[ "$(id -u)" -eq 0 ]]; then
    "$@"
  elif command -v sudo &>/dev/null; then
    sudo "$@"
  else
    die "Root privileges required but 'sudo' not found. Run as root or install sudo."
  fi
}

# ── Headless detection ────────────────────────────────────────────────────────
is_headless() {
  [[ "$OS_NAME" == "Linux" && -z "${DISPLAY:-}" && -z "${WAYLAND_DISPLAY:-}" ]]
}

# ── Helpers: auto-install dependencies ────────────────────────────────────────

# macOS: ensure Homebrew is present (installs silently if missing)
# Returns 0 on success, 1 if install failed (e.g. no sudo) — caller decides what to do.
ensure_homebrew() {
  if command -v brew &>/dev/null; then
    ok "Homebrew $(brew --version 2>/dev/null | head -1)"
    return 0
  fi
  info "Homebrew not found — attempting install (requires admin rights)…"
  if ! NONINTERACTIVE=1 /bin/bash -c \
      "$(curl -fsSL https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)" \
      </dev/null 2>&1; then
    warn "Homebrew install failed (no sudo access?). Will try to continue without it."
    return 1
  fi
  # Activate brew in the current shell
  if   [[ -x /opt/homebrew/bin/brew ]]; then eval "$(/opt/homebrew/bin/brew shellenv)"
  elif [[ -x /usr/local/bin/brew    ]]; then eval "$(/usr/local/bin/brew shellenv)"
  fi
  ok "Homebrew installed"
  return 0
}

# macOS: install Python via Homebrew
install_python_macos() {
  info "Installing Python 3.13 via Homebrew…"
  brew install python@3.13 --quiet
  # Homebrew Python is keg-only; add to PATH explicitly
  local brew_python
  brew_python="$(brew --prefix python@3.13 2>/dev/null)/bin"
  export PATH="$brew_python:$PATH"
  ok "Python 3.13 installed"
}

# Linux: install Python via the detected package manager
install_python_linux() {
  info "Installing Python 3.11+ via ${PKG_MGR}…"
  case "$PKG_MGR" in
    apt)
      run_as_root apt-get update -y -qq
      # Probe available versions: try installing each, stop on first success
      local installed=false
      for v in 3.13 3.12 3.11; do
        if run_as_root apt-get install -y -qq --no-install-recommends \
             "python${v}" "python${v}-venv" 2>/dev/null; then
          installed=true
          ok "Python ${v} installed"
          break
        fi
      done
      if [[ "$installed" == false ]]; then
        # Fall back to deadsnakes PPA (Ubuntu/Debian)
        warn "python3.11–3.13 not in default apt repos — adding deadsnakes PPA…"
        run_as_root apt-get install -y -qq --no-install-recommends \
          software-properties-common 2>/dev/null || true
        if command -v add-apt-repository &>/dev/null; then
          run_as_root add-apt-repository -y ppa:deadsnakes/ppa
          run_as_root apt-get update -y -qq
          run_as_root apt-get install -y -qq --no-install-recommends \
            python3.11 python3.11-venv
          ok "Python 3.11 (deadsnakes) installed"
        else
          die "Cannot add deadsnakes PPA. Please install Python 3.11+ manually: https://www.python.org/downloads/"
        fi
      fi
      ;;
    dnf)
      run_as_root dnf install -y python3.11 python3.11-devel 2>/dev/null || \
        run_as_root dnf install -y python3
      ;;
    pacman)
      run_as_root pacman -Sy --noconfirm python
      ;;
    zypper)
      run_as_root zypper install -y python311 2>/dev/null || \
        run_as_root zypper install -y python3
      ;;
    *)
      die "Cannot auto-install Python: no supported package manager found.\nPlease install Python 3.11+ manually from https://www.python.org/downloads/"
      ;;
  esac
}

# macOS: install Git via Homebrew
install_git_macos() {
  info "Installing Git via Homebrew…"
  brew install git --quiet
  ok "Git installed"
}

# Linux: install Git via the detected package manager
install_git_linux() {
  info "Installing Git via ${PKG_MGR}…"
  case "$PKG_MGR" in
    apt)    run_as_root apt-get install -y -qq --no-install-recommends git ;;
    dnf)    run_as_root dnf install -y git ;;
    pacman) run_as_root pacman -Sy --noconfirm git ;;
    zypper) run_as_root zypper install -y git ;;
    *)      die "Cannot auto-install Git: no supported package manager found." ;;
  esac
  ok "Git installed"
}

# Linux: ensure curl is available (needed for public IP detection)
ensure_curl_linux() {
  command -v curl &>/dev/null && return
  info "Installing curl…"
  case "$PKG_MGR" in
    apt)    run_as_root apt-get install -y -qq --no-install-recommends curl ;;
    dnf)    run_as_root dnf install -y --quiet curl ;;
    pacman) run_as_root pacman -Sy --noconfirm curl ;;
    zypper) run_as_root zypper install -y curl ;;
    *)      warn "curl not found; public IP detection skipped"; return ;;
  esac
  ok "curl installed"
}

# ── Ollama helpers ───────────────────────────────────────────────────────────

ensure_ollama() {
  if command -v ollama &>/dev/null; then
    ok "Ollama $(ollama --version 2>/dev/null | awk '{print $NF}' || echo '(installed)')"
    return 0
  fi
  info "Ollama not found — installing…"
  if [[ "$OS_NAME" == "macOS" ]]; then
    if command -v brew &>/dev/null; then
      brew install ollama --quiet
    else
      warn "Homebrew not available — cannot auto-install Ollama on macOS."
      warn "Install manually from https://ollama.com/download"
      return 1
    fi
  else
    # Official Ollama install script (sets up systemd service automatically)
    if command -v curl &>/dev/null; then
      curl -fsSL https://ollama.com/install.sh | sh
    else
      warn "curl not available — cannot auto-install Ollama."
      warn "Install manually from https://ollama.com/download"
      return 1
    fi
  fi
  if command -v ollama &>/dev/null; then
    ok "Ollama installed"
    return 0
  fi
  warn "Ollama installation may have failed — 'ollama' not found in PATH"
  return 1
}

# Start Ollama service and ensure it stays running across reboots
start_ollama_service() {
  if [[ "$OS_NAME" == "macOS" ]]; then
    if command -v brew &>/dev/null; then
      # brew services handles launchd plist creation + auto-start
      if brew services list 2>/dev/null | grep -q "ollama.*started"; then
        ok "Ollama service already running"
      else
        info "Starting Ollama service (auto-start on boot)…"
        brew services start ollama 2>/dev/null || true
        sleep 2
        ok "Ollama service started via brew services"
      fi
    else
      # Fallback: just run ollama serve in background
      if pgrep -f "ollama serve" &>/dev/null; then
        ok "Ollama already running"
      else
        info "Starting Ollama in background…"
        nohup ollama serve >> "$INSTALL_DIR/ollama.log" 2>&1 &
        sleep 2
        ok "Ollama started (nohup — will not auto-start on reboot)"
        warn "For auto-start, install Ollama via Homebrew: brew install ollama"
      fi
    fi
  else
    # Linux: Ollama's install script already creates a systemd service
    if command -v systemctl &>/dev/null; then
      if systemctl is-active --quiet ollama 2>/dev/null; then
        ok "Ollama service already running"
      else
        info "Starting Ollama service…"
        run_as_root systemctl enable --now ollama 2>/dev/null || true
        sleep 2
        ok "Ollama service started (auto-start on boot)"
      fi
    else
      if pgrep -f "ollama serve" &>/dev/null; then
        ok "Ollama already running"
      else
        nohup ollama serve >> "$INSTALL_DIR/ollama.log" 2>&1 &
        sleep 2
        ok "Ollama started (nohup)"
      fi
    fi
  fi
}

# Pull an Ollama model if not already present
ensure_ollama_model() {
  local model="$1"
  [[ -z "$model" ]] && return 0
  if ollama list 2>/dev/null | grep -q "$model"; then
    ok "Ollama model '$model' already available"
    return 0
  fi
  info "Pulling Ollama model '$model' (this may take a few minutes)…"
  if ollama pull "$model" 2>&1 | while IFS= read -r line; do
    [[ -n "$line" ]] && detail "$line"
  done; then
    ok "Model '$model' ready"
    return 0
  else
    warn "Failed to pull model '$model'. You can retry manually: ollama pull $model"
    return 1
  fi
}

# ── Helper: find Python 3.11+ in PATH and common install locations ────────────
# Sets the global PYTHON variable; returns 0 on success, 1 if not found.
# Also warns when an older Python is present so users get a clear diagnosis.
find_python() {
  local cmd candidate prefix suffix major minor found_old_ver=""

  # 0. Respect explicit override first (accept command name or absolute path)
  if [[ -n "$PYTHON_OVERRIDE" ]]; then
    if command -v "$PYTHON_OVERRIDE" &>/dev/null || [[ -x "$PYTHON_OVERRIDE" ]]; then
      cmd="$PYTHON_OVERRIDE"
      major=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || major=""
      minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || minor=""
      if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
        PYTHON="$cmd"
        ok "Using HUSHCLAW_PYTHON override: $cmd ($("$cmd" --version 2>&1 | awk '{print $2}'))"
        return 0
      fi
      warn "HUSHCLAW_PYTHON points to Python ${major:-?}.${minor:-?}; need 3.11+."
    else
      warn "HUSHCLAW_PYTHON is set but not executable: $PYTHON_OVERRIDE"
    fi
  fi

  # 1. Scan PATH-visible commands (versioned first, then generic)
  for cmd in python3.13 python3.12 python3.11 python3 python; do
    command -v "$cmd" &>/dev/null || continue
    major=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
    minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
    if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
      PYTHON="$cmd"
      ok "Found Python $("$cmd" --version 2>&1 | awk '{print $2}') at $(command -v "$cmd")"
      return 0
    elif [[ -z "$found_old_ver" && "$major" -ge 3 ]]; then
      # Remember the first old-Python version for a better error message later
      found_old_ver="$("$cmd" --version 2>&1 | awk '{print $2}')"
    fi
  done

  # 2. On macOS, also probe common absolute paths that may not be in PATH
  #    (python.org installer, Homebrew keg-only, pyenv shims, nix, etc.)
  if [[ "$OS_NAME" == "macOS" ]]; then
    for prefix in \
        /opt/homebrew/bin \
        /opt/homebrew/opt/python@3.13/bin \
        /opt/homebrew/opt/python@3.12/bin \
        /opt/homebrew/opt/python@3.11/bin \
        /usr/local/bin \
        /usr/local/opt/python@3.13/bin \
        /usr/local/opt/python@3.12/bin \
        /usr/local/opt/python@3.11/bin \
        /Library/Frameworks/Python.framework/Versions/Current/bin \
        /Library/Frameworks/Python.framework/Versions/3.13/bin \
        /Library/Frameworks/Python.framework/Versions/3.12/bin \
        /Library/Frameworks/Python.framework/Versions/3.11/bin \
        "$HOME/.pyenv/shims" \
        /nix/var/nix/profiles/default/bin; do
      [[ -d "$prefix" ]] || continue
      for suffix in python3.13 python3.12 python3.11 python3 python; do
        candidate="$prefix/$suffix"
        [[ -x "$candidate" ]] || continue
        major=$("$candidate" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
        minor=$("$candidate" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
          # Sanity-check stdlib integrity — a broken Homebrew Python can have
          # version info intact while native extensions (e.g. pyexpat) are
          # linked against a newer libexpat than the system provides.
          if ! "$candidate" -c 'import xml.parsers.expat, ssl, hashlib' 2>/dev/null; then
            warn "Python $("$candidate" --version 2>&1 | awk '{print $2}') at $candidate has broken stdlib extensions — skipping."
            warn "Fix: brew reinstall python@${major}.${minor}  OR  install from https://www.python.org/downloads/"
            continue
          fi
          PYTHON="$candidate"
          ok "Found Python $("$candidate" --version 2>&1 | awk '{print $2}') at $candidate"
          return 0
        fi
      done
    done
  fi

  # Nothing ≥ 3.11 found — emit a helpful diagnostic before returning failure
  if [[ -n "$found_old_ver" ]]; then
    warn "Python ${found_old_ver} detected but HushClaw requires Python 3.11+."
    warn "Please install Python 3.11 or newer from https://www.python.org/downloads/"
    warn "then re-run this script."
  fi
  return 1
}

# ── Step 1: Python ────────────────────────────────────────────────────────────
# On macOS we check for an existing Python first; Homebrew is only installed
# when Python is actually missing.  This lets users without sudo admin rights
# complete the install if Python is already present (e.g. from python.org).
section "Checking Python"

PYTHON=""
find_python || true   # sets PYTHON if found; 'true' prevents -e from firing

if [[ -z "$PYTHON" ]]; then
  warn "Python 3.11+ not found — installing automatically…"
  if [[ "$OS_NAME" == "macOS" ]]; then
    # Need Homebrew to install Python on macOS
    section "Checking Homebrew"
    if ensure_homebrew; then
      install_python_macos
      # Re-scan after installation (Homebrew may have added new bin paths)
      find_python || true
      hash -r
    else
      # Homebrew may be unavailable (no sudo). Re-scan once more in case Python exists
      # but wasn't on PATH when the shell started.
      find_python || true
    fi
    [[ -n "$PYTHON" ]] || die "Python 3.11+ not found and could not be installed automatically.\nPlease install it from https://www.python.org/downloads/ then re-run this script."
  else
    install_python_linux
    # Re-scan after installation — also probe /usr/bin directly (apt installs
    # may not update the current shell's hash table immediately)
    find_python || true
    if [[ -z "$PYTHON" ]]; then
      for cmd in /usr/bin/python3.13 /usr/bin/python3.12 /usr/bin/python3.11 \
                 /usr/bin/python3 /usr/bin/python; do
        [[ -x "$cmd" ]] || continue
        major=$("$cmd" -c 'import sys; print(sys.version_info.major)' 2>/dev/null) || continue
        minor=$("$cmd" -c 'import sys; print(sys.version_info.minor)' 2>/dev/null) || continue
        if [[ "$major" -ge 3 && "$minor" -ge 11 ]]; then
          PYTHON="$cmd"
          ok "Using Python $("$cmd" --version 2>&1 | awk '{print $2}') at $cmd"
          break
        fi
      done
    fi
    [[ -n "$PYTHON" ]] || die "Python 3.11+ installation failed. Please install it manually from https://www.python.org/downloads/"
  fi
fi

# ── Step 2: Git ───────────────────────────────────────────────────────────────
section "Checking Git"

if command -v git &>/dev/null; then
  ok "Git $(git --version | awk '{print $3}')"
else
  warn "Git not found — installing automatically…"
  if [[ "$OS_NAME" == "macOS" ]]; then
    install_git_macos
  else
    install_git_linux
  fi
  command -v git &>/dev/null || die "Git installation failed. Please install it manually."
  ok "Git $(git --version | awk '{print $3}')"
fi

# ── Process Check ─────────────────────────────────────────────────────────────
section "Process Check"
mkdir -p "$INSTALL_DIR"
RUNNING_PID=$(find_running_pid)
if [[ -n "$RUNNING_PID" ]]; then
  if [[ "$MODE" == "start" ]]; then
    warn "HushClaw is already running (PID $RUNNING_PID)."
    ok "Server is up — nothing to do."
    exit 0
  else
    info "Stopping running server (PID $RUNNING_PID) before ${MODE}…"
    stop_server "$RUNNING_PID"
  fi
else
  ok "No running HushClaw instance detected"
fi

# ── Install / Update ──────────────────────────────────────────────────────────
if [[ "$MODE" == "start" ]]; then
  [[ -d "$INSTALL_DIR" ]] || die "HushClaw not found at $INSTALL_DIR. Run without --start-only to install first."
else
  section "Installing HushClaw → $INSTALL_DIR"

  mkdir -p "$INSTALL_DIR"
  LAST_BACKUP_PATH=""

  if [[ -d "$INSTALL_DIR/repo/.git" ]]; then
    if [[ "$MODE" == "update" ]] || [[ "$MODE" == "install" ]]; then
      if repo_is_dirty; then
        warn "Installation repository has local modifications"
        list_dirty_files | while IFS= read -r line; do
          [[ -n "$line" ]] && detail_warn "$line"
        done
        if [[ "$BACKUP_BEFORE_OVERWRITE" == true ]] || [[ "$OVERWRITE_INSTALL" == true ]]; then
          LAST_BACKUP_PATH="$(backup_root_dir)"
          info "Backing up user data before overwriting code…"
          backup_user_data "$LAST_BACKUP_PATH"
          info "Saving install repo overlay snapshot…"
          backup_repo_overlay "$LAST_BACKUP_PATH/repo-overlay"
          ok "Backup completed: $LAST_BACKUP_PATH"
        else
          info "Proceeding to overwrite installation code. Runtime data lives outside the install repository."
          info "Use --backup-before-overwrite to save a pre-overwrite snapshot."
        fi
      fi
      info "Updating repository…"
      (cd "$INSTALL_DIR/repo" && git fetch --quiet origin)
      (cd "$INSTALL_DIR/repo" && git reset --hard origin/main --quiet 2>/dev/null \
        || git reset --hard origin/master --quiet)
      ok "Repository updated"
    fi
  else
    info "Cloning repository…"
    git clone --depth=1 "$REPO_URL" "$INSTALL_DIR/repo" --quiet
    ok "Repository cloned"
  fi

  # ── Self-update: restart with the freshly-pulled install.sh if we're not ──
  # already running from it.  Handles the common case where the user runs a
  # cached copy downloaded weeks ago via  curl … | bash  or  bash install.sh.
  _REPO_INSTALLER="$INSTALL_DIR/repo/install.sh"
  _SELF_REAL="$(realpath "$0" 2>/dev/null || echo "$0")"
  _REPO_REAL="$(realpath "$_REPO_INSTALLER" 2>/dev/null || echo "$_REPO_INSTALLER")"
  if [[ -f "$_REPO_INSTALLER" && "$_SELF_REAL" != "$_REPO_REAL" ]]; then
    info "Restarting with updated install.sh from repository…"
    exec bash "$_REPO_INSTALLER" "${ORIGINAL_ARGS[@]}"
  fi


  # Ubuntu/Debian ship python3.X without venv support by default; the
  # -venv package must be installed separately even for the system Python.
  if [[ "$OS_NAME" == "Linux" && "$PKG_MGR" == "apt" ]]; then
    if ! "$PYTHON" -c "import ensurepip" &>/dev/null 2>&1; then
      PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
      info "Installing python${PY_VER}-venv (required for venv support on Debian/Ubuntu)…"
      run_as_root apt-get install -y -qq --no-install-recommends "python${PY_VER}-venv"
      ok "python${PY_VER}-venv installed"
    fi
  fi

  # ── Virtual environment ────────────────────────────────────────────────────
  # Recreate venv if it doesn't exist or is broken (e.g. pip missing after a failed install)
  if [[ ! -x "$INSTALL_DIR/venv/bin/pip" ]]; then
    [[ -d "$INSTALL_DIR/venv" ]] && { warn "Broken venv detected — recreating…"; rm -rf "$INSTALL_DIR/venv"; }
  fi

  if [[ ! -d "$INSTALL_DIR/venv" ]]; then
    info "Creating virtual environment…"
    if "$PYTHON" -m venv "$INSTALL_DIR/venv" 2>/tmp/_hushclaw_venv_err; then
      ok "Virtual environment created"
    else
      warn "Standard venv failed: $(cat /tmp/_hushclaw_venv_err 2>/dev/null | head -1)"
      # Clean up any partial directory before retrying
      rm -rf "$INSTALL_DIR/venv"
      info "Retrying without pip (will bootstrap separately)…"
      "$PYTHON" -m venv --without-pip "$INSTALL_DIR/venv"
      # Guard: venv Python inherits the host Python's native extensions.
      # If pyexpat / ssl are broken (e.g. Homebrew Python linked against a
      # newer libexpat than the system provides), get-pip.py will crash.
      if ! "$INSTALL_DIR/venv/bin/python" -c 'import xml.parsers.expat, ssl' 2>/dev/null; then
        _PY_VER=$("$PYTHON" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")')
        rm -rf "$INSTALL_DIR/venv"
        die "Python ${_PY_VER} has broken stdlib extensions (pyexpat/ssl link error).\n\
Fix:  brew reinstall python@${_PY_VER}\n\
Then re-run this installer."
      fi
      # Bootstrap pip via ensurepip or get-pip.py
      if "$INSTALL_DIR/venv/bin/python" -m ensurepip --upgrade 2>/dev/null; then
        ok "pip bootstrapped via ensurepip"
      elif command -v curl &>/dev/null; then
        get_pip_tmp="$(mktemp "${TMPDIR:-/tmp}/hushclaw-get-pip.XXXXXX.py")"
        if curl -fsSL https://bootstrap.pypa.io/get-pip.py -o "$get_pip_tmp"; then
          if "$INSTALL_DIR/venv/bin/python" "$get_pip_tmp" --disable-pip-version-check --quiet; then
            ok "pip bootstrapped via get-pip.py"
          else
            rm -f "$get_pip_tmp"
            die "get-pip.py failed to bootstrap pip"
          fi
          rm -f "$get_pip_tmp"
        else
          rm -f "$get_pip_tmp"
          die "Failed to download get-pip.py"
        fi
      else
        die "Cannot bootstrap pip. Try: apt-get install python3-pip"
      fi
      if [[ ! -x "$INSTALL_DIR/venv/bin/pip" ]]; then
        die "pip bootstrap completed but $INSTALL_DIR/venv/bin/pip is still missing"
      fi
    fi
    rm -f /tmp/_hushclaw_venv_err
  fi

  info "Installing/upgrading packages…"
  "$INSTALL_DIR/venv/bin/pip" install --upgrade pip --quiet
  "$INSTALL_DIR/venv/bin/pip" install -e "$INSTALL_DIR/repo[server,calendar,encryption]" --quiet
  ok "HushClaw installed"
  write_install_state "${LAST_BACKUP_PATH:-}" "ok"

  # ── Canonical DB schema migration ─────────────────────────────────────────
  # The application owns the migration ledger, backup, integrity checks, and
  # permissions. Keep the installer out of schema details so installed builds
  # and source checkouts cannot drift onto different migration paths.
  if [[ "$OS_NAME" == "macOS" ]]; then
    _DB_DIR="$HOME/Library/Application Support/hushclaw"
  else
    _DB_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw"
  fi
  _DB="$_DB_DIR/memory.db"
  section "Migrating Local Database"
  "$INSTALL_DIR/venv/bin/python" -c \
    'from hushclaw.config.loader import load_config; from hushclaw.memory.db import open_db; c = open_db(load_config().memory.data_dir); c.close()' \
    || die "Database migration failed. Existing data was left in place; run: $INSTALL_DIR/venv/bin/hushclaw doctor"
  ok "Database schema and permissions verified"

  # ── AgentOS agent schema migration (one-time, idempotent) ────────────────
  # AgentOS no longer stores business/org fields on agent definitions.  Older
  # installs may have role/team/reports_to/capabilities in hushclaw.toml or
  # dynamic_agents.json; rewrite those to neutral routing_tags before startup.
  section "Migrating AgentOS Agent Schema"
  "$INSTALL_DIR/venv/bin/python" -m hushclaw.config.migrations \
    > >(while IFS= read -r line; do
          render_structured_line "$line"
        done) || true

  # ── Migrate memory-stored skills → SKILL.md files (one-time) ─────────────
  # Older hushclaw stored agent-created skills as _skill-tagged notes in SQLite.
  # This one-time migration exports qualifying skills to disk as SKILL.md files.
  # Quality gate: body ≥ 100 chars; preserves existing files; idempotent.
  if [[ -f "$_DB" ]]; then
    if [[ "$OS_NAME" == "macOS" ]]; then
      _MIGRATE_CFG="$HOME/Library/Application Support/hushclaw/hushclaw.toml"
      _MIGRATE_DEFAULT_SKILL_DIR="$HOME/Library/Application Support/hushclaw/user-skills"
    else
      _MIGRATE_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/hushclaw/hushclaw.toml"
      _MIGRATE_DEFAULT_SKILL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw/user-skills"
    fi
    section "Migrating Memory Skills → Files"
    "$PYTHON" - "$_DB" "$_MIGRATE_CFG" "$_MIGRATE_DEFAULT_SKILL_DIR" \
      > >(while IFS= read -r line; do
            render_structured_line "$line"
          done) <<'MIGRATE_PY' || true
import json, re, sqlite3, sys, time
from pathlib import Path

db_path     = Path(sys.argv[1])
config_file = Path(sys.argv[2])
default_dir = Path(sys.argv[3]).expanduser()

# Resolve target skill dir: only user_skill_dir (never skill_dir — that's
# the bundled dir). Fall back to default data-dir-based path.
target_dir = default_dir
try:
    import tomllib
    data = tomllib.loads(config_file.read_text(encoding="utf-8"))
    tools = data.get("tools", {}) if isinstance(data, dict) else {}
    v = tools.get("user_skill_dir", "")
    if isinstance(v, str) and v.strip():
        target_dir = Path(v.strip()).expanduser()
except Exception:
    pass

# Idempotent: skip if already migrated
marker = target_dir / ".memory-skill-migration.json"
if marker.exists():
    print("info|Already migrated — skipping")
    raise SystemExit(0)

if not db_path.exists():
    print("info|No memory.db — nothing to migrate")
    raise SystemExit(0)

# notes table: note_id (PK), title, path (markdown file on disk), tags
# body content lives in the markdown file, not in the DB column
try:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT note_id, title, path FROM notes WHERE tags LIKE '%_skill%'"
    ).fetchall()
    conn.close()
except Exception as exc:
    print(f"warn|DB read error: {exc} (skipping migration)")
    raise SystemExit(0)

def slugify(name: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", name.lower().strip())
    return s.strip("-")[:64]

def read_body(md_path: str) -> str:
    """Read markdown file, strip YAML front-matter if present."""
    try:
        text = Path(md_path).read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            return parts[2].strip()
    return text

MIN_BODY = 100  # skip stubs shorter than this
migrated, skipped = [], []

for note_id, title, md_path in rows:
    title = (title or "").strip()
    body  = read_body(md_path or "")
    if not title:
        skipped.append({"id": note_id, "reason": "no_title"})
        continue
    if len(body) < MIN_BODY:
        skipped.append({"id": note_id, "title": title, "reason": "body_too_short"})
        continue
    slug = slugify(title)
    if not slug:
        skipped.append({"id": note_id, "title": title, "reason": "bad_slug"})
        continue
    skill_path = target_dir / slug / "SKILL.md"
    if skill_path.exists():
        skipped.append({"id": note_id, "title": title, "reason": "already_exists"})
        continue
    try:
        skill_path.parent.mkdir(parents=True, exist_ok=True)
        safe_title = title.replace('"', "'")
        skill_path.write_text(
            f'---\nname: {safe_title}\ndescription: Migrated from memory\n'
            f'author: user\nversion: "1.0.0"\n---\n\n{body}\n',
            encoding="utf-8",
        )
        migrated.append({"id": note_id, "title": title, "slug": slug})
        print(f"ok|{title} → {slug}/SKILL.md")
    except Exception as exc:
        skipped.append({"id": note_id, "title": title, "reason": f"write_error: {exc}"})

target_dir.mkdir(parents=True, exist_ok=True)
marker.write_text(
    json.dumps(
        {"migrated_at": int(time.time()), "migrated": migrated, "skipped": skipped},
        indent=2, ensure_ascii=False,
    ),
    encoding="utf-8",
)
if migrated:
    print(f"summary|{len(migrated)} skill(s) migrated, {len(skipped)} skipped")
else:
    print(f"summary|No qualifying skills found ({len(skipped)} checked)")
MIGRATE_PY
  fi

  # ── Encrypt local SQLite state after all plaintext legacy readers finish ──
  section "Encrypting Local Database"
  "$INSTALL_DIR/venv/bin/hushclaw" database encrypt \
    || die "Database encryption failed. The verified pre-encryption database was left recoverable; run: $INSTALL_DIR/venv/bin/hushclaw database status"
  ok "Database and migration snapshots are encrypted"

  # ── Create helper launcher scripts ────────────────────────────────────────
  LAUNCHER="$INSTALL_DIR/hushclaw-start.sh"
  cat > "$LAUNCHER" <<LAUNCHER_EOF
#!/usr/bin/env bash
# HushClaw quick-start launcher
export HUSHCLAW_PORT="\${HUSHCLAW_PORT:-$PORT}"
export HUSHCLAW_HOST="\${HUSHCLAW_HOST:-$BIND}"
exec "$INSTALL_DIR/venv/bin/hushclaw" serve --host "\$HUSHCLAW_HOST" --port "\$HUSHCLAW_PORT" --distro "$DISTRO" "\$@"
LAUNCHER_EOF
  chmod +x "$LAUNCHER"

  # ── Sync bundled skill packages → skill_dir ───────────────────────────────
  # skill-packages/ in the repo are not loaded until copied into the
  # runtime skill_dir (mirrors the config loader's default path logic).
  REPO_SKILLS="$INSTALL_DIR/repo/skill-packages"
  if [[ "$OS_NAME" == "macOS" ]]; then
    DEFAULT_SKILL_DIR="$HOME/Library/Application Support/hushclaw/skills"
    CONFIG_FILE="$HOME/Library/Application Support/hushclaw/hushclaw.toml"
  else
    DEFAULT_SKILL_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hushclaw/skills"
    CONFIG_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/hushclaw/hushclaw.toml"
  fi
  SKILL_DIR="$DEFAULT_SKILL_DIR"
  CONFIG_SKILL_DIR="$("$PYTHON" - "$CONFIG_FILE" <<'PY'
import sys
from pathlib import Path
try:
    import tomllib
except Exception:
    print("")
    raise SystemExit(0)

cfg = Path(sys.argv[1]).expanduser()
if not cfg.exists():
    print("")
    raise SystemExit(0)
try:
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
except Exception:
    print("")
    raise SystemExit(0)
tools = data.get("tools", {}) if isinstance(data, dict) else {}
skill_dir = tools.get("skill_dir", "") if isinstance(tools, dict) else ""
if isinstance(skill_dir, str) and skill_dir.strip():
    print(str(Path(skill_dir.strip()).expanduser()))
else:
    print("")
PY
)"
  if [[ -n "$CONFIG_SKILL_DIR" ]]; then
    SKILL_DIR="$CONFIG_SKILL_DIR"
    info "Bundled skill target dir (configured): $SKILL_DIR"
  else
    info "Bundled skill target dir (default): $SKILL_DIR"
  fi

  if [[ -d "$REPO_SKILLS" ]]; then
    section "Syncing Bundled Skills"
    mkdir -p "$SKILL_DIR"
    info "Bundled skill policy: ${SKILL_POLICY}"
    if "$PYTHON" - "$REPO_SKILLS" "$SKILL_DIR" "$SKILL_POLICY" \
      > >(while IFS= read -r line; do
            render_skill_sync_line "$line"
          done) <<'PY'
import hashlib
import json
import os
import shutil
import sys
import time
from pathlib import Path

repo_skills = Path(sys.argv[1]).expanduser()
skill_dir = Path(sys.argv[2]).expanduser()
policy = (sys.argv[3] or "preserve_skip").strip().lower()
if policy not in {"preserve_skip", "force_official"}:
    policy = "preserve_skip"

state_path = skill_dir / ".bundled-skill-state.json"
backup_root = skill_dir / ".bundled-skill-backups"
schema_version = 1

def load_state() -> dict:
    if not state_path.exists():
        return {"schema_version": schema_version, "updated_at": int(time.time()), "skills": {}}
    try:
        data = json.loads(state_path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError("state root must be object")
        data.setdefault("schema_version", schema_version)
        data.setdefault("skills", {})
        if not isinstance(data["skills"], dict):
            data["skills"] = {}
        return data
    except Exception:
        return {"schema_version": schema_version, "updated_at": int(time.time()), "skills": {}}

def parse_version(skill_md: Path) -> str:
    if not skill_md.exists():
        return ""
    try:
        for line in skill_md.read_text(encoding="utf-8", errors="ignore").splitlines():
            s = line.strip()
            if s.startswith("version:"):
                return s.split(":", 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return ""

def hash_skill_dir(root: Path) -> str:
    files: list[Path] = []
    for rel in ("SKILL.md", "requirements.txt", "README.md"):
        p = root / rel
        if p.is_file():
            files.append(p)
    tools_dir = root / "tools"
    if tools_dir.is_dir():
        files.extend(sorted(p for p in tools_dir.rglob("*.py") if p.is_file()))
    files = sorted(files, key=lambda p: str(p.relative_to(root)).replace("\\", "/"))

    h = hashlib.sha256()
    for p in files:
        rel = str(p.relative_to(root)).replace("\\", "/")
        h.update(rel.encode("utf-8"))
        h.update(b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return f"sha256:{h.hexdigest()}"

def replace_dir(src: Path, dst: Path) -> None:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)

def unique_backup_dir(name: str) -> Path:
    backup_root.mkdir(parents=True, exist_ok=True)
    ts = time.strftime("%Y%m%d-%H%M%S")
    base = backup_root / f"{name}-{ts}"
    if not base.exists():
        return base
    i = 1
    while True:
        cand = backup_root / f"{name}-{ts}-{i}"
        if not cand.exists():
            return cand
        i += 1

state = load_state()
skills_state: dict = state.get("skills", {})
counts = {
    "installed": 0,
    "updated": 0,
    "forced_updated": 0,
    "skipped_dirty": 0,
    "skipped_error": 0,
}

for pkg in sorted(repo_skills.iterdir(), key=lambda p: p.name.lower()):
    if not pkg.is_dir():
        continue
    name = pkg.name
    local_dir = skill_dir / name
    official_hash = hash_skill_dir(pkg)
    official_version = parse_version(pkg / "SKILL.md")
    prev = skills_state.get(name, {}) if isinstance(skills_state.get(name, {}), dict) else {}
    prev_last = str(prev.get("last_deployed_hash", "") or "")

    try:
        if not local_dir.exists():
            replace_dir(pkg, local_dir)
            counts["installed"] += 1
            print(f"[installed] /{name} version={official_version or '-'}")
            skills_state[name] = {
                "source": "bundled",
                "policy": policy,
                "official_version": official_version,
                "official_hash": official_hash,
                "last_deployed_hash": official_hash,
                "local_hash": official_hash,
                "dirty": False,
            }
            continue

        local_hash = hash_skill_dir(local_dir)
        if not prev_last:
            # No deployment record: treat as clean only when content already
            # matches official (e.g. first run after a new bundled skill was
            # committed, or a reinstall of the same version).
            dirty = (local_hash != official_hash)
        else:
            dirty = (local_hash != prev_last)

        if dirty and policy != "force_official":
            counts["skipped_dirty"] += 1
            reason = "no_state_modified" if not prev_last else "local_modified"
            print(f"[skipped_dirty] /{name} reason={reason}")
            skills_state[name] = {
                "source": "bundled",
                "policy": policy,
                "official_version": official_version,
                "official_hash": official_hash,
                "last_deployed_hash": prev_last,
                "local_hash": local_hash,
                "dirty": True,
            }
            continue

        if dirty and policy == "force_official":
            backup_dir = unique_backup_dir(name)
            shutil.copytree(local_dir, backup_dir)
            replace_dir(pkg, local_dir)
            counts["forced_updated"] += 1
            print(f"[forced_updated] /{name} backup={backup_dir}")
        else:
            replace_dir(pkg, local_dir)
            counts["updated"] += 1
            print(f"[updated] /{name} version={official_version or '-'}")

        skills_state[name] = {
            "source": "bundled",
            "policy": policy,
            "official_version": official_version,
            "official_hash": official_hash,
            "last_deployed_hash": official_hash,
            "local_hash": official_hash,
            "dirty": False,
        }
    except Exception as exc:
        counts["skipped_error"] += 1
        print(f"[skipped_error] /{name} error={exc}")

state["schema_version"] = schema_version
state["updated_at"] = int(time.time())
state["skills"] = skills_state
state_path.write_text(json.dumps(state, indent=2, ensure_ascii=False), encoding="utf-8")
print(
    "summary "
    + " ".join(f"{k}={v}" for k, v in counts.items())
)
PY
    then
      ok "Bundled skill sync completed → $SKILL_DIR"
    else
      warn "Bundled skill sync encountered an unexpected failure"
    fi
  fi

fi

# ── Ollama (optional — only when embed_provider = ollama in config) ───────────
# Read embed_provider and embed_model from the user's hushclaw.toml.
# If embed_provider is "ollama", install Ollama, start its service, and pull
# the configured model.  Skipped entirely when embed_provider is anything else.
if [[ "$MODE" != "stop" ]]; then
  if [[ "$OS_NAME" == "macOS" ]]; then
    _HUSHCLAW_CFG="$HOME/Library/Application Support/hushclaw/hushclaw.toml"
  else
    _HUSHCLAW_CFG="${XDG_CONFIG_HOME:-$HOME/.config}/hushclaw/hushclaw.toml"
  fi
  _EMBED_PROVIDER="$("$PYTHON" - "$_HUSHCLAW_CFG" <<'PY'
import sys
from pathlib import Path
try:
    import tomllib
except ImportError:
    print("local"); raise SystemExit(0)
cfg = Path(sys.argv[1]).expanduser()
if not cfg.exists():
    print("local"); raise SystemExit(0)
try:
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
except Exception:
    print("local"); raise SystemExit(0)
mem = data.get("memory", {}) if isinstance(data, dict) else {}
print(str(mem.get("embed_provider", "local")).strip())
PY
  2>/dev/null || echo "local")"

  _EMBED_MODEL="$("$PYTHON" - "$_HUSHCLAW_CFG" <<'PY'
import sys
from pathlib import Path
try:
    import tomllib
except ImportError:
    print(""); raise SystemExit(0)
cfg = Path(sys.argv[1]).expanduser()
if not cfg.exists():
    print(""); raise SystemExit(0)
try:
    data = tomllib.loads(cfg.read_text(encoding="utf-8"))
except Exception:
    print(""); raise SystemExit(0)
mem = data.get("memory", {}) if isinstance(data, dict) else {}
print(str(mem.get("embed_model", "")).strip())
PY
  2>/dev/null || echo "")"

  if [[ "$_EMBED_PROVIDER" == "ollama" ]]; then
    section "Ollama (embed_provider = ollama)"
    if ensure_ollama; then
      start_ollama_service
      if [[ -n "$_EMBED_MODEL" ]]; then
        ensure_ollama_model "$_EMBED_MODEL"
      else
        ensure_ollama_model "nomic-embed-text"
      fi
    fi
  fi
fi

# ── Add hushclaw to PATH ────────────────────────────────────────────────
if [[ "$MODE" != "start" ]]; then
  section "Setting Up PATH"

  LOCAL_BIN="$HOME/.local/bin"
  mkdir -p "$LOCAL_BIN"

  # 1. Create symlink (always, overwrite old)
  if ln -sf "$INSTALL_DIR/venv/bin/hushclaw" "$LOCAL_BIN/hushclaw" 2>/dev/null; then
    ok "'hushclaw' command → $LOCAL_BIN/hushclaw"
  else
    warn "Could not create symlink in $LOCAL_BIN"
  fi

  # 2. Check if ~/.local/bin is already in PATH
  PATH_ENTRY='export PATH="$HOME/.local/bin:$PATH"'
  NEEDS_EXPORT=false
  if ! echo "$PATH" | tr ':' '\n' | grep -qxF "$LOCAL_BIN"; then
    NEEDS_EXPORT=true
    export PATH="$LOCAL_BIN:$PATH"
  fi

  # 3. Write to shell rc file (idempotent)
  add_to_shell_rc() {
    local rc="$1"
    if [[ -f "$rc" ]]; then
      if grep -qF '.local/bin' "$rc"; then
        ok "PATH already configured in $rc"
      else
        echo "" >> "$rc"
        echo '# HushClaw' >> "$rc"
        echo "$PATH_ENTRY" >> "$rc"
        ok "Added ~/.local/bin to PATH in $rc"
      fi
    fi
  }

  if [[ "$NEEDS_EXPORT" == true ]]; then
    case "${SHELL:-}" in
      */zsh)  add_to_shell_rc "$HOME/.zshrc" ;;
      */bash) add_to_shell_rc "$HOME/.bashrc"; add_to_shell_rc "$HOME/.bash_profile" ;;
      *)
        add_to_shell_rc "$HOME/.zshrc"
        add_to_shell_rc "$HOME/.bashrc"
        ;;
    esac
    warn "PATH updated for future terminals. For this terminal, run: export PATH=\"\$HOME/.local/bin:\$PATH\""
  else
    ok "'hushclaw' is already available in PATH"
  fi

  if command -v hushclaw &>/dev/null; then
    ok "'hushclaw' resolves to $(command -v hushclaw)"
  else
    warn "'hushclaw' is not visible in this shell yet. Use: $INSTALL_DIR/venv/bin/hushclaw"
  fi
fi

# ── Firewall: open port ───────────────────────────────────────────────────────
if [[ "$OS_NAME" == "Linux" ]]; then
  section "Firewall"

  open_firewall_port() {
    local port="$1"

    if command -v ufw &>/dev/null; then
      local ufw_status
      # ufw status requires root; use run_as_root so sudo prompts are visible
      # and the script does not hang silently waiting for a password.
      ufw_status=$(run_as_root ufw status 2>/dev/null)
      if echo "$ufw_status" | grep -q "Status: active"; then
        if echo "$ufw_status" | grep -qw "$port"; then
          ok "ufw: port $port already open"
        else
          info "Opening port $port in ufw…"
          run_as_root ufw allow "$port/tcp" >/dev/null
          ok "ufw: port $port opened"
        fi
        return
      fi
    fi

    if command -v firewall-cmd &>/dev/null; then
      # firewall-cmd also requires root for state inspection
      if run_as_root firewall-cmd --state 2>/dev/null | grep -q "running"; then
        if run_as_root firewall-cmd --list-ports 2>/dev/null | grep -qw "$port/tcp"; then
          ok "firewalld: port $port already open"
        else
          info "Opening port $port in firewalld…"
          run_as_root firewall-cmd --permanent --add-port="$port/tcp" >/dev/null
          run_as_root firewall-cmd --reload >/dev/null
          ok "firewalld: port $port opened"
        fi
        return
      fi
    fi

    if command -v iptables &>/dev/null; then
      # iptables -C requires root; use run_as_root for consistency
      if run_as_root iptables -C INPUT -p tcp --dport "$port" -j ACCEPT 2>/dev/null; then
        ok "iptables: port $port already open"
      else
        info "Opening port $port in iptables…"
        run_as_root iptables -A INPUT -p tcp --dport "$port" -j ACCEPT
        ok "iptables: port $port opened"
        warn "iptables rules are not persisted across reboots."
        warn "Install iptables-persistent to save rules permanently:"
        warn "  apt-get install iptables-persistent"
      fi
      return
    fi

    warn "No active firewall detected — skipping port configuration."
    warn "If using a cloud provider (Aliyun / AWS / GCP), open port $port"
    warn "in the security group / firewall rules of your instance."
  }

  open_firewall_port "$PORT"
fi

# ── Network info ──────────────────────────────────────────────────────────────
section "Network Addresses"

# Ensure curl is available for public IP fetch (Linux)
if [[ "$OS_NAME" == "Linux" ]]; then
  ensure_curl_linux
fi

# Local LAN IP
LOCAL_IP=""
if [[ "$OS_NAME" == "macOS" ]]; then
  for iface in en0 en1 en2 utun0; do
    ip=$(ipconfig getifaddr "$iface" 2>/dev/null || true)
    if [[ -n "$ip" && "$ip" != "127."* ]]; then
      LOCAL_IP="$ip"; break
    fi
  done
else
  # Try multiple methods in order of reliability
  LOCAL_IP=$(
    ip -4 route get 1.1.1.1 2>/dev/null \
      | awk '/src/{for(i=1;i<=NF;i++) if($i=="src"){print $(i+1); exit}}' \
    || ip -4 addr show scope global 2>/dev/null \
      | awk '/inet /{split($2,a,"/"); print a[1]; exit}' \
    || hostname -I 2>/dev/null | awk '{print $1}' \
    || true
  )
fi

# Public IP (best-effort, non-blocking)
PUBLIC_IP=""
if command -v curl &>/dev/null; then
  PUBLIC_IP=$(curl -s --connect-timeout 4 https://api.ipify.org 2>/dev/null || true)
fi

echo ""
WEB_PATH="$(web_path_for_distro "$DISTRO")"
echo -e "  ${BOLD}${GREEN}●  Local (this machine)${NC}"
echo -e "     ${CYAN}http://127.0.0.1:${PORT}${WEB_PATH}${NC}"
if [[ -n "$LOCAL_IP" ]]; then
  echo ""
  echo -e "  ${BOLD}${GREEN}●  LAN (same network)${NC}"
  echo -e "     ${CYAN}http://${LOCAL_IP}:${PORT}${WEB_PATH}${NC}"
fi
if [[ -n "$PUBLIC_IP" ]]; then
  echo ""
  echo -e "  ${BOLD}${YELLOW}●  Internet (public IP — only if port $PORT is open in firewall)${NC}"
  echo -e "     ${CYAN}http://${PUBLIC_IP}:${PORT}${WEB_PATH}${NC}"
fi
echo ""
warn "Tip: On first launch the browser opens the ${BOLD}Settings modal${NC} to configure your API key."
warn "     Use the ${BOLD}⚙ Settings${NC} button at any time to adjust Model, Channels, System, or Memory settings."
echo ""

# ── Background launch helpers ─────────────────────────────────────────────────

start_with_nohup() {
  mkdir -p "$INSTALL_DIR"
  nohup "$INSTALL_DIR/venv/bin/hushclaw" serve \
    --host "$BIND" --port "$PORT" --distro "$DISTRO" \
    >> "$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"
  sleep 1
  if ! kill -0 "$pid" 2>/dev/null; then
    warn "Server process exited during startup. Last log lines:"
    tail -40 "$LOG_FILE" 2>/dev/null | sed 's/^/  /' >&2 || true
    die "HushClaw server failed to start."
  fi
  ok "Server started in background (PID $pid)"
  info "Logs: $LOG_FILE"
  info "Stop: bash install.sh --stop"
}

start_with_systemd() {
  local service_name="hushclaw"
  local hushclaw_bin="$INSTALL_DIR/venv/bin/hushclaw"

  if [[ "$(id -u)" -eq 0 ]]; then
    # System-wide service
    local service_file="/etc/systemd/system/${service_name}.service"
    cat > "$service_file" <<SERVICE_EOF
[Unit]
Description=HushClaw AI Agent Server
After=network.target

[Service]
Type=simple
ExecStart=${hushclaw_bin} serve --host ${BIND} --port ${PORT} --distro ${DISTRO}
Restart=always
RestartSec=5
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=multi-user.target
SERVICE_EOF
    systemctl daemon-reload
    systemctl enable --now "$service_name"
    ok "HushClaw registered as system service and started"
    info "Check status: systemctl status $service_name"
    info "View logs:    journalctl -u $service_name -f  (or: tail -f $LOG_FILE)"
    info "Stop:         systemctl stop $service_name"
  else
    # User-level service
    # 1. Enable linger FIRST so the service survives SSH logout / session end
    if command -v loginctl &>/dev/null; then
      loginctl enable-linger "$USER" 2>/dev/null || true
    fi

    local user_systemd_dir="$HOME/.config/systemd/user"
    local service_file="$user_systemd_dir/${service_name}.service"
    mkdir -p "$user_systemd_dir"
    cat > "$service_file" <<SERVICE_EOF
[Unit]
Description=HushClaw AI Agent Server
After=network.target

[Service]
Type=simple
WorkingDirectory=%h
Environment="HOME=%h"
ExecStart=${hushclaw_bin} serve --host ${BIND} --port ${PORT} --distro ${DISTRO}
Restart=always
RestartSec=5
StandardOutput=append:${LOG_FILE}
StandardError=append:${LOG_FILE}

[Install]
WantedBy=default.target
SERVICE_EOF

    # 2. Try systemctl --user; fall back to nohup if D-Bus session is unavailable
    if systemctl --user daemon-reload 2>/dev/null \
       && systemctl --user enable --now "$service_name" 2>/dev/null; then
      ok "HushClaw registered as user service and started"
      info "Check status: systemctl --user status $service_name"
      info "View logs:    journalctl --user -u $service_name -f  (or: tail -f $LOG_FILE)"
      info "Stop:         systemctl --user stop $service_name"
    else
      warn "systemctl --user not available — falling back to nohup"
      rm -f "$service_file"
      start_with_nohup
    fi
  fi
}

start_background() {
  if [[ "$OS_NAME" == "Linux" ]] && command -v systemctl &>/dev/null; then
    start_with_systemd
  elif [[ "$OS_NAME" == "macOS" ]]; then
    start_with_launchd
  else
    start_with_nohup
  fi
}

# macOS: register a LaunchAgent so hushclaw starts at login / after reboot
start_with_launchd() {
  local label="com.hushclaw.server"
  local plist_dir="$HOME/Library/LaunchAgents"
  local plist="$plist_dir/${label}.plist"
  local hushclaw_bin="$INSTALL_DIR/venv/bin/hushclaw"

  mkdir -p "$plist_dir"

  # Write (or overwrite) the plist — idempotent, picks up new port/host settings
  cat > "$plist" <<PLIST_EOF
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN"
  "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key>             <string>${label}</string>
  <key>ProgramArguments</key>
  <array>
    <string>${hushclaw_bin}</string>
    <string>serve</string>
    <string>--host</string>  <string>${BIND}</string>
    <string>--port</string>  <string>${PORT}</string>
    <string>--distro</string> <string>${DISTRO}</string>
  </array>
  <key>RunAtLoad</key>         <true/>
  <key>KeepAlive</key>         <true/>
  <key>StandardOutPath</key>   <string>${LOG_FILE}</string>
  <key>StandardErrorPath</key> <string>${LOG_FILE}</string>
</dict>
</plist>
PLIST_EOF

  # Unload first (safe if not loaded); then load — starts immediately
  launchctl unload "$plist" 2>/dev/null || true
  launchctl load -w "$plist"
  ok "HushClaw registered as LaunchAgent and started"
  info "Check status: launchctl list | grep hushclaw"
  info "View logs:    tail -f $LOG_FILE"
  info "Stop:         launchctl unload $plist"
}

# ── Open browser ──────────────────────────────────────────────────────────────
open_browser() {
  local url="$1"
  # Skip if explicitly disabled or on a headless Linux server
  if [[ -n "$NO_BROWSER" ]]; then return; fi
  if is_headless; then
    warn "Headless server detected — browser auto-open skipped."
    warn "Connect from a client machine using the addresses above."
    return
  fi
  # Wait briefly for the server to bind
  sleep 1.5
  if [[ "$OS_NAME" == "macOS" ]]; then
    open "$url" 2>/dev/null &
  elif command -v xdg-open &>/dev/null; then
    xdg-open "$url" 2>/dev/null &
  elif command -v sensible-browser &>/dev/null; then
    sensible-browser "$url" 2>/dev/null &
  fi
}

# ── Start server ──────────────────────────────────────────────────────────────
section "Starting HushClaw Server"
WEB_PATH="$(web_path_for_distro "$DISTRO")"
LOCAL_WEB_URL="http://127.0.0.1:${PORT}${WEB_PATH}"
info "Personal WebUI on http://${BIND}:${PORT}${WEB_PATH}"
echo ""

if ! "$INSTALL_DIR/venv/bin/hushclaw" doctor >/tmp/hushclaw-doctor.log 2>&1; then
  warn "hushclaw doctor reported issues before startup:"
  sed 's/^/  /' /tmp/hushclaw-doctor.log >&2 || true
  warn "The server may not start until the issues above are fixed."
fi

if [[ "$FOREGROUND" == true ]]; then
  warn "Running in foreground mode (Ctrl-C to stop)"
  open_browser "$LOCAL_WEB_URL" &
  exec "$INSTALL_DIR/venv/bin/hushclaw" serve \
    --host "$BIND" \
    --port "$PORT" \
    --distro "$DISTRO"
else
  start_background
  # Open browser after background server starts
  open_browser "$LOCAL_WEB_URL" &
  ok "Installation complete. HushClaw is running in the background."
fi
