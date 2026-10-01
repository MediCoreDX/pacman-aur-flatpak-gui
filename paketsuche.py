#!/usr/bin/env python3
"""
Paketsuche - moderne GUI für Pacman, AUR (yay) und Flatpak
================================================
Funktionen:
  - Suche in offiziellen Repos (pacman), AUR (yay) und Flatpak-Remotes
  - Filter (installiert/nicht installiert, Quelle) und Sortierung
  - Kategorie-Erkennung mit Icon je Paket
  - Paket-Details (pacman -Si/-Qi, yay -Si bzw. flatpak info)
  - Installieren / Deinstallieren mit LIVE-Ausgabe direkt im GUI
    (kein externes Terminal mehr nötig - inkl. grafischer Passwortabfrage)
  - Mehrfachauswahl + Sammelinstallation
  - Update-Check für Repo- (checkupdates), AUR- (yay -Qua) und Flatpak-Pakete
 
Abhängigkeiten:
    python3 -m venv .venv
    .venv/bin/python -m pip install -r requirements.txt
    sudo pacman -S pacman-contrib   # optional, für den Update-Check der Repos
 
Start:
    python3 paketsuche.py
 
Hinweis: Nutzt pty.fork() und ist damit auf Linux/Unix beschränkt.
"""
 
import errno
import json
import os
import re
import shlex
import pty
import signal
import shutil
import subprocess
import sys
import threading
import time
from tkinter import messagebox
import customtkinter as ctk
 
ctk.set_appearance_mode("dark")
ctk.set_default_color_theme("blue")
 
 
# ----------------------------------------------------------------------
# Kategorie-Erkennung
# ----------------------------------------------------------------------
 
CATEGORY_RULES = [
    (r"^(python2?)-", "🐍", "Python"),
    (r"^(nodejs|npm-|yarn)", "🟩", "Node.js"),
    (r"^(ttf-|otf-|font-|noto-)", "🔤", "Schriftart"),
    (r"(icon-theme|-icons$|^.*-theme$)", "🎨", "Theme"),
    (r"^(linux(-lts|-zen|-hardened)?)$", "🐧", "Kernel"),
    (r"(gimp|inkscape|blender|krita|kdenlive|audacity|obs-studio)", "🎬", "Multimedia"),
    (r"(vlc|mpv|ffmpeg)", "🎥", "Multimedia"),
    (r"(steam|lutris|wine|heroic|proton)", "🎮", "Gaming"),
    (r"(docker|podman|kubectl|^git$|^git-|gcc|clang|cmake|^make$)", "🛠️", "Entwicklung"),
    (r"(^code$|codium|neovim|^vim$|emacs|sublime|jetbrains)", "📝", "Editor"),
    (r"(firefox|chromium|brave|vivaldi|opera)", "🌐", "Browser"),
    (r"^gnome-|^gdm$", "🟣", "GNOME"),
    (r"^plasma-|^kde-|kwin|dolphin|konsole", "🔷", "KDE"),
    (r"^xfce4-|xfwm", "🐭", "XFCE"),
    (r"(systemd|networkmanager|pipewire|pulseaudio|bluez)", "⚙️", "System"),
    (r"^lib", "📚", "Bibliothek"),
]
 
# Einmal kompiliert statt bei jeder Suche neu (Performance)
PACMAN_LINE_RE = re.compile(r"^(\S+)/(\S+)\s+(\S+)(.*)$")
PACKAGE_NAME_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9@._+-]*")
 
 
def guess_category(name):
    lname = name.lower()
    for pattern, icon, label in CATEGORY_RULES:
        if re.search(pattern, lname):
            return icon, label
    return "📦", "Sonstiges"


def shell_join(command_parts):
    return " ".join(shlex.quote(str(part)) for part in command_parts)


def build_install_command(pkg):
    name = pkg["name"]
    if not PACKAGE_NAME_RE.fullmatch(name):
        raise ValueError(f"Ungültiger Paketname: {name!r}")

    if pkg["source"] == "flatpak":
        if pkg["installed"]:
            command = ["flatpak", "uninstall"]
            if pkg.get("installation") == "user":
                command.append("--user")
            return [*command, name]
        remote = pkg.get("repo", "")
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
            raise ValueError(f"Ungültige Flatpak-Gegenstelle: {remote!r}")
        return ["flatpak", "install", remote, name]
    if pkg["installed"]:
        return ["sudo", "pacman", "-Rns", name]
    if pkg["source"] == "repo":
        return ["sudo", "pacman", "-S", name]
    if pkg["source"] == "aur":
        return ["yay", "-S", name]
    raise ValueError(f"Unbekannte Paketquelle: {pkg['source']!r}")


def build_install_commands_for_selection(pkgs):
    repo_names = [p["name"] for p in pkgs if p["source"] == "repo"]
    aur_names = [p["name"] for p in pkgs if p["source"] == "aur"]
    flatpak_by_remote = {}
    for pkg in pkgs:
        if pkg["source"] == "flatpak":
            remote = pkg.get("repo", "")
            if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
                raise ValueError(f"Ungültige Flatpak-Gegenstelle: {remote!r}")
            flatpak_by_remote.setdefault(remote, []).append(pkg["name"])

    for name in repo_names + aur_names + [
        name for names in flatpak_by_remote.values() for name in names
    ]:
        if not PACKAGE_NAME_RE.fullmatch(name):
            raise ValueError(f"Ungültiger Paketname: {name!r}")

    commands = []
    if repo_names:
        commands.append(["sudo", "pacman", "-S", *repo_names])
    if aur_names:
        commands.append(["yay", "-S", *aur_names])
    for remote, names in flatpak_by_remote.items():
        commands.append(["flatpak", "install", remote, *names])
    return commands


def build_sequence_command(commands):
    commands = [list(command) for command in commands]
    if not commands:
        return ["true"]
    if len(commands) == 1:
        return commands[0]

    script = """
import json
import subprocess
import sys

commands = json.loads(sys.argv[1])
for command in commands:
    result = subprocess.run(command, check=False)
    if result.returncode != 0:
        raise SystemExit(result.returncode)
"""
    return [sys.executable, "-c", script, json.dumps(commands)]


def command_available(cmd):
    return shutil.which(cmd) is not None


