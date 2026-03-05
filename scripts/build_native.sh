#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"

if [ ! -d "$PROJECT_DIR/c_src" ]; then
    echo "ERROR: c_src not found at $PROJECT_DIR/c_src"
    exit 1
fi

echo "Building libpmkernel from $PROJECT_DIR/c_src ..."
cd "$PROJECT_DIR" && make

LIB_DIR="$PROJECT_DIR/lib"
echo "Installed to $LIB_DIR:"
ls -lh "$LIB_DIR"/libpmkernel.* 2>/dev/null || echo "(no library found)"
