#!/usr/bin/env bash
# ==============================================================================
# OmniCache Official One-Line Installer
# Usage: curl -fsSL https://omnicache.rawwgrid.com/install.sh | bash
# ==============================================================================

set -e

RESET="\033[0m"
BOLD="\033[1m"
DIM="\033[2m"
ORANGE="\033[38;5;208m"
GREEN="\033[38;5;151m"
CYAN="\033[38;5;81m"
RED="\033[38;5;203m"

print_banner() {
  cat << "EOF"

   ____                  _  ____           _          
  / __ \____ ___  ____  (_)/ ____/___ _____/ /_  ___  
 / / / / __ `__ \/ __ \/ // /   / __ `/ ___/ __ \/ _ \ 
/ /_/ / / / / / / / / / // /___/ /_/ / /__/ / / /  __/ 
\____/_/ /_/ /_/_/ /_/_(_)____/\__,_/\___/_/ /_/\___/  
                                                        
EOF
  echo -e " ${BOLD}Sub-Millisecond AI Proxy & Agent Replay Gateway${RESET}"
  echo -e " ${DIM}https://omnicache.rawwgrid.com · v3.1.0${RESET}\n"
}

info() {
  echo -e " ${CYAN}▸${RESET} $1"
}

success() {
  echo -e " ${GREEN}✓${RESET} ${BOLD}$1${RESET}"
}

warn() {
  echo -e " ${ORANGE}⚠${RESET} $1"
}

error() {
  echo -e " ${RED}✗${RESET} ${BOLD}$1${RESET}" >&2
}

print_banner

# 1. Detect Python 3.9+
info "Checking Python runtime environment..."
PYTHON_BIN=""
for cmd in python3 python; do
  if command -v "$cmd" >/dev/null 2>&1; then
    VER=$("$cmd" -c 'import sys; print(f"{sys.version_info.major}.{sys.version_info.minor}")' 2>/dev/null || true)
    MAJOR=$(echo "$VER" | cut -d. -f1)
    MINOR=$(echo "$VER" | cut -d. -f2)
    if [ "$MAJOR" -eq 3 ] && [ "$MINOR" -ge 9 ]; then
      PYTHON_BIN="$cmd"
      break
    fi
  fi
done

if [ -z "$PYTHON_BIN" ]; then
  error "Python 3.9+ is required but was not found."
  echo -e "   Please install Python 3.9 or higher and rerun this installer."
  exit 1
fi

success "Found Python $("$PYTHON_BIN" -c 'import sys; print(sys.version.split()[0])') ($PYTHON_BIN)"

# 2. Install / Upgrade omnicache-proxy
info "Installing/updating omnicache-proxy via pip..."

INSTALL_SUCCESS=false

# Try pipx first if installed
if command -v pipx >/dev/null 2>&1; then
  if pipx install --force omnicache-proxy >/dev/null 2>&1 || pipx upgrade omnicache-proxy >/dev/null 2>&1; then
    INSTALL_SUCCESS=true
    success "Installed via pipx"
  fi
fi

# Try uv pip if available
if [ "$INSTALL_SUCCESS" = false ] && command -v uv >/dev/null 2>&1; then
  if uv pip install --upgrade omnicache-proxy >/dev/null 2>&1; then
    INSTALL_SUCCESS=true
    success "Installed via uv"
  fi
fi

# Standard pip3 install
if [ "$INSTALL_SUCCESS" = false ]; then
  # Try user install first
  if "$PYTHON_BIN" -m pip install --upgrade --user omnicache-proxy >/dev/null 2>&1; then
    INSTALL_SUCCESS=true
    success "Installed via pip (user space)"
  elif "$PYTHON_BIN" -m pip install --upgrade --user --break-system-packages omnicache-proxy >/dev/null 2>&1; then
    INSTALL_SUCCESS=true
    success "Installed via pip (--break-system-packages)"
  elif "$PYTHON_BIN" -m pip install --upgrade omnicache-proxy >/dev/null 2>&1; then
    INSTALL_SUCCESS=true
    success "Installed via standard pip"
  fi
fi

if [ "$INSTALL_SUCCESS" = false ]; then
  error "Failed to install omnicache-proxy automatically."
  echo -e "   Try running manually: ${BOLD}$PYTHON_BIN -m pip install --upgrade omnicache-proxy${RESET}"
  exit 1
fi

# 3. Verify PATH and binary
USER_BASE=$("$PYTHON_BIN" -m site --user-base 2>/dev/null || echo "$HOME/.local")
USER_BIN="$USER_BASE/bin"

if ! command -v omnicache >/dev/null 2>&1; then
  if [ -f "$USER_BIN/omnicache" ]; then
    warn "The directory $USER_BIN is not currently in your PATH."
    echo -e "   To fix this in your current session, run:"
    echo -e "     ${BOLD}export PATH=\"$USER_BIN:\$PATH\"${RESET}\n"
    
    # Check shell profile
    SHELL_PROFILE=""
    if [ -n "$ZSH_VERSION" ] || [ "$(basename "$SHELL" 2>/dev/null)" = "zsh" ]; then
      SHELL_PROFILE="$HOME/.zshrc"
    elif [ -n "$BASH_VERSION" ] || [ "$(basename "$SHELL" 2>/dev/null)" = "bash" ]; then
      SHELL_PROFILE="$HOME/.bashrc"
    elif [ -f "$HOME/.profile" ]; then
      SHELL_PROFILE="$HOME/.profile"
    fi

    if [ -n "$SHELL_PROFILE" ] && [ -w "$SHELL_PROFILE" ]; then
      if ! grep -q "$USER_BIN" "$SHELL_PROFILE" 2>/dev/null; then
        echo "export PATH=\"$USER_BIN:\$PATH\"" >> "$SHELL_PROFILE"
        success "Added $USER_BIN to $SHELL_PROFILE"
      fi
    fi
  fi
fi

echo ""
success "OmniCache installed successfully!"
echo ""
echo -e " ${BOLD}🚀 Quickstart Guide:${RESET}"
echo -e "   ${DIM}1. Run directly with Claude Code:${RESET}"
echo -e "      ${BOLD}omnicache run claude${RESET}"
echo ""
echo -e "   ${DIM}2. Run directly with Cursor / Any LLM app:${RESET}"
echo -e "      ${BOLD}omnicache start &${RESET}"
echo -e "      ${BOLD}export OPENAI_BASE_URL=\"http://127.0.0.1:8000/v1\"${RESET}"
echo ""
echo -e "   ${DIM}3. Live Control Center & Telemetry:${RESET}"
echo -e "      ${BOLD}http://localhost:8000/dashboard${RESET}"
echo ""
echo -e " ${DIM}Docs & Architecture: https://omnicache.rawwgrid.com/docs.html${RESET}"
echo -e " ${DIM}GitHub Repository:   https://github.com/13manmayarai-hash/omnicache-proxy${RESET}\n"