def parse_flatpak_search_output(output, installed_apps):
    records = json.loads(output) if isinstance(output, str) else output
    if not isinstance(records, list):
        raise ValueError("Flatpak-Suchergebnis hat ein ungültiges Format.")

    results = []
    seen = set()
    for record in records:
        if not isinstance(record, dict):
            continue
        name = record.get("application_id") or record.get("application")
        if not isinstance(name, str) or not PACKAGE_NAME_RE.fullmatch(name):
            continue
        remotes = record.get("remotes", "")
        if isinstance(remotes, list):
            remotes = remotes[0] if remotes else ""
        if not isinstance(remotes, str):
            continue
        remote = remotes.split(",")[0].strip()
        if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", remote):
            continue
        if name in seen:
            continue
        seen.add(name)
        installed = installed_apps.get(name)
        results.append({
            "repo": remote,
            "name": name,
            "version": str(record.get("version", "")),
            "installed": installed is not None,
            "installation": installed.get("installation") if installed else None,
            "description": str(record.get("description", "")),
            "source": "flatpak",
        })
    return results


def parse_flatpak_updates(output):
    records = json.loads(output)
    if not isinstance(records, list):
        raise ValueError("Flatpak-Update-Ergebnis hat ein ungültiges Format.")
    updates = []
    for record in records:
        if not isinstance(record, dict):
            continue
        application_id = record.get("application_id") or record.get("application", "")
        name = record.get("name") or application_id
        version = record.get("version", "")
        branch = record.get("branch", "")
        origin = record.get("origin", "")
        details = " ".join(
            str(value) for value in (name, f"({application_id})", version, branch, origin)
            if value
        )
        if details:
            updates.append(details)
    return updates


def scroll_frame_with_mousewheel(scrollable_frame, event):
    if not scrollable_frame.check_if_master_is_canvas(event.widget):
        return None

    if getattr(event, "num", None) == 4:
        direction = -1
    elif getattr(event, "num", None) == 5:
        direction = 1
    elif getattr(event, "delta", 0):
        direction = -1 if event.delta > 0 else 1
    else:
        return None

    canvas = scrollable_frame._parent_canvas
    if canvas.yview() == (0.0, 1.0):
        return None
    canvas.yview_scroll(direction * 3, "units")
    return "break"


# ----------------------------------------------------------------------
# Passwort-Dialog (maskierte Eingabe für sudo)
# ----------------------------------------------------------------------
 
class PasswordDialog(ctk.CTkToplevel):
    def __init__(self, master):
        super().__init__(master)
        self.title("Authentifizierung erforderlich")
        self.geometry("360x160")
        self.resizable(False, False)
        self.result = None
        self.attributes("-topmost", True)
        self.grab_set()
 
        ctk.CTkLabel(self, text="🔒 sudo-Passwort eingeben:").pack(padx=20, pady=(22, 8))
        self.entry = ctk.CTkEntry(self, show="*", width=280)
        self.entry.pack(padx=20, pady=(0, 14))
        self.entry.bind("<Return>", lambda e: self.confirm())
        self.entry.focus()
 
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack()
        ctk.CTkButton(btn_row, text="OK", width=90, command=self.confirm).pack(side="left", padx=6)
        ctk.CTkButton(
            btn_row, text="Abbrechen", width=90, fg_color="#7f8c8d",
            hover_color="#636e72", command=self.cancel
        ).pack(side="left", padx=6)
 
    def confirm(self):
        self.result = self.entry.get()
        self.destroy()
 
    def cancel(self):
        self.result = None
        self.destroy()
 
    def get_password(self):
        self.wait_window()
        return self.result
 
 
# ----------------------------------------------------------------------
# Live-Ausgabe-Dialog für Installation/Deinstallation/Updates
# ----------------------------------------------------------------------
 
