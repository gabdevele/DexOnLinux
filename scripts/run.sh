#!/bin/bash

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"

cd "$PROJECT_ROOT" || {
    echo "Error: Cannot access project directory $PROJECT_ROOT"
    exit 1
}

if [ ! -d "venv" ]; then
    echo "Error: Virtual environment 'venv' not found in $PROJECT_ROOT"
    echo "Please run the installation script first: ./scripts/install.sh"
    exit 1
fi

if [ ! -f "src/dexonlinux/main.py" ]; then
    echo "Error: main.py not found in $PROJECT_ROOT"
    exit 1
fi

echo "Starting DexOnLinux..."
source venv/bin/activate
python3 src/dexonlinux/main.py
deactivate