#!/usr/bin/env bash
# ==============================================================================
# Open-Source README Sponsorship Platform - Bash Startup Script (start.sh)
# ==============================================================================
# Universal POSIX / Linux / macOS / WSL startup wrapper.
# Automatically detects virtual environments and executes start.py via exec.
# ==============================================================================

set -eo pipefail

# 1. Resolve repository root directory
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

# 2. Automatically detect and activate virtual environments if present
if [ -z "$VIRTUAL_ENV" ]; then
    if [ -f "$SCRIPT_DIR/.venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/.venv/bin/activate"
    elif [ -f "$SCRIPT_DIR/venv/bin/activate" ]; then
        # shellcheck disable=SC1091
        source "$SCRIPT_DIR/venv/bin/activate"
    fi
fi

# 3. Discover Python binary (validating candidate execution and version >= 3.12)
PY_BIN=""
for candidate in python3 python py; do
    if command -v "$candidate" >/dev/null 2>&1; then
        if "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 12) else 1)" >/dev/null 2>&1; then
            PY_BIN="$candidate"
            break
        fi
    fi
done

if [ -z "$PY_BIN" ]; then
    # Check if any python candidate exists to report helpful diagnostic
    for candidate in python3 python py; do
        if command -v "$candidate" >/dev/null 2>&1; then
            PY_BIN="$candidate"
            break
        fi
    done
    if [ -z "$PY_BIN" ]; then
        echo -e "\033[91m[ERROR] Python is not installed or not in PATH.\033[0m" >&2
        echo "Please install Python 3.12+ (https://www.python.org/)." >&2
        exit 1
    fi
    echo -e "\033[91m[ERROR] Python 3.12 or higher is required.\033[0m" >&2
    echo "Current Python version: $($PY_BIN --version 2>&1)" >&2
    exit 1
fi

# 4. Hand off process directly to start.py via exec
# Using exec ensures SIGINT (Ctrl+C) and SIGTERM route directly to Python
exec "$PY_BIN" "$SCRIPT_DIR/start.py" "$@"