class LiveOutputDialog(ctk.CTkToplevel):
    def __init__(self, master, title, command):
        super().__init__(master)
        self.title(title)
        self.geometry("720x480")

        if not isinstance(command, (list, tuple)) or not command:
            raise TypeError("Befehle müssen als nicht-leere Argumentliste übergeben werden.")
        if isinstance(command[0], (list, tuple)):
            self.commands = [list(item) for item in command]
        else:
            self.commands = [list(command)]
        if any(not item or not all(isinstance(arg, str) for arg in item) for item in self.commands):
            raise ValueError("Jeder Befehl muss aus nicht-leeren Textargumenten bestehen.")

        self.command_display = " && ".join(shell_join(item) for item in self.commands)
        self.process = None
        self.master_fd = None
        self.process_lock = threading.Lock()
        self.fd_lock = threading.Lock()
        self.awaiting_password = False
        self.finished = False
        self.closing = False
        self.cancel_requested = threading.Event()

        self.protocol("WM_DELETE_WINDOW", self.on_close)

        info = ctk.CTkLabel(
            self, text=self.command_display, font=ctk.CTkFont(family="monospace", size=12),
            text_color="gray", anchor="w", wraplength=680, justify="left"
        )
        info.pack(fill="x", padx=14, pady=(14, 6))
 
        self.textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(family="monospace", size=12))
        self.textbox.pack(fill="both", expand=True, padx=14, pady=(0, 8))
        self.textbox.configure(state="disabled")
 
        input_row = ctk.CTkFrame(self, fg_color="transparent")
        input_row.pack(fill="x", padx=14, pady=(0, 8))
        input_row.grid_columnconfigure(0, weight=1)
 
        self.input_entry = ctk.CTkEntry(
            input_row, placeholder_text="Antwort eingeben (z. B. y / n / Enter) und senden..."
        )
        self.input_entry.grid(row=0, column=0, sticky="ew", padx=(0, 8))
        self.input_entry.bind("<Return>", lambda e: self.send_input_from_entry())
 
        ctk.CTkButton(
            input_row, text="Senden", width=90, command=self.send_input_from_entry
        ).grid(row=0, column=1)
 
        bottom_row = ctk.CTkFrame(self, fg_color="transparent")
        bottom_row.pack(fill="x", padx=14, pady=(0, 14))
        self.status_label = ctk.CTkLabel(bottom_row, text="Wird gestartet...", text_color="gray")
        self.status_label.pack(side="left")
        self.abort_button = ctk.CTkButton(
            bottom_row, text="Abbrechen", fg_color="#c0392b", hover_color="#962d22",
            width=100, command=self.abort_process
        )
        self.abort_button.pack(side="right")
 
        threading.Thread(target=self.run_process, daemon=True).start()
 
    def append_text(self, text):
        self.textbox.configure(state="normal")
        self.textbox.insert("end", text)
        self.textbox.see("end")
        self.textbox.configure(state="disabled")
 
    def run_process(self):
        try:
            command = build_sequence_command(self.commands)
            master_fd, slave_fd = pty.openpty()
        except (OSError, ValueError) as error:
            self.after(0, self.process_start_failed, str(error))
            return

        runner = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pty_runner.py")
        try:
            process = subprocess.Popen(
                [sys.executable, runner, json.dumps(command)],
                stdin=slave_fd,
                stdout=slave_fd,
                stderr=slave_fd,
                close_fds=True,
                start_new_session=True,
            )
        except (OSError, ValueError) as error:
            os.close(master_fd)
            self.after(0, self.process_start_failed, str(error))
            return
        finally:
            os.close(slave_fd)

        with self.process_lock:
            self.process = process
        if self.cancel_requested.is_set():
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
        with self.fd_lock:
            self.master_fd = master_fd
        tail = ""

        while True:
            try:
                data = os.read(master_fd, 1024)
            except OSError as error:
                if error.errno not in (errno.EIO, errno.EBADF):
                    self.after(0, self.append_text, f"\n[PTY-Fehler: {error}]\n")
                break
            if not data:
                break
            text = data.decode(errors="replace")
            tail = (tail + text)[-200:]
            self.after(0, self.append_text, text)
            if not self.awaiting_password and re.search(r"password|passwort", tail, re.IGNORECASE):
                self.awaiting_password = True
                self.after(0, self.prompt_password)
 
        with self.fd_lock:
            if self.master_fd == master_fd:
                self.master_fd = None
        os.close(master_fd)
        exit_code = process.wait()
        self.after(0, self.on_finished, exit_code)

    def process_start_failed(self, error):
        self.append_text(f"[Fehler beim Starten: {error}]\n")
        self.on_finished(127)

    def prompt_password(self):
        dialog = PasswordDialog(self)
        pwd = dialog.get_password()
        self.awaiting_password = False
        if pwd is not None:
            self.write_to_process(pwd + "\n")

    def send_input_from_entry(self):
        text = self.input_entry.get()
        self.write_to_process(text + "\n")
        self.input_entry.delete(0, "end")

    def write_to_process(self, text):
        with self.fd_lock:
            fd = self.master_fd
            if fd is None:
                return
            try:
                os.write(fd, text.encode())
            except OSError as error:
                self.append_text(f"\n[Eingabe konnte nicht gesendet werden: {error}]\n")

    def abort_process(self):
        self.cancel_requested.set()
        with self.process_lock:
            process = self.process
        if process is not None and process.poll() is None and not self.finished:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            if not self.closing:
                self.append_text("\n[Abbruch angefordert]\n")
            self.after(3000, self.force_abort_process, process.pid)

    def force_abort_process(self, process_id):
        if self.finished:
            return
        try:
            os.killpg(process_id, signal.SIGKILL)
        except ProcessLookupError:
            pass

    def on_finished(self, exit_code):
        self.finished = True
        if exit_code == 0:
            self.status_label.configure(text="✓ Erfolgreich abgeschlossen.", text_color="#2ecc71")
        else:
            self.status_label.configure(text=f"✗ Beendet mit Code {exit_code}.", text_color="#e74c3c")
        self.abort_button.configure(state="disabled")
        if self.closing:
            self.destroy()

    def on_close(self):
        if not self.finished:
            self.closing = True
            self.abort_process()
            self.status_label.configure(text="Prozess wird beendet …", text_color="gray")
            return
        self.destroy()
 
 
# ----------------------------------------------------------------------
# Paket-Detail-Dialog
# ----------------------------------------------------------------------
 
class DetailDialog(ctk.CTkToplevel):
    def __init__(self, master, pkg):
        super().__init__(master)
        self.pkg = pkg
        self.title(f"Details: {pkg['name']}")
        self.geometry("620x520")
 
        header = ctk.CTkLabel(
            self, text=f"{pkg['name']}  {pkg['version']}",
            font=ctk.CTkFont(size=18, weight="bold")
        )
        header.pack(padx=16, pady=(16, 4), anchor="w")
 
        self.textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(family="monospace", size=12))
        self.textbox.pack(fill="both", expand=True, padx=16, pady=(0, 16))
        self.textbox.insert("1.0", "Lade Details...")
        self.textbox.configure(state="disabled")
 
        threading.Thread(target=self.load_details, daemon=True).start()
 
    def load_details(self):
        pkg = self.pkg
        if pkg["source"] == "flatpak":
            if pkg["installed"]:
                cmd = ["flatpak", "info"]
                if pkg.get("installation") == "user":
                    cmd.append("--user")
                cmd.append(pkg["name"])
            else:
                cmd = ["flatpak", "remote-info", pkg["repo"], pkg["name"]]
        elif pkg["installed"]:
            cmd = ["pacman", "-Qi", pkg["name"]]
        elif pkg["source"] == "repo":
            cmd = ["pacman", "-Si", pkg["name"]]
        else:
            cmd = ["yay", "-Si", pkg["name"]]

        if not command_available(cmd[0]):
            text = f"Befehl '{cmd[0]}' wurde nicht gefunden."
            self.after(0, self.show_text, text)
            return

        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            output = result.stdout.strip() or result.stderr.strip()
            if result.returncode:
                text = output or f"Befehl wurde mit Exit-Code {result.returncode} beendet."
            else:
                text = output or "Keine Details gefunden."
        except FileNotFoundError:
            text = f"Befehl '{cmd[0]}' wurde nicht gefunden."
        except subprocess.TimeoutExpired:
            text = f"Zeitüberschreitung beim Laden der Details mit '{cmd[0]}'."
        except OSError as error:
            text = f"Fehler beim Laden der Details: {error}"

        self.after(0, self.show_text, text)
 
    def show_text(self, text):
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", text)
        self.textbox.configure(state="disabled")
 
 
# ----------------------------------------------------------------------
# Update-Dialog
# ----------------------------------------------------------------------
 
