#!/bin/bash

set -e  # Exit on error

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
SHARED_DB_PATH="$PROJECT_ROOT/libs/shared-db"
# Windows (Git Bash, Cygwin)
VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python"
VENV_PIP="$SCRIPT_DIR/.venv/Scripts/pip"

# Create .venv if it doesn't exist
if [ ! -d "$SCRIPT_DIR/.venv" ]; then
    echo "Creating virtual environment..."
    python -m venv "$SCRIPT_DIR/.venv"
fi

# Install requirements
echo "Installing requirements..."
"$VENV_PIP" install -r "$SCRIPT_DIR/requirements.txt"

# Install shared-db in editable mode
echo "Installing shared-db..."
"$VENV_PIP" install -e "$SHARED_DB_PATH"

echo ""
echo "✓ Environment prepared successfully!"
