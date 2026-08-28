#!/usr/bin/env bash
#
# FedoraRestore.sh - Rebuild a personal Fedora Workstation app set after a fresh install.
#
# Removes Firefox and installs: Brave, GNOME Extension Manager, Dash to Dock,
# VLC, LibreOffice Base, JetBrains Toolbox, OBS Studio, LM Studio, opencode,
# Sublime Text, Steam, Obsidian, the latest OpenJDK, Python, and the
# Claude Code CLI.
#
# Usage:  ./FedoraRestore.sh                  # run everything
#         ./FedoraRestore.sh --dry-run        # print the commands without running them
#         ./FedoraRestore.sh --with-desktop   # also set up the Claude desktop app
#                                             # in an Ubuntu distrobox (see notes below)
#
# Run as your normal user; the script calls sudo where it needs root.

set -uo pipefail

DRY_RUN=0
WITH_DESKTOP=0

# Print the header comment block (everything after the shebang up to the first
# line that isn't a comment).
usage() {
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    exit "${1:-0}"
}

while (( $# )); do
    case "$1" in
        --dry-run)      DRY_RUN=1 ;;
        --with-desktop) WITH_DESKTOP=1 ;;
        -h|--help)      usage 0 ;;
        *) printf 'Unknown option: %s\n\n' "$1" >&2; usage 1 ;;
    esac
    shift
done

SUCCEEDED=()
FAILED=()

# Anthropic's release signing key, used for both the CLI rpm repo and the
# desktop app's apt repo. Verify at https://code.claude.com/docs/en/setup
CLAUDE_KEY_FPR="31DDDE24DDFAB679F42D7BD2BAA929FF1A7ECACE"

# Ubuntu container used for the (Debian-only) Claude desktop app.
DESKTOP_BOX="${DESKTOP_BOX:-claude-desktop}"
DESKTOP_IMAGE="${DESKTOP_IMAGE:-ubuntu:24.04}"

# JetBrains Toolbox, which manages the IDE installs itself.
TOOLBOX_DIR="${TOOLBOX_DIR:-$HOME/.local/share/JetBrains/Toolbox/bin}"

# LM Studio ships as an AppImage with a versioned URL. Override if this 404s:
#   LMSTUDIO_URL=https://installers.lmstudio.ai/linux/x64/<version>/LM-Studio-<version>-x64.AppImage ./FedoraRestore.sh
LMSTUDIO_URL="${LMSTUDIO_URL:-https://installers.lmstudio.ai/linux/x64/0.3.30-2/LM-Studio-0.3.30-2-x64.AppImage}"
LMSTUDIO_DIR="${LMSTUDIO_DIR:-$HOME/Applications}"

# ---------------------------------------------------------------- helpers ---

info() { printf '\n\033[1;34m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[warn]\033[0m %s\n' "$*"; }
err()  { printf '\033[1;31m[fail]\033[0m %s\n' "$*"; }

run() {
    if (( DRY_RUN )); then
        printf '    (dry-run) %s\n' "$*"
        return 0
    fi
    "$@"
}

# step "Label" cmd...  - never aborts the script, just records the result
step() {
    local label="$1"; shift
    info "$label"
    if run "$@"; then
        SUCCEEDED+=("$label")
        return 0
    fi
    err "$label"
    FAILED+=("$label")
    return 1
}

have()      { command -v "$1" >/dev/null 2>&1; }
installed() { rpm -q "$1" >/dev/null 2>&1; }

# dnf5 (Fedora 41+) and dnf4 spell repo management differently.
add_repo() {
    local repo_url="$1"
    if dnf config-manager --help 2>&1 | grep -q 'addrepo'; then
        sudo dnf config-manager addrepo --overwrite --from-repofile="$repo_url"
    else
        sudo dnf config-manager --add-repo "$repo_url"
    fi
}

# ------------------------------------------------------------ sanity check ---

if [[ $EUID -eq 0 ]]; then
    err "Run this as your normal user, not root - flatpak and opencode install per-user."
    exit 1
fi

