#!/bin/sh
# User-local install: no sudo, shell profile edits or Ghostty configuration edits.
set -eu
umask 077

SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
INSTALL_ROOT="${XDG_DATA_HOME:-$HOME/.local/share}/ghostbrowse"
VENV="$INSTALL_ROOT/venv"
BIN_DIR="$HOME/.local/bin"
PYTHON="${PYTHON:-python3}"

if ! command -v "$PYTHON" >/dev/null 2>&1; then
    printf '%s\n' 'Python 3.10+ is required. On macOS: brew install python' >&2
    exit 1
fi
"$PYTHON" -c 'import sys; sys.exit(0 if sys.version_info >= (3,10) else "Python 3.10+ is required")'
if [ "$(id -u)" = 0 ]; then
    printf '%s\n' 'Run install.sh as your normal user, without sudo.' >&2
    exit 1
fi
if [ -e "$BIN_DIR/ghostbrowse" ] || [ -L "$BIN_DIR/ghostbrowse" ]; then
    if [ "$(readlink "$BIN_DIR/ghostbrowse" 2>/dev/null || true)" != "$VENV/bin/ghostbrowse" ]; then
        printf '%s\n' "Refusing to overwrite an unrelated command: $BIN_DIR/ghostbrowse" >&2
        exit 1
    fi
fi
mkdir -p "$INSTALL_ROOT" "$BIN_DIR"
if [ ! -x "$VENV/bin/python" ]; then
    "$PYTHON" -m venv "$VENV"
fi
"$VENV/bin/python" -m pip install --upgrade pip
"$VENV/bin/python" -m pip install -r "$SOURCE_DIR/requirements.txt"
"$VENV/bin/python" -m pip install --no-deps "$SOURCE_DIR"
"$VENV/bin/python" -m playwright install chromium
ln -sf "$VENV/bin/ghostbrowse" "$BIN_DIR/ghostbrowse"
printf '\nInstalled: %s\n\n' "$BIN_DIR/ghostbrowse"
printf '%s\n' 'Start inside Ghostty:' '  ~/.local/bin/ghostbrowse http://localhost:3000' ''
printf '%s\n' 'To make the command available in this shell:' '  export PATH="$HOME/.local/bin:$PATH"' ''
printf '%s\n' 'Ctrl+L: URL | Ctrl+T: tab | F7/F8: history | Ctrl+Q: return to shell'
