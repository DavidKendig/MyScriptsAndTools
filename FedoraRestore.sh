#!/usr/bin/env bash
#
# FedoraRestore.sh - Set up the GNOME desktop on a fresh Fedora Workstation.
#
# Removes Firefox, enables RPM Fusion, installs Brave, ffmpeg, Node.js,
# browser_cookie3, ProtonVPN, GNOME Tweaks, and the Dash to Dock and
# AppIndicator extensions, then enables them.
#
# Usage:  ./FedoraRestore.sh            # run everything
#         ./FedoraRestore.sh --dry-run  # print the commands without running them
#
# Run as your normal user; the script calls sudo where it needs root.

set -uo pipefail

DRY_RUN=0

# Print the header comment block (everything after the shebang up to the first
# line that isn't a comment).
usage() {
    awk 'NR == 1 { next } /^#/ { sub(/^# ?/, ""); print; next } { exit }' "$0"
    exit "${1:-0}"
}

while (( $# )); do
    case "$1" in
        --dry-run) DRY_RUN=1 ;;
        -h|--help) usage 0 ;;
        *) printf 'Unknown option: %s\n\n' "$1" >&2; usage 1 ;;
    esac
    shift
done

SUCCEEDED=()
FAILED=()

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

# ------------------------------------------------------------ sanity check ---

if [[ $EUID -eq 0 ]]; then
    err "Run this as your normal user, not root - the extension is enabled per-user."
    exit 1
fi

if ! have dnf; then
    err "dnf not found. This script targets Fedora."
    exit 1
fi

FEDORA_VER="$(rpm -E %fedora)"
info "Fedora $FEDORA_VER detected"
(( DRY_RUN )) && warn "Dry run - nothing will actually be changed."

if ! sudo -v; then
    err "sudo authentication failed."
    exit 1
fi

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
if (( ! DRY_RUN )) && have flatpak \
   && flatpak list --app 2>/dev/null | grep -q 'org.mozilla.firefox'; then
    step "Remove the Firefox flatpak" \
        flatpak uninstall -y org.mozilla.firefox
fi

# -------------------------------------------------------------- RPM Fusion ---

# The third-party repos Fedora can't ship itself: free carries the full ffmpeg
# and other codec-dependent packages, nonfree carries the proprietary ones
# (NVIDIA drivers, Steam, and friends).
step "Enable RPM Fusion free + nonfree" \
    sudo dnf install -y \
        "https://mirrors.rpmfusion.org/free/fedora/rpmfusion-free-release-${FEDORA_VER}.noarch.rpm" \
        "https://mirrors.rpmfusion.org/nonfree/fedora/rpmfusion-nonfree-release-${FEDORA_VER}.noarch.rpm"

# ------------------------------------------------------------------- Brave ---

# dnf5 (Fedora 41+) and dnf4 spell repo management differently.
add_repo() {
    local repo_url="$1"
    if dnf config-manager --help 2>&1 | grep -q 'addrepo'; then
        sudo dnf config-manager addrepo --overwrite --from-repofile="$repo_url"
    else
        sudo dnf config-manager --add-repo "$repo_url"
    fi
}

step "Install dnf-plugins-core (provides dnf config-manager)" \
    sudo dnf install -y dnf-plugins-core

step "Add the Brave browser repo" \
    add_repo "https://brave-browser-rpm-release.s3.brave.com/brave-browser.repo"

step "Install Brave" \
    sudo dnf install -y brave-browser

# ------------------------------------------------------------------ ffmpeg ---

# Fedora's own repos only carry ffmpeg-free, which is built without the
# patent-encumbered codecs. The full build comes from RPM Fusion free, and
# --allowerasing lets it replace ffmpeg-free rather than conflict with it.
install_ffmpeg() {
    if sudo dnf install -y --allowerasing ffmpeg; then
        return 0
    fi
    warn "Full ffmpeg unavailable - falling back to Fedora's ffmpeg-free"
    sudo dnf install -y ffmpeg-free
}

step "Install ffmpeg" install_ffmpeg

# ----------------------------------------------------------------- Node.js ---

# Fedora's nodejs package tracks whichever LTS the release shipped with. To pin
# a different major instead, install the versioned package (nodejs22, nodejs20,
# ...) - dnf swaps the default out for it.
install_node() {
    sudo dnf install -y nodejs npm || return 1
    have node && info "node $(node --version), npm $(npm --version)"
    return 0
}