if ! have dnf; then
    err "dnf not found. This script targets Fedora."
    exit 1
fi

FEDORA_VER="$(rpm -E %fedora)"
info "Fedora $FEDORA_VER detected"
(( DRY_RUN )) && warn "Dry run - nothing will actually be installed."

if ! sudo -v; then
    err "sudo authentication failed."
    exit 1
fi

# ------------------------------------------------------------ base plumbing ---

step "Update installed packages" \
    sudo dnf upgrade --refresh -y

step "Install core tooling (dnf-plugins-core, curl, gnupg, flatpak, fuse)" \
    sudo dnf install -y dnf-plugins-core curl gnupg2 flatpak fuse fuse-libs

step "Enable RPM Fusion free + nonfree (needed for VLC, OBS, Steam)" \
    sudo dnf install -y \
        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${FEDORA_VER}.noarch.rpm" \
        "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${FEDORA_VER}.noarch.rpm"

step "Add the Flathub remote" \
    flatpak remote-add --if-not-exists --user \
        flathub https://dl.flathub.org/repo/flathub.flatpakrepo

# ----------------------------------------------------------- remove Firefox ---

info "Remove Firefox"
if (( DRY_RUN )); then
    printf '    (dry-run) sudo dnf remove -y firefox\n'
    SUCCEEDED+=("Remove Firefox")
elif installed firefox; then
    if sudo dnf remove -y firefox; then
        SUCCEEDED+=("Remove Firefox")
    else
        err "Remove Firefox"
        FAILED+=("Remove Firefox")
    fi
else
    warn "firefox rpm not installed - nothing to remove"
    SUCCEEDED+=("Remove Firefox (already absent)")
fi

# The Flatpak build, if that one is present too.
if (( ! DRY_RUN )) && flatpak list --app 2>/dev/null | grep -q 'org.mozilla.firefox'; then
    step "Remove the Firefox flatpak" \
        flatpak uninstall -y org.mozilla.firefox
fi

# ------------------------------------------------------------- dnf packages ---

step "Add the Brave browser repo" \
    add_repo "https://brave-browser-rpm-release.s3.brave.com/brave-browser.repo"

step "Install Brave" \
    sudo dnf install -y brave-browser

step "Add the Sublime Text signing key" \
    sudo rpm -v --import https://download.sublimetext.com/sublimehq-rpm-pub.gpg

step "Add the Sublime Text repo" \
    add_repo "https://download.sublimetext.com/rpm/stable/x86_64/sublime-text.repo"

step "Install Sublime Text" \
    sudo dnf install -y sublime-text

step "Install VLC (RPM Fusion)" \
    sudo dnf install -y vlc

step "Install OBS Studio (RPM Fusion)" \
    sudo dnf install -y obs-studio

step "Install Steam (RPM Fusion nonfree)" \
    sudo dnf install -y steam

step "Install LibreOffice Base" \
    sudo dnf install -y libreoffice-base

step "Install the Dash to Dock GNOME extension" \
    sudo dnf install -y gnome-shell-extension-dash-to-dock

# ------------------------------------------------------------- toolchains ---

# java-latest-openjdk tracks the newest JDK Fedora packages; fall back to the
# distro default if that package name isn't in the repos.
install_jdk() {
    if sudo dnf install -y java-latest-openjdk java-latest-openjdk-devel; then
        return 0
    fi
    warn "java-latest-openjdk unavailable - falling back to java-openjdk"
    sudo dnf install -y java-openjdk java-openjdk-devel
}

step "Install the latest OpenJDK" install_jdk

# Fedora's python3 is the system interpreter; a python3.NN package installs
# alongside it without touching /usr/bin/python3, so both are safe to have.
install_python() {
    sudo dnf install -y python3 python3-pip python3-devel || return 1

    local newest
    newest="$(dnf -q repoquery --qf '%{name}\n' 'python3.*' 2>/dev/null \
        | grep -E '^python3\.[0-9]+$' | sort -V | tail -n 1)"

    if [[ -n "$newest" && "$newest" != "$(python3 -c 'import sys; print("python3.%d" % sys.version_info[1])' 2>/dev/null)" ]]; then
        info "Newest packaged interpreter is $newest - installing it alongside python3"
        sudo dnf install -y "$newest" || warn "Could not install $newest"
    fi
    return 0
}

