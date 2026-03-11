#!/bin/bash

set -e  # Exit on error

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python"

# Set database environment variables
export DB_HOST="${DB_HOST:-192.168.0.202}"
export DB_PORT="${DB_PORT:-3306}"
export DB_USER="${DB_USER:-kaufland_sync}"
export DB_PASSWORD="${DB_PASSWORD:-kaufland_sync123}"
export DB_NAME="${DB_NAME:-kaufland_sync}"
export KAUFLAND_BASE_URL="${KAUFLAND_BASE_URL:-https://sellerapi.kaufland.com}"

# Run main.py with --local parameter
"$VENV_PYTHON" "$SCRIPT_DIR/main.py" --local
