#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PROJECT_DIR="$(dirname "$SCRIPT_DIR")"
BSP_DIR="${BSP_DIR:-$(dirname "$PROJECT_DIR")/bs-p}"

if [ ! -d "$BSP_DIR/c_src" ]; then
    echo "ERROR: bs-p repo not found at $BSP_DIR"
    echo "Set BSP_DIR env var to the bs-p repository root."
    exit 1
fi

echo "Building libpmkernel from $BSP_DIR ..."
cd "$BSP_DIR" && make

LIB_DIR="$PROJECT_DIR/lib"
mkdir -p "$LIB_DIR"
cp "$BSP_DIR"/libpmkernel.* "$LIB_DIR/" 2>/dev/null

echo "Installed to $LIB_DIR:"
ls -lh "$LIB_DIR"/libpmkernel.* 2>/dev/null || echo "(no library found)"
