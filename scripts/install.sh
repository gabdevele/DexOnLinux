#!/bin/bash
set -e

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
    source /etc/os-release || error "Unable to detect operating system."
    [[ "$ID" =~ (debian|ubuntu) ]] || error "Unsupported system ($NAME)."
    info "Detected OS: $NAME $VERSION_ID"
}

check_python() {
    command_exists python3 || error "Python3 not installed. Run: sudo apt install python3"
    PY_VER=$(python3 -V | awk '{print $2}')
    version_ge "$PY_VER" "3.9" || error "Python >=3.9 required (found $PY_VER)"
    success "Python $PY_VER detected"
}

install_system_deps() {
    info "Updating packages and installing dependencies..."
    sudo apt-get update -qq
    sudo apt-get install -y python3-pip python3-venv git wget
    sudo apt-get install -y cmake libglib2.0-dev libudev-dev libsystemd-dev libreadline-dev check libtool autoconf #Miraclecast's dependencies
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

install_miraclecast() {
    command_exists miracle-sinkctl && { success "Miraclecast already installed"; return; }
    info "Installing Miraclecast..."
    git clone https://github.com/albfan/miraclecast.git 2>/dev/null
    cd miraclecast
    sudo cp res/org.freedesktop.miracle.conf /etc/dbus-1/system.d/
    rm -rf build && mkdir build && cd build
    cmake -DCMAKE_INSTALL_PREFIX=/usr ..
    make -j$(nproc)
    sudo make install && sudo ldconfig
    cd ../..
    success "Miraclecast installed"
}

install_scrcpy() {
    REQ="3.3.2"
    ARCH=$(uname -m)
    [[ "$ARCH" == "x86_64" ]] || error "Automatic scrcpy install currently supports x86_64 only (found $ARCH). Install scrcpy manually."
    if command_exists scrcpy; then
        CUR=$(scrcpy --version 2>/dev/null | grep -oE '[0-9]+\.[0-9]+(\.[0-9]+)?' | head -n1)
        version_ge "$CUR" "$REQ" && { success "Scrcpy $CUR already present"; return; }
        warn "Updating Scrcpy ($CUR < $REQ)"
        sudo apt-get remove -y scrcpy || true
    fi

    info "Downloading scrcpy $REQ..."
    URL="https://github.com/Genymobile/scrcpy/releases/download/v${REQ}/scrcpy-linux-x86_64-v${REQ}.tar.gz"
    TMP=$(mktemp -d); cd "$TMP"
    wget -qO scrcpy.tar.gz "$URL" || error "Failed to download scrcpy"
    tar -xzf scrcpy.tar.gz
    DIR=$(find . -type d -name "scrcpy-linux-*")
    sudo cp -r "$DIR/scrcpy" /usr/bin/
    sudo cp -r "$DIR/scrcpy-server" /usr/bin/
    cd /; rm -rf "$TMP"
    success "Scrcpy $REQ installed"
}

print_usage() {
    echo ""
    if command_exists uv; then
        info "DexOnLinux is installed. Run:"
        echo -e "${YELLOW}dexonlinux${NC}"
    else
        info "DexOnLinux is installed in .venv. Run:"
        echo -e "${YELLOW}source .venv/bin/activate${NC}"
        echo -e "${YELLOW}dexonlinux${NC}"
    fi
    echo ""
}

main() {
    info "Starting DexOnLinux installation..."
    [[ "$EUID" -eq 0 ]] && error "Do not run this script as root. Run ./scripts/install.sh; it will ask sudo only when needed."
    cd "$PROJECT_ROOT" || error "Cannot access project directory $PROJECT_ROOT"
    check_os
    check_python
    install_system_deps
    install_python_package
    install_miraclecast
    install_scrcpy
    success "Installation completed!"
    print_usage
}

main "$@"