class UpdatesDialog(ctk.CTkToplevel):
    def __init__(self, master_app):
        super().__init__(master_app)
        self.master_app = master_app
        self.title("Verfügbare Updates")
        self.geometry("660x520")
 
        self.status_label = ctk.CTkLabel(self, text="Suche nach Updates...", text_color="gray")
        self.status_label.pack(padx=16, pady=(16, 6), anchor="w")
 
        self.textbox = ctk.CTkTextbox(self, font=ctk.CTkFont(family="monospace", size=12))
        self.textbox.pack(fill="both", expand=True, padx=16, pady=(0, 10))
        self.textbox.configure(state="disabled")
 
        btn_row = ctk.CTkFrame(self, fg_color="transparent")
        btn_row.pack(fill="x", padx=16, pady=(0, 16))
        self.update_button = ctk.CTkButton(
            btn_row, text="🔄 Jetzt aktualisieren", state="disabled",
            fg_color="#27ae60", hover_color="#219150", command=self.run_update
        )
        self.update_button.pack(side="left")
        ctk.CTkButton(
            btn_row, text="Schließen", fg_color="#7f8c8d", hover_color="#636e72",
            command=self.destroy
        ).pack(side="right")
 
        self.total_updates = 0
        threading.Thread(target=self.load_updates, daemon=True).start()
 
    def load_updates(self):
        repo_updates = None  # None = checkupdates nicht installiert
        aur_updates = None  # None = yay nicht installiert
        flatpak_updates = None  # None = flatpak nicht installiert
        errors = []

        if command_available("checkupdates"):
            try:
                result = subprocess.run(
                    ["checkupdates"], capture_output=True, text=True, timeout=30
                )
                if result.returncode not in (0, 2):
                    errors.append(
                        f"checkupdates fehlgeschlagen: "
                        f"{(result.stderr or result.stdout).strip() or result.returncode}"
                    )
                else:
                    repo_updates = [line for line in result.stdout.splitlines() if line]
            except (OSError, subprocess.TimeoutExpired) as error:
                repo_updates = []
                errors.append(f"Repo-Update-Prüfung fehlgeschlagen: {error}")

        if command_available("yay"):
            try:
                result = subprocess.run(
                    ["yay", "-Qua"], capture_output=True, text=True, timeout=30
                )
                if result.returncode:
                    errors.append(
                        f"yay -Qua fehlgeschlagen: "
                        f"{(result.stderr or result.stdout).strip() or result.returncode}"
                    )
                else:
                    aur_updates = [line for line in result.stdout.splitlines() if line]
            except (OSError, subprocess.TimeoutExpired) as error:
                aur_updates = []
                errors.append(f"AUR-Update-Prüfung fehlgeschlagen: {error}")

        if command_available("flatpak"):
            try:
                result = subprocess.run(
                    [
                        "flatpak", "remote-ls", "--updates", "--json",
                        "--columns=application,name,version,branch,origin",
                    ],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    env={**os.environ, "LC_ALL": "C"},
                )
                if result.returncode:
                    errors.append(
                        "Flatpak-Update-Prüfung fehlgeschlagen: "
                        f"{(result.stderr or result.stdout).strip() or result.returncode}"
                    )
                    flatpak_updates = []
                else:
                    flatpak_updates = parse_flatpak_updates(result.stdout)
            except (OSError, subprocess.TimeoutExpired, ValueError) as error:
                flatpak_updates = []
                errors.append(f"Flatpak-Update-Prüfung fehlgeschlagen: {error}")

        self.after(
            0, self.show_updates, repo_updates, aur_updates, flatpak_updates, errors
        )

    def show_updates(self, repo_updates, aur_updates, flatpak_updates, errors):
        lines = []
        if repo_updates is None:
            lines.append("ℹ️  Für Repo-Updates wird 'pacman-contrib' benötigt:")
            lines.append("    sudo pacman -S pacman-contrib")
        elif repo_updates:
            lines.append(f"📦 Repo-Updates ({len(repo_updates)}):")
            lines.extend(f"   {l}" for l in repo_updates)
        else:
            lines.append("📦 Keine Repo-Updates verfügbar.")

        lines.append("")

        if aur_updates is None:
            lines.append("🏗  AUR-Update-Prüfung nicht verfügbar (yay ist nicht installiert).")
        elif aur_updates:
            lines.append(f"🏗  AUR-Updates ({len(aur_updates)}):")
            lines.extend(f"   {l}" for l in aur_updates)
        else:
            lines.append("🏗  Keine AUR-Updates verfügbar.")

        lines.append("")

        if flatpak_updates is None:
            lines.append("📦 Flatpak-Update-Prüfung nicht verfügbar (flatpak fehlt).")
        elif flatpak_updates:
            lines.append(f"📦 Flatpak-Updates ({len(flatpak_updates)}):")
            lines.extend(f"   {line}" for line in flatpak_updates)
        else:
            lines.append("📦 Keine Flatpak-Updates verfügbar.")

        if errors:
            lines.append("")
            lines.append("⚠️  Fehler bei der Update-Prüfung:")
            lines.extend(f"   {error}" for error in errors)
 
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", "\n".join(lines))
        self.textbox.configure(state="disabled")
 
        self.total_updates = (len(repo_updates) if repo_updates else 0) + (
            len(aur_updates) if aur_updates else 0
        ) + (len(flatpak_updates) if flatpak_updates else 0)
        if self.total_updates > 0:
            self.update_button.configure(state="normal")
            self.status_label.configure(text=f"{self.total_updates} Update(s) gefunden.")
        elif errors:
            self.status_label.configure(text="Update-Prüfung mit Fehlern beendet.")
        elif repo_updates is None or aur_updates is None:
            self.status_label.configure(text="Update-Prüfung teilweise verfügbar.")
        else:
            self.status_label.configure(text="System ist aktuell.")
 
    def run_update(self):
        commands = [
            ["yay", "-Syu"] if shutil.which("yay") else ["sudo", "pacman", "-Syu"]
        ]
        if command_available("flatpak"):
            commands.append(["flatpak", "update"])
        self.master_app.run_package_commands("System-Update", commands)
        self.destroy()
 
 
# ----------------------------------------------------------------------
# Hauptanwendung
# ----------------------------------------------------------------------
 
class PackageSearchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Paketsuche – Pacman, AUR & Flatpak")
        self.geometry("1020x700")
        self.minsize(700, 480)
 
        self.all_results = []
        self.results_by_key = {}
        self.selected_keys = set()
        self.current_filtered = []
        self.render_limit = 40  # Anzahl gleichzeitig gerenderter Karten (Performance bei großen Trefferlisten)
        self.PAGE_SIZE = 40
 
        # Font-Objekte einmal anlegen und wiederverwenden statt pro Karte neu zu erzeugen (spürbar schneller)
        self.font_badge = ctk.CTkFont(size=11, weight="bold")
        self.font_cat = ctk.CTkFont(size=11)
        self.font_name = ctk.CTkFont(size=15, weight="bold")
        self.font_small = ctk.CTkFont(size=11)
        self.font_mono_small = ctk.CTkFont(family="monospace", size=11)
 
        self.grid_columnconfigure(0, weight=1)
        self.grid_rowconfigure(6, weight=1)
 
        # --- Header ---
        ctk.CTkLabel(
            self, text="🔍 Paketsuche", font=ctk.CTkFont(size=26, weight="bold")
        ).grid(row=0, column=0, padx=24, pady=(22, 4), sticky="w")
 
        ctk.CTkLabel(
            self, text="Durchsucht Pacman-Repos, das AUR (yay) und Flatpak-Remotes",
            text_color="gray", font=ctk.CTkFont(size=12)
        ).grid(row=1, column=0, padx=24, pady=(0, 14), sticky="w")
 
        # --- Suchleiste ---
        search_frame = ctk.CTkFrame(self, fg_color="transparent")
        search_frame.grid(row=2, column=0, padx=24, pady=(0, 8), sticky="ew")
        search_frame.grid_columnconfigure(0, weight=1)
 
        self.search_entry = ctk.CTkEntry(
            search_frame, placeholder_text="Paketname eingeben und Enter drücken...",
            height=42, font=ctk.CTkFont(size=14), corner_radius=10
        )
        self.search_entry.grid(row=0, column=0, sticky="ew", padx=(0, 10))
        self.search_entry.bind("<Return>", lambda e: self.start_search())
        self.search_entry.focus()
 
        self.search_button = ctk.CTkButton(
            search_frame, text="Suchen", width=110, height=42,
            corner_radius=10, command=self.start_search
        )
        self.search_button.grid(row=0, column=1, padx=(0, 10))
 
        self.cancel_button = ctk.CTkButton(
            search_frame, text="Abbrechen", width=110, height=42,
            corner_radius=10, fg_color="#7f8c8d", hover_color="#636e72",
            state="disabled", command=self.cancel_search
        )
        self.cancel_button.grid(row=0, column=2)
 
        # --- Steuerleiste: Suchquelle, Filter, Sortierung, Updates (sauber ausgerichtet) ---
        controls_frame = ctk.CTkFrame(self, fg_color="transparent")
        controls_frame.grid(row=3, column=0, padx=24, pady=(0, 10), sticky="ew")
        controls_frame.grid_columnconfigure(4, weight=1)  # Spacer, schiebt Updates-Button nach rechts
 
        LABEL_W = 110  # feste Breite für alle Beschriftungen -> Dropdowns beginnen alle an derselben X-Position
 
        ctk.CTkLabel(
            controls_frame, text="Suche in:", text_color="gray", width=LABEL_W, anchor="w"
        ).grid(row=0, column=0, sticky="w", pady=4)
        self.source_selector = ctk.CTkSegmentedButton(
            controls_frame,
            values=["Alle", "Nur Pacman", "Nur AUR (yay)", "Nur Flatpak"],
        )
        self.source_selector.set("Alle")
        self.source_selector.grid(row=0, column=1, columnspan=3, sticky="w", pady=4)
 
        ctk.CTkButton(
            controls_frame, text="🔄 Updates prüfen", width=150,
            command=self.open_updates_dialog
        ).grid(row=0, column=5, sticky="e", pady=4)
 
        ctk.CTkLabel(
            controls_frame, text="Anzeige:", text_color="gray", width=LABEL_W, anchor="w"
        ).grid(row=1, column=0, sticky="w", pady=4)
        self.filter_installed_menu = ctk.CTkOptionMenu(
            controls_frame, width=190,
            values=["Alle", "Nur installierte", "Nur nicht installierte"],
            command=self.on_controls_changed
        )
        self.filter_installed_menu.grid(row=1, column=1, sticky="w", padx=(0, 18), pady=4)
 
        ctk.CTkLabel(
            controls_frame, text="Quelle:", text_color="gray", width=70, anchor="w"
        ).grid(row=1, column=2, sticky="w", pady=4)
        self.filter_source_menu = ctk.CTkOptionMenu(
            controls_frame, width=150,
            values=["Alle Quellen", "Nur Repo", "Nur AUR", "Nur Flatpak"],
            command=self.on_controls_changed
        )
        self.filter_source_menu.grid(row=1, column=3, sticky="w", pady=4)
 
        ctk.CTkLabel(
            controls_frame, text="Sortierung:", text_color="gray", width=LABEL_W, anchor="w"
        ).grid(row=2, column=0, sticky="w", pady=4)
        self.sort_menu = ctk.CTkOptionMenu(
            controls_frame, width=190,
            values=["Name (A-Z)", "Name (Z-A)", "Installiert zuerst", "Quelle"],
            command=self.on_controls_changed
        )
        self.sort_menu.grid(row=2, column=1, sticky="w", pady=4)
 
        # --- Status ---
        self.status_label = ctk.CTkLabel(
            self, text="Bereit.", text_color="gray", anchor="w", font=ctk.CTkFont(size=12)
        )
        self.status_label.grid(row=4, column=0, padx=24, pady=(2, 4), sticky="new")
 
        # --- Auswahl-Werkzeugleiste (nur sichtbar bei Auswahl) ---
        self.selection_toolbar = ctk.CTkFrame(
            self, fg_color=("#dbeeff", "#1b3a4b"), corner_radius=8
        )
        self.selection_toolbar.grid(row=5, column=0, padx=24, pady=(0, 8), sticky="ew")
        self.selection_label = ctk.CTkLabel(self.selection_toolbar, text="0 Paket(e) ausgewählt")
        self.selection_label.pack(side="left", padx=12, pady=8)
        ctk.CTkButton(
            self.selection_toolbar, text="📦 Ausgewählte installieren",
            fg_color="#27ae60", hover_color="#219150", command=self.install_selected
        ).pack(side="right", padx=(6, 12), pady=8)
        ctk.CTkButton(
            self.selection_toolbar, text="Auswahl aufheben",
            fg_color="#7f8c8d", hover_color="#636e72", command=self.clear_selection
        ).pack(side="right", padx=6, pady=8)
        self.selection_toolbar.grid_remove()
 
        # --- Ergebnisliste ---
        self.results_frame = ctk.CTkScrollableFrame(self, label_text="", corner_radius=10)
        self.results_frame.grid(row=6, column=0, padx=24, pady=(0, 22), sticky="nsew")
        self.results_frame.grid_columnconfigure(0, weight=1)
        self.bind_all(
            "<Button-4>",
            lambda event: scroll_frame_with_mousewheel(self.results_frame, event),
            add="+",
        )
        self.bind_all(
            "<Button-5>",
            lambda event: scroll_frame_with_mousewheel(self.results_frame, event),
            add="+",
        )
 
    # ---------- Suche ----------
 
    def start_search(self):
        query = self.search_entry.get().strip()
        if not query:
            self.status_label.configure(text="Bitte einen Suchbegriff eingeben.")
            return
        if query.startswith("-"):
            self.status_label.configure(
                text="Suchbegriffe dürfen nicht mit einem Bindestrich beginnen."
            )
            return
        self.cancel_event = threading.Event()
        self.all_results = []
        self.results_by_key = {}
        self.selected_keys = set()
        self.render_limit = self.PAGE_SIZE
        self.search_button.configure(state="disabled", text="...")
        self.cancel_button.configure(state="normal")
        self.status_label.configure(text=f"Suche nach „{query}“ ...")
        self.clear_results()
        # run_search startet die Teilsuchen direkt selbst als Threads (kein extra Wrapper-Thread nötig)
        self.run_search(query, self.cancel_event)
 
    def cancel_search(self):
        if hasattr(self, "cancel_event"):
            self.cancel_event.set()
        self.status_label.configure(text="Suche abgebrochen.")
        self.cancel_button.configure(state="disabled")
        self.search_button.configure(state="normal", text="Suchen")
 
    def clear_results(self):
        for widget in self.results_frame.winfo_children():
            widget.destroy()
 
    def run_search(self, query, cancel_event):
        source = self.source_selector.get()

        wants_repo = source in ("Alle", "Nur Pacman")
        wants_aur = source in ("Alle", "Nur AUR (yay)")
        wants_flatpak = source in ("Alle", "Nur Flatpak")

        self.pending_sources = set()
        self.search_errors = []
        if wants_repo:
            self.pending_sources.add("repo")
        if wants_aur:
            self.pending_sources.add("aur")
        if wants_flatpak:
            self.pending_sources.add("flatpak")

        if wants_repo and not command_available("pacman"):
            self.after(
                0, self.on_partial_results, "repo", [], query,
                "pacman wurde nicht gefunden.",
            )
        if wants_aur and not command_available("yay"):
            self.after(
                0, self.on_partial_results, "aur", [], query,
                "yay wurde nicht gefunden; AUR-Suche ist nicht verfügbar.",
            )
        if wants_flatpak and not command_available("flatpak"):
            self.after(
                0, self.on_partial_results, "flatpak", [], query,
                "flatpak wurde nicht gefunden; Flatpak-Suche ist nicht verfügbar.",
            )

        if wants_repo and command_available("pacman"):
            threading.Thread(
                target=lambda: self._search_and_report(
                    self.search_pacman, query, cancel_event, "repo"
                ), daemon=True
            ).start()
        if wants_aur and command_available("yay"):
            threading.Thread(
                target=lambda: self._search_and_report(
                    self.search_aur, query, cancel_event, "aur"
                ), daemon=True
            ).start()
        if wants_flatpak and command_available("flatpak"):
            threading.Thread(
                target=lambda: self._search_and_report(
                    self.search_flatpak, query, cancel_event, "flatpak"
                ), daemon=True
            ).start()
 
    def _search_and_report(self, search_fn, query, cancel_event, source_key):
        """Führt eine Suche aus und meldet das Ergebnis sofort ans GUI,
        statt auf die jeweils andere (oft langsamere) Quelle zu warten."""
        results, error = search_fn(query, cancel_event)
        if cancel_event.is_set():
            return
        self.after(0, self.on_partial_results, source_key, results, query, error)

    def on_partial_results(self, source_key, results, query, error=None):
        if error:
            self.search_errors.append(error)
        # Ergebnisse dieser Quelle mit ggf. schon vorhandenen anderer Quelle zusammenführen
        others = [p for p in self.all_results if p["source"] != source_key]
        combined = others + results
        self.all_results = combined
        self.results_by_key = {f"{p['source']}:{p['name']}": p for p in combined}
        self.selected_keys &= set(self.results_by_key.keys())
        self.render_limit = self.PAGE_SIZE
 
        self.pending_sources.discard(source_key)
        still_loading = bool(self.pending_sources)
 
        if still_loading:
            waiting_for = next(
                label for key, label in (
                    ("repo", "Repos"), ("aur", "AUR"), ("flatpak", "Flatpak")
                )
                if key in self.pending_sources
            )
            self.status_label.configure(
                text=f"{len(combined)} Paket(e) bisher – warte noch auf {waiting_for} ..."
            )
        else:
            self.search_button.configure(state="normal", text="Suchen")
            self.cancel_button.configure(state="disabled")
            if combined:
                status = f"{len(combined)} Paket(e) gefunden für „{query}“."
            else:
                status = f"Keine Pakete für „{query}“ gefunden."
            if self.search_errors:
                warning = " ".join(dict.fromkeys(self.search_errors))
                status = f"{status} Hinweis: {warning}"
            self.status_label.configure(text=status)

        self.render_results()

    def search_pacman(self, query, cancel_event):
        return self.search_command(["pacman", "-Ss", query], cancel_event, "repo")

    def search_aur(self, query, cancel_event):
        return self.search_command(["yay", "-Ssa", query], cancel_event, "aur")

    def search_flatpak(self, query, cancel_event):
        command = [
            "flatpak", "search", "--json",
            "--columns=application,name,description,version,branch,remotes",
            query,
        ]
        results, error = self.search_command(command, cancel_event, "flatpak")
        if error or cancel_event.is_set():
            return results, error

        try:
            installed_result = subprocess.run(
                [
                    "flatpak", "list", "--app", "--json",
                    "--columns=application,name,version,origin,installation",
                ],
                capture_output=True,
                text=True,
                timeout=30,
                env={**os.environ, "LC_ALL": "C"},
            )
            if installed_result.returncode:
                detail = (
                    installed_result.stderr or installed_result.stdout
                ).strip() or installed_result.returncode
                return [], f"flatpak list fehlgeschlagen: {detail}"
            installed_records = json.loads(installed_result.stdout)
            if not isinstance(installed_records, list):
                raise ValueError("flatpak list lieferte ein ungültiges JSON-Format.")
            installed_apps = {}
            for record in installed_records:
                if not isinstance(record, dict):
                    continue
                app_id = record.get("application_id") or record.get("application")
                if not isinstance(app_id, str):
                    continue
                existing = installed_apps.get(app_id)
                # Prefer system for a duplicate ID; user installs remain supported.
                if existing is None or (
                    existing.get("installation") != "system"
                    and record.get("installation") == "system"
                ):
                    installed_apps[app_id] = record
            return parse_flatpak_search_output(results, installed_apps), None
        except (OSError, subprocess.TimeoutExpired, ValueError, json.JSONDecodeError) as error:
            return [], f"Flatpak-Installationen konnten nicht geprüft werden: {error}"

    @classmethod
    def search_command(cls, command, cancel_event, source, timeout=30):
        try:
            proc = subprocess.Popen(
                command,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                start_new_session=True,
                env={**os.environ, "LC_ALL": "C"} if source == "flatpak" else None,
            )
        except OSError as error:
            return [], f"{command[0]} konnte nicht gestartet werden: {error}"

        deadline = time.monotonic() + timeout
        while True:
            if cancel_event.is_set():
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                try:
                    proc.communicate(timeout=2)
                except subprocess.TimeoutExpired:
                    try:
                        os.killpg(proc.pid, signal.SIGKILL)
                    except ProcessLookupError:
                        pass
                    proc.communicate()
                return [], None

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                proc.communicate()
                return [], f"{command[0]} hat das Zeitlimit von {timeout} Sekunden überschritten."

            try:
                output, _ = proc.communicate(timeout=min(0.2, remaining))
                if proc.returncode:
                    detail = output.strip().splitlines()
                    reason = detail[-1] if detail else f"Exit-Code {proc.returncode}"
                    return [], f"{command[0]} fehlgeschlagen: {reason}"
                if source == "flatpak":
                    if not output.strip():
                        return [], None
                    try:
                        return json.loads(output), None
                    except json.JSONDecodeError as error:
                        return [], f"flatpak search lieferte ungültiges JSON: {error}"
                return cls.parse_output(output, source=source), None
            except subprocess.TimeoutExpired:
                continue
 
    @staticmethod
    def parse_output(output, source):
        results = []
        lines = output.splitlines()
        i = 0
        while i < len(lines):
            line = lines[i]
            if line and not line.startswith(" "):
                m = PACMAN_LINE_RE.match(line)
                if m:
                    repo, name, version, extra = m.groups()
                    installed = "[installed]" in extra
                    desc = ""
                    if i + 1 < len(lines) and lines[i + 1].startswith(" "):
                        desc = lines[i + 1].strip()
                        i += 1
                    results.append({
                        "repo": repo,
                        "name": name,
                        "version": version,
                        "installed": installed,
                        "description": desc,
                        "source": source,
                    })
            i += 1
        return results
 
    # ---------- Anzeige, Filter, Sortierung ----------
 
    def on_controls_changed(self, _value=None):
        self.render_limit = self.PAGE_SIZE  # Filter/Sortierung geändert -> Paginierung zurücksetzen
        self.render_results()
 
    def render_results(self):
        self.clear_results()
        filtered = list(self.all_results)
 
        inst_filter = self.filter_installed_menu.get()
        if inst_filter == "Nur installierte":
            filtered = [p for p in filtered if p["installed"]]
        elif inst_filter == "Nur nicht installierte":
            filtered = [p for p in filtered if not p["installed"]]
 
        src_filter = self.filter_source_menu.get()
        if src_filter == "Nur Repo":
            filtered = [p for p in filtered if p["source"] == "repo"]
        elif src_filter == "Nur AUR":
            filtered = [p for p in filtered if p["source"] == "aur"]
        elif src_filter == "Nur Flatpak":
            filtered = [p for p in filtered if p["source"] == "flatpak"]
 
        sort_mode = self.sort_menu.get()
        if sort_mode == "Name (A-Z)":
            filtered.sort(key=lambda p: p["name"].lower())
        elif sort_mode == "Name (Z-A)":
            filtered.sort(key=lambda p: p["name"].lower(), reverse=True)
        elif sort_mode == "Installiert zuerst":
            filtered.sort(key=lambda p: (not p["installed"], p["name"].lower()))
        elif sort_mode == "Quelle":
            filtered.sort(key=lambda p: (p["source"], p["name"].lower()))
 
        self.current_filtered = filtered
 
        if not filtered:
            if self.all_results:
                text = "Keine Ergebnisse für die aktuellen Filter."
            else:
                text = "Noch keine Suche durchgeführt."
            ctk.CTkLabel(self.results_frame, text=text, text_color="gray").pack(pady=40)
        else:
            # Nur einen begrenzten Ausschnitt rendern -> GUI bleibt bei sehr breiten
            # Suchbegriffen (z. B. "lib") flüssig statt hunderte Karten auf einmal zu bauen.
            visible = filtered[: self.render_limit]
            for pkg in visible:
                self.add_result_card(pkg)
 
            remaining = len(filtered) - len(visible)
            if remaining > 0:
                more_frame = ctk.CTkFrame(self.results_frame, fg_color="transparent")
                more_frame.pack(fill="x", pady=10)
                ctk.CTkLabel(
                    more_frame, text=f"{len(visible)} von {len(filtered)} angezeigt",
                    text_color="gray"
                ).pack(side="left", padx=(2, 12))
                ctk.CTkButton(
                    more_frame, text=f"Mehr laden (+{min(self.PAGE_SIZE, remaining)})",
                    width=160, command=self.load_more
                ).pack(side="left")
 
        self.update_selection_toolbar()
 
    def load_more(self):
        self.render_limit += self.PAGE_SIZE
        self.render_results()
 
    def add_result_card(self, pkg):
        key = f"{pkg['source']}:{pkg['name']}"
 
        card = ctk.CTkFrame(self.results_frame, corner_radius=10)
        card.pack(fill="x", pady=5, padx=2)
 
        top_row = ctk.CTkFrame(card, fg_color="transparent")
        top_row.pack(fill="x", padx=12, pady=(10, 0))
 
        var = ctk.BooleanVar(value=(key in self.selected_keys))
        ctk.CTkCheckBox(
            top_row, text="", variable=var, width=20, checkbox_width=20, checkbox_height=20,
            command=lambda k=key, v=var: self.toggle_selection(k, v)
        ).pack(side="left", padx=(0, 10))
 
        source_color = {
            "repo": "#3b8ed0",
            "aur": "#9b59b6",
            "flatpak": "#2c9c69",
        }[pkg["source"]]
        source_text = (
            pkg["repo"].upper() if pkg["source"] in ("repo", "flatpak") else "AUR"
        )
        ctk.CTkLabel(
            top_row, text=source_text, fg_color=source_color, corner_radius=6,
            width=55, height=22, font=self.font_badge
        ).pack(side="left", padx=(0, 6))
 
        icon, cat_label = guess_category(pkg["name"])
        ctk.CTkLabel(
            top_row, text=f"{icon} {cat_label}", fg_color=("#dcdcdc", "#3a3a3a"),
            corner_radius=6, font=self.font_cat
        ).pack(side="left", padx=(0, 10))
 
        ctk.CTkLabel(
            top_row, text=f"{pkg['name']}   {pkg['version']}",
            font=self.font_name, anchor="w"
        ).pack(side="left", fill="x", expand=True)
 
        if pkg["installed"]:
            ctk.CTkLabel(
                top_row, text="✓ installiert", text_color="#2ecc71", font=self.font_small
            ).pack(side="right")
 
        ctk.CTkLabel(
            card, text=pkg["description"] or "Keine Beschreibung.",
            text_color="gray", anchor="w", justify="left", wraplength=700
        ).pack(fill="x", padx=12, pady=(4, 6))
 
        action_cmd = build_install_command(pkg)
        action_display = shell_join(action_cmd)
        ctk.CTkLabel(
            card, text=action_display, font=self.font_mono_small,
            text_color="gray", anchor="w"
        ).pack(fill="x", padx=12, pady=(0, 6))
 
        button_row = ctk.CTkFrame(card, fg_color="transparent")
        button_row.pack(fill="x", padx=12, pady=(0, 10))
 
        ACTION_BTN_W = 150  # einheitliche Breite -> Kopieren-Button sitzt bei jeder Karte an gleicher Stelle
 
        ctk.CTkButton(
            button_row, text="ℹ Details", width=100, height=26, corner_radius=6,
            font=self.font_small, fg_color="#5b6cff", hover_color="#4a58d9",
            command=lambda p=pkg: self.show_package_details(p)
        ).pack(side="left", padx=(0, 6))
 
        if pkg["installed"]:
            ctk.CTkButton(
                button_row, text="🗑 Deinstallieren", width=ACTION_BTN_W, height=26, corner_radius=6,
                font=self.font_small, fg_color="#c0392b", hover_color="#962d22",
                command=lambda p=pkg: self.uninstall_package(p)
            ).pack(side="left", padx=(0, 6))
        else:
            ctk.CTkButton(
                button_row, text="⬇ Installieren", width=ACTION_BTN_W, height=26, corner_radius=6,
                font=self.font_small, fg_color="#27ae60", hover_color="#219150",
                command=lambda p=pkg: self.install_package(p)
            ).pack(side="left", padx=(0, 6))
 
        copy_btn = ctk.CTkButton(
            button_row, text="📋 Kopieren", width=100, height=26, corner_radius=6,
            font=self.font_small
        )
        copy_btn.pack(side="left")
        copy_btn.configure(command=lambda c=action_display, b=copy_btn: self.copy_command(c, b))
 
    # ---------- Mehrfachauswahl ----------
 
    def toggle_selection(self, key, var):
        if var.get():
            self.selected_keys.add(key)
        else:
            self.selected_keys.discard(key)
        self.update_selection_toolbar()
 
    def update_selection_toolbar(self):
        count = len(self.selected_keys)
        if count > 0:
            self.selection_toolbar.grid()
            self.selection_label.configure(text=f"{count} Paket(e) ausgewählt")
        else:
            self.selection_toolbar.grid_remove()
 
    def clear_selection(self):
        self.selected_keys.clear()
        self.render_results()
 
    def install_selected(self):
        pkgs = [self.results_by_key[k] for k in self.selected_keys if k in self.results_by_key]
        pkgs = [pkg for pkg in pkgs if not pkg["installed"]]
        if not pkgs:
            return

        commands = build_install_commands_for_selection(pkgs)
        if not commands:
            return

        self.run_package_commands(
            f"Installiere {len(pkgs)} Paket(e)",
            commands,
        )

    def run_package_commands(self, title, commands):
        missing = sorted({
            command[0]
            for command in commands
            if not command_available(command[0])
        })
        if missing:
            messagebox.showerror(
                "Befehl nicht gefunden",
                "Folgende benötigte Programme fehlen:\n" + "\n".join(missing),
                parent=self,
            )
            return
        LiveOutputDialog(self, title, commands)
 
    # ---------- Aktionen ----------
 
    def copy_command(self, command, button):
        self.clipboard_clear()
        self.clipboard_append(command)
        self.update()
        original_text = "📋 Kopieren"
        button.configure(text="✓ Kopiert!")
        self.after(1500, lambda: button.configure(text=original_text))
 
    def install_package(self, pkg):
        cmd = build_install_command(pkg)
        self.run_package_commands(f"Installiere {pkg['name']}", [cmd])

    def uninstall_package(self, pkg):
        cmd = build_install_command(pkg)
        self.run_package_commands(f"Deinstalliere {pkg['name']}", [cmd])
 
    def show_package_details(self, pkg):
        DetailDialog(self, pkg)
 
    def open_updates_dialog(self):
        UpdatesDialog(self)
 
 
if __name__ == "__main__":
    app = PackageSearchApp()
    app.mainloop()
 
