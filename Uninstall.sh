#!/usr/bin/env bash

set -Eeuo pipefail

DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/paketsuche"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$DATA_HOME/applications"

rm -f -- "$BIN_DIR/paketsuche" "$DESKTOP_DIR/paketsuche.desktop"
rm -rf -- "$APP_DIR"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR"
fi

echo "Paketsuche wurde entfernt."
echo "Andere Python-Pakete und Benutzerdaten wurden nicht verändert."
