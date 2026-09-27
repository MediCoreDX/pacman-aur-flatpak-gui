#!/usr/bin/env bash
# Entfernt die per install.sh installierte Paketsuche-App wieder vollständig.
set -e
 
APP_NAME="paketsuche"
INSTALL_DIR="$HOME/.local/bin"
DESKTOP_DIR="$HOME/.local/share/applications"
 
rm -f "$INSTALL_DIR/$APP_NAME"
rm -f "$DESKTOP_DIR/$APP_NAME.desktop"
 
if command -v update-desktop-database &>/dev/null; then
    update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true
fi
 
echo "✓ Paketsuche wurde deinstalliert."
echo "  (customtkinter wurde nicht entfernt, falls andere Programme es nutzen.)"
