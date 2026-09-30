#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
DATA_HOME="${XDG_DATA_HOME:-$HOME/.local/share}"
APP_DIR="$DATA_HOME/paketsuche"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="$DATA_HOME/applications"
APP_ENTRY="$APP_DIR/paketsuche.py"
PTY_RUNNER="$APP_DIR/pty_runner.py"
VENV_PYTHON="$APP_DIR/venv/bin/python"
LAUNCHER="$BIN_DIR/paketsuche"
DESKTOP_FILE="$DESKTOP_DIR/paketsuche.desktop"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3 wurde nicht gefunden. Bitte installiere Python 3.10 oder neuer." >&2
    exit 1
fi

if ! python3 -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
    echo "Paketsuche benötigt Python 3.10 oder neuer." >&2
    exit 1
fi

mkdir -p -- "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR"
install -m 644 "$SCRIPT_DIR/paketsuche.py" "$APP_ENTRY"
install -m 644 "$SCRIPT_DIR/pty_runner.py" "$PTY_RUNNER"

if [[ ! -x "$VENV_PYTHON" ]]; then
    python3 -m venv "$APP_DIR/venv"
fi
"$VENV_PYTHON" -m pip install --disable-pip-version-check \
    -r "$SCRIPT_DIR/requirements.txt"

printf '%s\n' \
    '#!/usr/bin/env bash' \
    "exec $(printf '%q' "$VENV_PYTHON") $(printf '%q' "$APP_ENTRY") \"\$@\"" \
    > "$LAUNCHER"
chmod 755 "$LAUNCHER"

python3 - "$DESKTOP_FILE" "$LAUNCHER" <<'PY'
import sys

desktop_file, launcher = sys.argv[1:]
escaped_launcher = (
    launcher.replace("\\", "\\\\")
    .replace('"', '\\"')
    .replace("`", "\\`")
    .replace("$", "\\$")
)
with open(desktop_file, "w", encoding="utf-8") as desktop:
    desktop.write(
        "[Desktop Entry]\n"
        "Version=1.0\n"
        "Type=Application\n"
        "Name=Paketsuche – Pacman & AUR\n"
        "GenericName=Package Manager\n"
        "Comment=Pakete in Pacman-Repositories und im AUR suchen und verwalten\n"
        f'Exec="{escaped_launcher}"\n'
        "Icon=system-software-install\n"
        "Terminal=false\n"
        "StartupNotify=true\n"
        "Categories=Settings;PackageManager;\n"
        "Keywords=Arch;Pacman;AUR;Paket;Package;\n"
    )
PY
chmod 644 "$DESKTOP_FILE"

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database "$DESKTOP_DIR"
fi

echo "Paketsuche wurde für diesen Benutzer installiert."
echo "Startmenü: Paketsuche – Pacman & AUR"
echo "Programm:  $LAUNCHER"
if [[ ":$PATH:" != *":$BIN_DIR:"* ]]; then
    echo "Hinweis: $BIN_DIR ist nicht in PATH; der Startmenü-Eintrag funktioniert trotzdem."
fi
