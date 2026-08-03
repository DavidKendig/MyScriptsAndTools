#!/usr/bin/env bash
# AutoTranslate - one-shot setup for Ubuntu/Debian (and other common distros).
#
#   chmod +x install.sh && ./install.sh
#
# Options:
#   --yes           assume yes, never prompt (good for scripted setups)
#   --with-ollama   also install the Ollama server
#   --no-ollama     skip the Ollama question entirely
#   --model NAME    pull this vision model after installing Ollama

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$HERE"

ASSUME_YES=0
WANT_OLLAMA=""
PULL_MODEL="llama3.2-vision"

while [ $# -gt 0 ]; do
    case "$1" in
        --yes|-y)      ASSUME_YES=1 ;;
        --with-ollama) WANT_OLLAMA=1 ;;
        --no-ollama)   WANT_OLLAMA=0 ;;
        --model)       PULL_MODEL="${2:-}"; shift ;;
        -h|--help)     sed -n '2,10p' "$0"; exit 0 ;;
        *) echo "Unknown option: $1" >&2; exit 1 ;;
    esac
    shift
done

bold()  { printf '\033[1m%s\033[0m\n' "$*"; }
info()  { printf '  %s\n' "$*"; }
warn()  { printf '\033[33mWARNING: %s\033[0m\n' "$*"; }
die()   { printf '\033[31mERROR: %s\033[0m\n' "$*" >&2; exit 1; }

ask() {  # ask "question" -> 0 for yes
    [ "$ASSUME_YES" = "1" ] && return 0
    local reply
    read -r -p "$1 [y/N] " reply </dev/tty || return 1
    [[ "$reply" =~ ^[Yy] ]]
}

SUDO=""
if [ "$(id -u)" -ne 0 ]; then
    command -v sudo >/dev/null 2>&1 && SUDO="sudo" || die "Need root or sudo."
fi

bold "============================================================"
bold "  AutoTranslate installer"
bold "============================================================"
echo

# ---------------------------------------------------------------
# 1. System packages
# ---------------------------------------------------------------
bold "[1/4] System packages"
if command -v apt-get >/dev/null 2>&1; then
    export DEBIAN_FRONTEND=noninteractive
    $SUDO apt-get update -qq
    $SUDO apt-get install -y python3 python3-venv python3-pip python3-tk curl
elif command -v dnf >/dev/null 2>&1; then
    $SUDO dnf install -y python3 python3-pip python3-tkinter curl
elif command -v pacman >/dev/null 2>&1; then
    $SUDO pacman -Sy --noconfirm python python-pip tk curl
elif command -v zypper >/dev/null 2>&1; then
    $SUDO zypper install -y python3 python3-pip python3-tk curl
else
    warn "Unknown package manager - install python3, python3-venv and tk yourself."
fi

command -v python3 >/dev/null 2>&1 || die "python3 is still missing."
info "$(python3 --version)"

if ! python3 -c 'import tkinter' >/dev/null 2>&1; then
    warn "Tkinter is unavailable, so the GUI will not open."
    warn "Install it with: sudo apt install python3-tk"
    warn "Headless mode (./start.sh --cli) works regardless."
fi
echo

# ---------------------------------------------------------------
# 2. Virtual environment
# ---------------------------------------------------------------
bold "[2/4] Python environment"
if [ ! -x ".venv/bin/python" ]; then
    # --system-site-packages lets the venv borrow the distro's tkinter.
    python3 -m venv --system-site-packages .venv \
        || die "Could not create the virtual environment (try: sudo apt install python3-venv)"
    info "Created .venv"
else
    info "Reusing the existing .venv"
fi

./.venv/bin/python -m pip install --upgrade pip --quiet
if ./.venv/bin/python -m pip install -r requirements.txt --quiet; then
    info "Dependencies installed"
else
    warn "Pillow failed to install - the app still runs, without image downscaling."
fi
chmod +x start.sh 2>/dev/null || true
echo

# ---------------------------------------------------------------
# 3. Ollama (optional)
# ---------------------------------------------------------------
bold "[3/4] Model server"
if command -v ollama >/dev/null 2>&1; then
    info "Ollama is already installed."
    WANT_OLLAMA=0
elif [ "$WANT_OLLAMA" = "" ]; then
    if ask "Install Ollama now (downloads and runs the official script)?"; then
        WANT_OLLAMA=1
    else
        WANT_OLLAMA=0
    fi
fi

if [ "$WANT_OLLAMA" = "1" ]; then
    info "Installing Ollama from https://ollama.com/install.sh ..."
    curl -fsSL https://ollama.com/install.sh | sh
fi

if command -v ollama >/dev/null 2>&1; then
    command -v systemctl >/dev/null 2>&1 && $SUDO systemctl enable --now ollama || true
    if [ -n "$PULL_MODEL" ] && ask "Pull the vision model '$PULL_MODEL' now (several GB)?"; then
        ollama pull "$PULL_MODEL" || warn "Could not pull $PULL_MODEL - do it later."
    fi
else
    info "No Ollama here. Use LM Studio instead: https://lmstudio.ai"
fi
echo

# ---------------------------------------------------------------
# 4. Desktop shortcut
# ---------------------------------------------------------------
bold "[4/4] Desktop entry"
APPS="$HOME/.local/share/applications"
mkdir -p "$APPS"
cat > "$APPS/autotranslate.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=AutoTranslate
Comment=Batch-translate folders of images with a local AI model
Exec=$HERE/start.sh
Path=$HERE
Terminal=false
Categories=Utility;Graphics;
EOF
chmod +x "$APPS/autotranslate.desktop" 2>/dev/null || true
command -v update-desktop-database >/dev/null 2>&1 \
    && update-desktop-database "$APPS" >/dev/null 2>&1 || true
info "Added to your application menu"
echo

bold "============================================================"
bold "  Done."
bold "============================================================"
echo "  Launch the GUI  :  ./start.sh"
echo "  Headless batch  :  ./start.sh --cli --folder ./pages --model $PULL_MODEL"
echo