step "Install Node.js and npm" install_node

# ---------------------------------------------------------- browser_cookie3 ---

# Python library that reads cookies out of installed browsers. Its Chrome/Brave
# path decrypts through the Secret Service, so python3-secretstorage comes from
# dnf rather than pip.
install_browser_cookie3() {
    sudo dnf install -y python3-pip python3-secretstorage || return 1

    # Fedora marks the system interpreter externally managed (PEP 668), so a
    # plain --user install is refused; retry with the documented escape hatch.
    if pip3 install --user browser-cookie3; then
        return 0
    fi
    warn "pip refused a --user install (PEP 668) - retrying with --break-system-packages"
    pip3 install --user --break-system-packages browser-cookie3
}

step "Install browser_cookie3" install_browser_cookie3

# -------------------------------------------------------------- ProtonVPN ---

# Proton publishes a per-release repo; the release rpm just drops the repo file
# and its signing key in place. Bump this if Proton ships a newer one:
#   PROTONVPN_RELEASE=1.0.5-1 ./FedoraRestore.sh
PROTONVPN_RELEASE="${PROTONVPN_RELEASE:-1.0.4-1}"

install_protonvpn() {
    local rpm="protonvpn-stable-release-${PROTONVPN_RELEASE}.noarch.rpm"
    local url="https://repo.protonvpn.com/fedora-${FEDORA_VER}-stable/protonvpn-stable-release/${rpm}"

    local tmp
    tmp="$(mktemp -d)"
    if ! curl -fL --progress-bar -o "$tmp/$rpm" "$url"; then
        err "Could not download $rpm - Proton may not publish a repo for Fedora $FEDORA_VER yet"
        rm -rf "$tmp"
        return 1
    fi

    if ! sudo dnf install -y "$tmp/$rpm"; then
        rm -rf "$tmp"
        return 1
    fi
    rm -rf "$tmp"

    # check-update exits 100 when updates are available, which is not an error.
    sudo dnf check-update --refresh || true

    sudo dnf install -y proton-vpn-gnome-desktop
}

step "Install ProtonVPN" install_protonvpn

# The Proton client puts its status in the system tray, which GNOME only shows
# with the AppIndicator extension.
step "Install the AppIndicator tray support ProtonVPN needs" \
    sudo dnf install -y libappindicator-gtk3 gnome-shell-extension-appindicator \
        gnome-extensions-app

# --------------------------------------------------------- GNOME packages ---

step "Install GNOME Tweaks" \
    sudo dnf install -y gnome-tweaks

step "Install the Dash to Dock GNOME extension" \
    sudo dnf install -y gnome-shell-extension-dash-to-dock

# ----------------------------------------------------- enable the extensions ---

# uuid|label
EXTENSIONS=(
    "dash-to-dock@micxgx.gmail.com|Dash to Dock"
    "appindicatorsupport@rgcjonas.gmail.com|AppIndicator tray icons"
)

for entry in "${EXTENSIONS[@]}"; do
    uuid="${entry%%|*}"
    label="${entry##*|}"

    info "Enable $label"
    if (( DRY_RUN )); then
        printf '    (dry-run) gnome-extensions enable %s\n' "$uuid"
        SUCCEEDED+=("Enable $label")
    elif have gnome-extensions && [[ -n "${XDG_CURRENT_DESKTOP:-}" ]]; then
        if gnome-extensions enable "$uuid" 2>/dev/null; then
            SUCCEEDED+=("Enable $label")
        else
            warn "Could not enable it yet - log out and back in, then run:"
            warn "  gnome-extensions enable $uuid"
        fi
    else
        warn "No GNOME session here. After rebooting, turn $label on in the Extensions app."
    fi
done

# ----------------------------------------------------------------- summary ---

printf '\n\033[1;34m================ Summary ================\033[0m\n'
printf '\033[1;32mOK (%d):\033[0m\n' "${#SUCCEEDED[@]}"
for item in "${SUCCEEDED[@]}"; do printf '  + %s\n' "$item"; done

if (( ${#FAILED[@]} )); then
    printf '\033[1;31mFailed (%d):\033[0m\n' "${#FAILED[@]}"
    for item in "${FAILED[@]}"; do printf '  - %s\n' "$item"; done
fi

printf '\nReboot (or log out and back in) so GNOME picks up the new extensions.\n'
(( ${#FAILED[@]} )) && exit 1
exit 0