step "Install Python (system python3 + pip, plus the newest packaged version)" \
    install_python

# ---------------------------------------------------------------- flatpaks ---

step "Install GNOME Extension Manager" \
    flatpak install -y --user flathub com.mattjakeman.ExtensionManager

step "Install Obsidian" \
    flatpak install -y --user flathub md.obsidian.Obsidian

# ------------------------------------------------------ JetBrains Toolbox ---

# The Flathub IDE builds trail upstream and drag in an end-of-life
# org.freedesktop.Sdk runtime. Toolbox is JetBrains' own Linux channel: it
# installs the IDEs itself and keeps them current.
install_jetbrains_toolbox() {
    local arch_key="linux"
    [[ "$(uname -m)" == "aarch64" ]] && arch_key="linuxARM64"

    local release_json link sum_link
    release_json="$(curl -fsSL \
        "https://data.services.jetbrains.com/products/releases?code=TBA&latest=true&type=release")" \
        || return 1

    read -r link sum_link <<<"$(printf '%s' "$release_json" | python3 -c "
import json, sys
d = json.load(sys.stdin)['TBA'][0]['downloads']['$arch_key']
print(d['link'], d['checksumLink'])
" 2>/dev/null)"

    if [[ -z "${link:-}" ]]; then
        err "Could not read a Toolbox download URL for $arch_key from the release API"
        return 1
    fi

    local tmp
    tmp="$(mktemp -d)"
    if ! curl -fL --progress-bar -o "$tmp/toolbox.tar.gz" "$link"; then
        rm -rf "$tmp"
        return 1
    fi

    local want have
    want="$(curl -fsSL "$sum_link" | awk '{print $1}')"
    have="$(sha256sum "$tmp/toolbox.tar.gz" | awk '{print $1}')"
    if [[ -z "$want" || "$want" != "$have" ]]; then
        err "Toolbox checksum mismatch - expected ${want:-<none>}, got $have"
        rm -rf "$tmp"
        return 1
    fi

    tar -xzf "$tmp/toolbox.tar.gz" -C "$tmp" || { rm -rf "$tmp"; return 1; }

    local src
    src="$(find "$tmp" -maxdepth 1 -type d -name 'jetbrains-toolbox-*' | head -n 1)"
    if [[ -z "$src" ]]; then
        err "Unexpected Toolbox archive layout"
        rm -rf "$tmp"
        return 1
    fi

    mkdir -p "$TOOLBOX_DIR"
    rm -rf "${TOOLBOX_DIR:?}"/*
    cp -a "$src"/. "$TOOLBOX_DIR/" || { rm -rf "$tmp"; return 1; }
    rm -rf "$tmp"

    mkdir -p "$HOME/.local/bin"
    ln -sf "$TOOLBOX_DIR/jetbrains-toolbox" "$HOME/.local/bin/jetbrains-toolbox"

    local icon="applications-development"
    [[ -f "$TOOLBOX_DIR/toolbox.svg" ]] && icon="$TOOLBOX_DIR/toolbox.svg"

    mkdir -p "$HOME/.local/share/applications"
    cat > "$HOME/.local/share/applications/jetbrains-toolbox.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=JetBrains Toolbox
Exec=$TOOLBOX_DIR/jetbrains-toolbox
Icon=$icon
Terminal=false
Categories=Development;
DESKTOP
    update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
    return 0
}

step "Install JetBrains Toolbox" install_jetbrains_toolbox

# Drop the old Flathub IDE builds if a previous run installed them, then let
# flatpak garbage-collect any runtime nothing references any more.
retire_jetbrains_flatpaks() {
    local app
    for app in com.jetbrains.PyCharm-Community com.jetbrains.IntelliJ-IDEA-Community; do
        if flatpak list --app --columns=application 2>/dev/null | grep -qx "$app"; then
            info "Removing the $app flatpak in favour of Toolbox"
            flatpak uninstall -y "$app" || warn "Could not remove $app"
        fi
    done
    flatpak uninstall --unused -y >/dev/null 2>&1
    return 0
}

step "Retire the Flathub JetBrains IDEs and unused runtimes" \
    retire_jetbrains_flatpaks

# --------------------------------------------------------- enable the dock ---

info "Enable Dash to Dock"
if (( DRY_RUN )); then
    printf '    (dry-run) gnome-extensions enable dash-to-dock@micxgx.gmail.com\n'
elif have gnome-extensions && [[ -n "${XDG_CURRENT_DESKTOP:-}" ]]; then
    if gnome-extensions enable dash-to-dock@micxgx.gmail.com 2>/dev/null; then
        SUCCEEDED+=("Enable Dash to Dock")
    else
        warn "Could not enable it yet - log out and back in, then run:"
        warn "  gnome-extensions enable dash-to-dock@micxgx.gmail.com"
    fi
else
    warn "No GNOME session here. After rebooting, turn it on in Extension Manager."
fi

# --------------------------------------------------------------- LM Studio ---

info "Install LM Studio (AppImage)"
if (( DRY_RUN )); then
    printf '    (dry-run) curl -fL -o %s/LM-Studio.AppImage %s\n' "$LMSTUDIO_DIR" "$LMSTUDIO_URL"
else
    mkdir -p "$LMSTUDIO_DIR"
    if curl -fL --progress-bar -o "$LMSTUDIO_DIR/LM-Studio.AppImage" "$LMSTUDIO_URL"; then
        chmod +x "$LMSTUDIO_DIR/LM-Studio.AppImage"
        mkdir -p "$HOME/.local/share/applications"
        cat > "$HOME/.local/share/applications/lm-studio.desktop" <<DESKTOP
[Desktop Entry]
Type=Application
Name=LM Studio
Exec=$LMSTUDIO_DIR/LM-Studio.AppImage %U
Icon=applications-science
Terminal=false
Categories=Development;Utility;
DESKTOP
        update-desktop-database "$HOME/.local/share/applications" 2>/dev/null
        SUCCEEDED+=("Install LM Studio")
    else
        err "Install LM Studio"
        FAILED+=("Install LM Studio")
        warn "The pinned AppImage URL may be stale. Grab the current link from"
        warn "https://lmstudio.ai/download and re-run with LMSTUDIO_URL=<url>"
    fi
fi

# ---------------------------------------------------------------- opencode ---

# Vendor install script from opencode.ai; drops the binary in ~/.opencode/bin.
info "Install opencode"
if (( DRY_RUN )); then
    printf '    (dry-run) curl -fsSL https://opencode.ai/install | bash\n'
elif curl -fsSL https://opencode.ai/install | bash; then
    SUCCEEDED+=("Install opencode")
    case ":$PATH:" in
        *":$HOME/.opencode/bin:"*) ;;
        *) warn "Add to your shell profile: export PATH=\"\$HOME/.opencode/bin:\$PATH\"" ;;
    esac
else
    err "Install opencode"
    FAILED+=("Install opencode")
fi

# -------------------------------------------------------- Claude Code CLI ---

# Anthropic publishes a signed rpm repo for Fedora/RHEL.
install_claude_cli() {
    local key_tmp
    key_tmp="$(mktemp)"
    if ! curl -fsSL https://downloads.claude.ai/keys/claude-code.asc -o "$key_tmp"; then
        rm -f "$key_tmp"
        return 1
    fi
    if ! gpg --show-keys --with-colons "$key_tmp" | grep -q "$CLAUDE_KEY_FPR"; then
        err "Signing key fingerprint does not match $CLAUDE_KEY_FPR - refusing to import"
        rm -f "$key_tmp"
        return 1
    fi
    sudo rpm --import "$key_tmp" || { rm -f "$key_tmp"; return 1; }
    rm -f "$key_tmp"

    sudo tee /etc/yum.repos.d/claude-code.repo >/dev/null <<'REPO'
[claude-code]
name=Claude Code
baseurl=https://downloads.claude.ai/claude-code/rpm/stable
enabled=1
gpgcheck=1
gpgkey=https://downloads.claude.ai/keys/claude-code.asc
REPO

    sudo dnf install -y claude-code
}

step "Install the Claude Code CLI (signed dnf repo)" install_claude_cli

# ------------------------------------------------- Claude desktop app (opt) ---

# The official desktop app is Debian/Ubuntu only - Fedora is not supported yet.
# This runs it out of an Ubuntu distrobox container and exports the launcher to
# the host. Unsupported by Anthropic; the CLI above is the documented path.
install_claude_desktop() {
    sudo dnf install -y distrobox podman || return 1

    local extra_flags=""
    if [[ -e /dev/kvm ]]; then
        extra_flags+=" --device /dev/kvm"
        sudo usermod -aG kvm "$USER" || warn "Could not add $USER to the kvm group"
    else
        warn "/dev/kvm is missing - turn on hardware virtualization in firmware for Cowork"
    fi
    [[ -e /dev/vhost-vsock ]] && extra_flags+=" --device /dev/vhost-vsock"

    if ! distrobox list --no-color 2>/dev/null | grep -qw "$DESKTOP_BOX"; then
        distrobox create --yes --name "$DESKTOP_BOX" --image "$DESKTOP_IMAGE" \
            ${extra_flags:+--additional-flags "$extra_flags"} || return 1
    fi

    local inner
    inner="$(cat <<INNER
set -e
export DEBIAN_FRONTEND=noninteractive
sudo apt update
sudo apt install -y curl gnupg
sudo curl -fsSLo /usr/share/keyrings/claude-desktop-archive-keyring.asc \
    https://downloads.claude.ai/claude-desktop/key.asc
gpg --show-keys --with-colons /usr/share/keyrings/claude-desktop-archive-keyring.asc \
    | grep -q $CLAUDE_KEY_FPR
echo "deb [arch=amd64,arm64 signed-by=/usr/share/keyrings/claude-desktop-archive-keyring.asc] https://downloads.claude.ai/claude-desktop/apt/stable stable main" \
    | sudo tee /etc/apt/sources.list.d/claude-desktop.list
sudo apt update
sudo apt install -y claude-desktop
INNER
)"

    distrobox enter --name "$DESKTOP_BOX" -- bash -c "$inner" || return 1
    distrobox enter --name "$DESKTOP_BOX" -- distrobox-export --app claude-desktop
}

if (( WITH_DESKTOP )); then
    step "Install the Claude desktop app in an Ubuntu distrobox" install_claude_desktop
    if (( ! DRY_RUN )); then
        warn "Log out and back in so the kvm group takes effect, then launch Claude"
        warn "from your app grid. Update it later with:"
        warn "  distrobox enter --name $DESKTOP_BOX -- sudo apt update"
        warn "  distrobox enter --name $DESKTOP_BOX -- sudo apt upgrade claude-desktop"
    fi
else
    info "Skipping the Claude desktop app (pass --with-desktop to install it)"
fi

# ----------------------------------------------------------------- summary ---

printf '\n\033[1;34m================ Summary ================\033[0m\n'
printf '\033[1;32mOK (%d):\033[0m\n' "${#SUCCEEDED[@]}"
for item in "${SUCCEEDED[@]}"; do printf '  + %s\n' "$item"; done

if (( ${#FAILED[@]} )); then
    printf '\033[1;31mFailed (%d):\033[0m\n' "${#FAILED[@]}"
    for item in "${FAILED[@]}"; do printf '  - %s\n' "$item"; done
fi

printf '\nReboot (or log out and back in) so GNOME picks up Dash to Dock.\n'
printf 'Then open JetBrains Toolbox and install PyCharm and IntelliJ IDEA from it.\n'
(( ${#FAILED[@]} )) && exit 1
exit 0
