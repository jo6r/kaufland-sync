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

# Set Kaufland API credentials (must be set by user)
if [ -z "$KAUFLAND_CLIENT_KEY" ] || [ -z "$KAUFLAND_SECRET_KEY" ]; then
    echo "Error: KAUFLAND_CLIENT_KEY and KAUFLAND_SECRET_KEY must be set"
    exit 1
fi

# Run main.py with --local parameter
"$VENV_PYTHON" "$SCRIPT_DIR/main.py" --local
