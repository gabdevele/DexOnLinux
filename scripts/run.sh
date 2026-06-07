#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT" || {
    echo "Error: Cannot access project directory $PROJECT_ROOT"
    exit 1
}

if [ ! -d ".venv" ]; then
    echo "Error: Virtual environment '.venv' not found in $PROJECT_ROOT"
    echo "Please run the installation script first: ./scripts/install.sh"
    exit 1
fi

if [ ! -x ".venv/bin/dexonlinux" ]; then
    echo "Error: dexonlinux command not found in .venv"
    echo "Please run the installation script again: ./scripts/install.sh"
    exit 1
fi

echo "Starting DexOnLinux..."
source .venv/bin/activate
dexonlinux "$@"
deactivate
