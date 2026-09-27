#!/usr/bin/env python3
"""
Paketsuche - moderne GUI für Pacman & AUR (yay)
================================================
Funktionen:
  - Suche in offiziellen Repos (pacman) und/oder AUR (yay)
  - Filter (installiert/nicht installiert, Quelle) und Sortierung
  - Kategorie-Erkennung mit Icon je Paket
  - Paket-Details (pacman -Si/-Qi bzw. yay -Si)
  - Installieren / Deinstallieren mit LIVE-Ausgabe direkt im GUI
    (kein externes Terminal mehr nötig - inkl. grafischer Passwortabfrage)
  - Mehrfachauswahl + Sammelinstallation
  - Update-Check für Repo- (checkupdates) und AUR-Pakete (yay -Qua)
 
Abhängigkeiten:
    pip install customtkinter --break-system-packages
    sudo pacman -S pacman-contrib   # optional, für den Update-Check der Repos
 
Start:
    python3 paketsuche.py
 
Hinweis: Nutzt pty.fork() und ist damit auf Linux/Unix beschränkt.
"""
 
import os
import re
import pty
import signal
import shutil
import subprocess
import threading
import time
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
 
 
def guess_category(name):
    lname = name.lower()
    for pattern, icon, label in CATEGORY_RULES:
        if re.search(pattern, lname):
            return icon, label
    return "📦", "Sonstiges"
 
 
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
    def __init__(self, master, title, command_str):
        super().__init__(master)
        self.title(title)
        self.geometry("720x480")
 
        self.command_str = command_str
        self.child_pid = None
        self.master_fd = None
        self.awaiting_password = False
        self.finished = False
 
        self.protocol("WM_DELETE_WINDOW", self.on_close)
 
        info = ctk.CTkLabel(
            self, text=command_str, font=ctk.CTkFont(family="monospace", size=12),
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
            pid, fd = pty.fork()
        except OSError as e:
            self.after(0, self.append_text, f"[Fehler beim Starten: {e}]\n")
            return
 
        if pid == 0:
            # Kindprozess: ersetzt sich selbst durch bash -c "<command>"
            try:
                os.execvp("bash", ["bash", "-c", self.command_str])
            except Exception:
                os._exit(1)
            return
 
        self.child_pid = pid
        self.master_fd = fd
        tail = ""
 
        while True:
            try:
                data = os.read(fd, 1024)
            except OSError:
                break
            if not data:
                break
            text = data.decode(errors="replace")
            tail = (tail + text)[-200:]
            self.after(0, self.append_text, text)
            if not self.awaiting_password and re.search(r"password|passwort", tail, re.IGNORECASE):
                self.awaiting_password = True
                self.after(0, self.prompt_password)
 
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            _, status = os.waitpid(pid, 0)
            exit_code = os.WEXITSTATUS(status) if os.WIFEXITED(status) else -1
        except Exception:
            exit_code = -1
        self.after(0, self.on_finished, exit_code)
 
    def prompt_password(self):
        dialog = PasswordDialog(self)
        pwd = dialog.get_password()
        self.awaiting_password = False
        if pwd is not None and self.master_fd is not None:
            try:
                os.write(self.master_fd, (pwd + "\n").encode())
            except OSError:
                pass
 
    def send_input_from_entry(self):
        text = self.input_entry.get()
        if self.master_fd is not None:
            try:
                os.write(self.master_fd, (text + "\n").encode())
            except OSError:
                pass
        self.input_entry.delete(0, "end")
 
    def abort_process(self):
        if self.child_pid and not self.finished:
            try:
                os.kill(self.child_pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            self.append_text("\n[Abgebrochen]\n")
 
    def on_finished(self, exit_code):
        self.finished = True
        if exit_code == 0:
            self.status_label.configure(text="✓ Erfolgreich abgeschlossen.", text_color="#2ecc71")
        else:
            self.status_label.configure(text=f"✗ Beendet mit Code {exit_code}.", text_color="#e74c3c")
        self.abort_button.configure(state="disabled")
 
    def on_close(self):
        if not self.finished:
            self.abort_process()
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
        if pkg["installed"]:
            cmd = ["pacman", "-Qi", pkg["name"]]
        elif pkg["source"] == "repo":
            cmd = ["pacman", "-Si", pkg["name"]]
        else:
            cmd = ["yay", "-Si", pkg["name"]]
 
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=20)
            text = result.stdout.strip() or result.stderr.strip() or "Keine Details gefunden."
        except FileNotFoundError:
            text = f"Befehl '{cmd[0]}' wurde nicht gefunden."
        except Exception as e:
            text = f"Fehler beim Laden der Details: {e}"
 
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
        aur_updates = []
 
        if shutil.which("checkupdates"):
            try:
                out = subprocess.run(
                    ["checkupdates"], capture_output=True, text=True, timeout=30
                ).stdout
                repo_updates = [l for l in out.strip().splitlines() if l]
            except Exception:
                repo_updates = []
 
        if shutil.which("yay"):
            try:
                out = subprocess.run(
                    ["yay", "-Qua"], capture_output=True, text=True, timeout=30
                ).stdout
                aur_updates = [l for l in out.strip().splitlines() if l]
            except Exception:
                aur_updates = []
 
        self.after(0, self.show_updates, repo_updates, aur_updates)
 
    def show_updates(self, repo_updates, aur_updates):
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
 
        if aur_updates:
            lines.append(f"🏗  AUR-Updates ({len(aur_updates)}):")
            lines.extend(f"   {l}" for l in aur_updates)
        else:
            lines.append("🏗  Keine AUR-Updates verfügbar (oder yay ist nicht installiert).")
 
        self.textbox.configure(state="normal")
        self.textbox.delete("1.0", "end")
        self.textbox.insert("1.0", "\n".join(lines))
        self.textbox.configure(state="disabled")
 
        self.total_updates = (len(repo_updates) if repo_updates else 0) + len(aur_updates)
        if self.total_updates > 0:
            self.update_button.configure(state="normal")
            self.status_label.configure(text=f"{self.total_updates} Update(s) gefunden.")
        else:
            self.status_label.configure(text="System ist aktuell.")
 
    def run_update(self):
        cmd = "yay -Syu" if shutil.which("yay") else "sudo pacman -Syu"
        LiveOutputDialog(self.master_app, "System-Update", cmd)
        self.destroy()
 
 
# ----------------------------------------------------------------------
# Hauptanwendung
# ----------------------------------------------------------------------
 
class PackageSearchApp(ctk.CTk):
    def __init__(self):
        super().__init__()
        self.title("Paketsuche – Pacman & AUR")
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
            self, text="Durchsucht offizielle Repos (pacman) und das AUR (yay)",
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
            controls_frame, values=["Beide", "Nur Pacman", "Nur AUR (yay)"]
        )
        self.source_selector.set("Beide")
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
            values=["Alle Quellen", "Nur Repo", "Nur AUR"],
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
 
    # ---------- Suche ----------
 
    def start_search(self):
        query = self.search_entry.get().strip()
        if not query:
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
 
        # Welche Quellen wurden angefragt? Nur darauf warten wir.
        wants_repo = source in ("Beide", "Nur Pacman")
        wants_aur = source in ("Beide", "Nur AUR (yay)")
 
        self.pending_sources = set()
        if wants_repo:
            self.pending_sources.add("repo")
        if wants_aur:
            self.pending_sources.add("aur")
 
        if wants_repo:
            threading.Thread(
                target=lambda: self._search_and_report(
                    self.search_pacman, query, cancel_event, "repo"
                ), daemon=True
            ).start()
        if wants_aur:
            threading.Thread(
                target=lambda: self._search_and_report(
                    self.search_aur, query, cancel_event, "aur"
                ), daemon=True
            ).start()
 
    def _search_and_report(self, search_fn, query, cancel_event, source_key):
        """Führt eine Suche aus und meldet das Ergebnis sofort ans GUI,
        statt auf die jeweils andere (oft langsamere) Quelle zu warten."""
        results = search_fn(query, cancel_event)
        if cancel_event.is_set():
            return
        self.after(0, self.on_partial_results, source_key, results, query)
 
    def on_partial_results(self, source_key, results, query):
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
            waiting_for = "AUR" if "aur" in self.pending_sources else "Repos"
            self.status_label.configure(
                text=f"{len(combined)} Paket(e) bisher – warte noch auf {waiting_for} ..."
            )
        else:
            self.search_button.configure(state="normal", text="Suchen")
            self.cancel_button.configure(state="disabled")
            if combined:
                self.status_label.configure(text=f"{len(combined)} Paket(e) gefunden für „{query}“.")
            else:
                self.status_label.configure(text=f"Keine Pakete für „{query}“ gefunden.")
 
        self.render_results()
 
    def search_pacman(self, query, cancel_event):
        try:
            proc = subprocess.Popen(
                ["pacman", "-Ss", query],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if self.wait_or_cancel(proc, cancel_event):
                return []
            stdout, _ = proc.communicate()
            return self.parse_output(stdout, source="repo")
        except Exception:
            return []
 
    def search_aur(self, query, cancel_event):
        try:
            proc = subprocess.Popen(
                ["yay", "-Ssa", query],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True
            )
            if self.wait_or_cancel(proc, cancel_event):
                return []
            stdout, _ = proc.communicate()
            return self.parse_output(stdout, source="aur")
        except FileNotFoundError:
            return []
        except Exception:
            return []
 
    @staticmethod
    def wait_or_cancel(proc, cancel_event, timeout=30):
        elapsed = 0.0
        interval = 0.1
        while proc.poll() is None:
            if cancel_event.is_set():
                proc.terminate()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                return True
            time.sleep(interval)
            elapsed += interval
            if elapsed >= timeout:
                proc.kill()
                return True
        return False
 
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
 
        source_color = "#3b8ed0" if pkg["source"] == "repo" else "#9b59b6"
        source_text = pkg["repo"].upper() if pkg["source"] == "repo" else "AUR"
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
 
        action_cmd = (
            f"sudo pacman -Rns {pkg['name']}" if pkg["installed"]
            else (f"sudo pacman -S {pkg['name']}" if pkg["source"] == "repo" else f"yay -S {pkg['name']}")
        )
        ctk.CTkLabel(
            card, text=action_cmd, font=self.font_mono_small,
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
        copy_btn.configure(command=lambda c=action_cmd, b=copy_btn: self.copy_command(c, b))
 
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
        if not pkgs:
            return
        repo_names = [p["name"] for p in pkgs if p["source"] == "repo"]
        aur_names = [p["name"] for p in pkgs if p["source"] == "aur"]
 
        parts = []
        if repo_names:
            parts.append(f"sudo pacman -S {' '.join(repo_names)}")
        if aur_names:
            parts.append(f"yay -S {' '.join(aur_names)}")
        full_cmd = " && ".join(parts)
 
        LiveOutputDialog(self, f"Installiere {len(pkgs)} Paket(e)", full_cmd)
 
    # ---------- Aktionen ----------
 
    def copy_command(self, command, button):
        self.clipboard_clear()
        self.clipboard_append(command)
        self.update()
        original_text = "📋 Kopieren"
        button.configure(text="✓ Kopiert!")
        self.after(1500, lambda: button.configure(text=original_text))
 
    def install_package(self, pkg):
        cmd = f"sudo pacman -S {pkg['name']}" if pkg["source"] == "repo" else f"yay -S {pkg['name']}"
        LiveOutputDialog(self, f"Installiere {pkg['name']}", cmd)
 
    def uninstall_package(self, pkg):
        cmd = f"sudo pacman -Rns {pkg['name']}"
        LiveOutputDialog(self, f"Deinstalliere {pkg['name']}", cmd)
 
    def show_package_details(self, pkg):
        DetailDialog(self, pkg)
 
    def open_updates_dialog(self):
        UpdatesDialog(self)
 
 
if __name__ == "__main__":
    app = PackageSearchApp()
    app.mainloop()
 
