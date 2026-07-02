#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_DIR="$SCRIPT_DIR/data"
OUTPUT_FILE="$DATA_DIR/products.csv"
CSV_URL="https://www.mamito.cz/export/products.csv?patternId=27&partnerId=33&hash=62461a573739a6838662d347f4e4beca92c3bc12ebb221046b60a046fd379fd5"
TMP_FILE="$(mktemp)"

trap 'rm -f "$TMP_FILE"' EXIT

mkdir -p "$DATA_DIR"

curl -fL "$CSV_URL" -o "$TMP_FILE"


if ! iconv -f WINDOWS-1250 -t UTF-8 "$TMP_FILE" > "$OUTPUT_FILE" 2>/dev/null; then
	echo "Error: Could not convert CSV from WINDOWS-1250 to UTF-8." >&2
	exit 1
fi

echo "CSV downloaded and converted from WINDOWS-1250 to UTF-8: $OUTPUT_FILE"
