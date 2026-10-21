#!/usr/bin/env bash
set -euo pipefail

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

log() { echo -e "${!1}[${2}]${NC} $3"; }
info()    { log BLUE "INFO" "$1"; }
success() { log GREEN "OK"   "$1"; }
warn()    { log YELLOW "WARN" "$1"; }
error()   { log RED "FAIL" "$1"; exit 1; }

command_exists() { command -v "$1" >/dev/null 2>&1; }
version_ge() { [ "$(printf '%s\n' "$2" "$1" | sort -V | head -n1)" = "$2" ]; }

check_os() {
    [[ -r /etc/os-release ]] || error "Unable to detect the operating system."
    # shellcheck disable=SC1091
    source /etc/os-release
    case "${ID:-}" in
        ubuntu|debian) PACKAGE_MANAGER=apt ;;
        arch|manjaro)  PACKAGE_MANAGER=pacman ;;
        fedora)        PACKAGE_MANAGER=dnf ;;
        *)
            case " ${ID_LIKE:-} " in
                *debian*|*ubuntu*) PACKAGE_MANAGER=apt ;;
                *arch*)            PACKAGE_MANAGER=pacman ;;
                *fedora*)          PACKAGE_MANAGER=dnf ;;
                *) error "Unsupported distribution (${NAME:-unknown}). Supported: Debian/Ubuntu, Arch, Fedora." ;;
            esac
            ;;
    esac
    info "Detected ${NAME:-$ID} (package manager: $PACKAGE_MANAGER)"
}

check_python() {
    command_exists python3 || error "python3 is not installed."
    PY_VER=$(python3 -V | awk '{print $2}')
    version_ge "$PY_VER" "3.9" || error "Python >= 3.9 is required (found $PY_VER)."
    success "Python $PY_VER detected"
}

install_system_deps() {
    info "Installing system dependencies with $PACKAGE_MANAGER..."
    case "$PACKAGE_MANAGER" in
        apt)
            sudo apt-get update -qq
            sudo apt-get install -y \
                python3 python3-venv python3-pip git wget \
                iw iproute2 network-manager wpasupplicant adb
            if ! sudo apt-get install -y scrcpy; then
                warn "The distribution does not provide scrcpy; install a recent release manually."
            fi
            ;;
        pacman)
            sudo pacman -Sy --needed --noconfirm \
                python python-pip git wget iw iproute2 networkmanager wpa_supplicant android-tools
            if ! sudo pacman -S --needed --noconfirm scrcpy; then
                warn "The distribution does not provide scrcpy; install a recent release manually."
            fi
            ;;
        dnf)
            sudo dnf install -y \
                python3 python3-pip git wget iw iproute NetworkManager wpa_supplicant android-tools
            if ! sudo dnf install -y scrcpy; then
                warn "The distribution does not provide scrcpy; install a recent release manually."
            fi
            ;;
    esac
    success "System dependencies installed"
}

install_python_package() {
    if command_exists uv; then
        success "uv detected: $(uv --version)"
        info "Installing DexOnLinux from PyPI with uv tool..."
        uv tool install --upgrade dexonlinux
    else
        warn "uv not found; falling back to pip inside .venv."
        info "Creating Python virtual environment..."
        python3 -m venv .venv
        info "Installing DexOnLinux from PyPI with pip..."
        .venv/bin/pip install --upgrade pip
        .venv/bin/pip install --upgrade dexonlinux
    fi
    success "Python package installed"
}

check_scrcpy() {
    if ! command_exists scrcpy; then
        warn "scrcpy is not installed. Install a recent release before running DexOnLinux."
        return
    fi
    SCRC_VERSION=$(scrcpy --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1 || true)
    if [[ -n "$SCRC_VERSION" ]] && ! version_ge "$SCRC_VERSION" "2.0"; then
        warn "scrcpy $SCRC_VERSION is old; --list-displays requires scrcpy >= 2.0."
    else
        success "scrcpy ${SCRC_VERSION:-detected}"
    fi
}

print_usage() {
    echo ""
    if command_exists uv; then
        info "DexOnLinux is installed. Run:"
        echo -e "${YELLOW}dexonlinux --doctor${NC}"
        echo -e "${YELLOW}dexonlinux${NC}"
    else
        info "DexOnLinux is installed in .venv. Run:"
        echo -e "${YELLOW}source .venv/bin/activate${NC}"
        echo -e "${YELLOW}dexonlinux --doctor${NC}"
        echo -e "${YELLOW}dexonlinux${NC}"
    fi
    echo ""
}

main() {
    info "Starting DexOnLinux installation..."
    [[ "$EUID" -eq 0 ]] && error "Do not run this script as root. Run ./scripts/install.sh; it will ask for sudo only when needed."
    cd "$PROJECT_ROOT" || error "Cannot access project directory $PROJECT_ROOT"
    check_os
    check_python
    install_system_deps
    check_scrcpy
    install_python_package
    success "Installation completed!"
    print_usage
}

main "$@"
