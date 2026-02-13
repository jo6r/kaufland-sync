#!/bin/bash

set -e  # Exit on error

# Get the directory where the script is located
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

VENV_PYTHON="$SCRIPT_DIR/.venv/Scripts/python"

# Set database environment variables
export DB_HOST="${DB_HOST:-192.168.0.202}"
export DB_PORT="${DB_PORT:-3306}"
export DB_USER="${DB_USER:-shoptet_marketplace_sync}"
export DB_PASSWORD="${DB_PASSWORD:-shoptet_marketplace_sync123}"
export DB_NAME="${DB_NAME:-shoptet_marketplace_sync}"

# Feed URL (override with JIRI_MODELS_FEED_URL if needed)
export JIRI_MODELS_FEED_URL="${JIRI_MODELS_FEED_URL:-https://830da31362404f48af581b3d17b226.e3.environment.api.powerplatform.com/powerautomate/automations/direct/workflows/b0ef334a78e0481ab3dd289931d68684/triggers/manual/paths/invoke/systemId/6cd8732b-57fb-f011-8405-000d3a468045?api-version=1&sp=%2Ftriggers%2Fmanual%2Frun&sv=1.0&sig=wUhwagyusdwhbfyuaNpfHbvNXfQLap3A0JAFcIDmm-Q}"

# Run main.py with --local parameter
"$VENV_PYTHON" "$SCRIPT_DIR/main.py" --local
