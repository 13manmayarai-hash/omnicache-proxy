#!/usr/bin/env bash
# ==============================================================================
# OmniCache Proxy - Automated 1-Line Installer for Android (Termux) & Edge
# ==============================================================================
set -e

CYAN='\033[0;36m'
GREEN='\033[0;32m'
AMBER='\033[0;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${CYAN}======================================================${NC}"
echo -e "${CYAN}⚡  OmniCache Proxy - Termux & Edge Environment Init  ${NC}"
echo -e "${CYAN}======================================================${NC}"

# 1. Detect Environment & Install Prerequisites
if [ -d "/data/data/com.termux" ] || [ -n "$TERMUX_VERSION" ]; then
    echo -e "${GREEN}✔ Detected Termux on Android.${NC}"
    echo -e "${AMBER}▶ Updating package repositories and installing core packages...${NC}"
    pkg update -y || apt-get update -y
    pkg install -y python git clang libffi || apt-get install -y python3 git clang libffi-dev
else
    echo -e "${AMBER}ℹ Running in standard Linux/Unix environment.${NC}"
fi

# 2. Upgrade pip
echo -e "${AMBER}▶ Ensuring pip is up to date...${NC}"
python3 -m pip install --upgrade pip setuptools wheel --quiet

# 3. Install OmniCache Proxy
echo -e "${AMBER}▶ Installing omnicache-proxy...${NC}"
if [ -f "pyproject.toml" ] && grep -q "omnicache-proxy" pyproject.toml 2>/dev/null; then
    echo -e "${GREEN}✔ Found local repository. Installing in editable mode...${NC}"
    python3 -m pip install -e . --quiet
else
    echo -e "${GREEN}✔ Installing latest release from PyPI...${NC}"
    python3 -m pip install --upgrade omnicache-proxy --quiet
fi

# 4. Create default storage directory
mkdir -p "$HOME/.omnicache"

# 5. Run diagnostic checks
echo -e "\n${AMBER}▶ Running OmniCache system diagnostics...${NC}"
omnicache doctor

# 6. Add convenience configuration to shell profile if not present
SHELL_RC="$HOME/.bashrc"
if [ -f "$HOME/.zshrc" ]; then
    SHELL_RC="$HOME/.zshrc"
fi

touch "$SHELL_RC"
if ! grep -q "omnicache run claude" "$SHELL_RC"; then
    echo "" >> "$SHELL_RC"
    echo "# OmniCache AI Proxy Shortcuts" >> "$SHELL_RC"
    echo "alias claude-cached='omnicache run claude'" >> "$SHELL_RC"
    echo "export ANTHROPIC_BASE_URL=\"http://127.0.0.1:8000\"" >> "$SHELL_RC"
    echo "export OPENAI_BASE_URL=\"http://127.0.0.1:8000/v1\"" >> "$SHELL_RC"
    echo -e "${GREEN}✔ Injected proxy environment exports to $SHELL_RC${NC}"
fi

chmod +x "$0" 2>/dev/null || true

echo -e "\n${GREEN}======================================================${NC}"
echo -e "${GREEN}🎉 OmniCache successfully installed on your device!  ${NC}"
echo -e "${GREEN}======================================================${NC}"
echo -e "To start using OmniCache with your coding agents:"
echo -e "  1. Quick run:      ${CYAN}omnicache run claude${NC}"
echo -e "  2. Background run: ${CYAN}omnicache start${NC}"
echo -e "  3. Open Dashboard: ${CYAN}http://localhost:8000/dashboard${NC}"
echo -e "  4. Check stats:    ${CYAN}omnicache stats${NC}"
echo -e "======================================================\n"
