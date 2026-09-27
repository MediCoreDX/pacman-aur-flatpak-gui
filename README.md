🔍 Paketsuche – Pacman & AUR GUI

Eine moderne, schlichte grafische Oberfläche zum Durchsuchen, Installieren und Verwalten von Paketen auf Arch-basierten Systemen (entwickelt und getestet auf Manjaro) – sowohl aus den offiziellen Repositories (pacman) als auch aus dem AUR (yay). Kein Terminal-Wissen nötig, keine externe Konsole beim Installieren – alles läuft in einem sauberen, dunklen GUI-Fenster.

<!-- Badges -->

Inhaltsverzeichnis
Warum dieses Tool?
Features im Detail
Voraussetzungen
Installation
Nutzung
Wie es funktioniert
Deinstallation
Troubleshooting / FAQ
Roadmap
Mitwirken
Lizenz
Warum dieses Tool?

pacman und yay sind mächtig, aber rein kommandozeilenbasiert. Wer schnell mal ein Paket suchen, Details nachschlagen oder mehrere Pakete auf einen Schlag installieren möchte, ohne sich Flags zu merken, bekommt hier eine aufgeräumte grafische Alternative – ohne dabei die Kontrolle über den tatsächlich ausgeführten Befehl zu verlieren (jeder Befehl wird transparent angezeigt, bevor er läuft).

Features im Detail
🔎 Suche
Durchsucht offizielle Repos (pacman -Ss) und/oder das AUR (yay -Ssa) gleichzeitig, in parallelen Threads – keine Wartezeit durch sequenzielle Abfragen.
Progressive Anzeige: Repo-Treffer erscheinen sofort, AUR-Treffer werden nachgeladen, sobald sie da sind (AUR-Netzwerkabfragen sind meist der langsamere Teil).
Suche jederzeit abbrechbar, auch mitten im Lauf.
 Filtern & Sortieren
Anzeige-Filter: Alle / Nur installierte / Nur nicht installierte Pakete.
Quellen-Filter (unabhängig von der Suchquelle): Alle / Nur Repo / Nur AUR.
Sortierung: Name (A–Z / Z–A), Installiert zuerst, nach Quelle.
Kategorie-Erkennung

Jedes Paket bekommt automatisch ein Kategorie-Badge (z. B.  Python, Entwicklung, Gaming, KDE, GNOME, Schriftart, Bibliothek, ...) basierend auf Namensmustern – auf einen Blick erkennbar, worum es sich handelt.

Paket-Details

Klick auf „Details“ öffnet ein Fenster mit der vollständigen Ausgabe von pacman -Qi (installiert), pacman -Si (Repo, nicht installiert) oder yay -Si (AUR, nicht installiert) – Abhängigkeiten, Lizenz, Größe, Homepage etc.

Installieren /  Deinstallieren – live im GUI

Statt ein externes Terminal zu öffnen, läuft der Installations-/ Deinstallationsbefehl in einem eingebetteten Pseudo-Terminal:

Echtzeit-Ausgabe direkt im Fenster.
Fragt sudo nach dem Passwort, erscheint automatisch ein maskiertes Passwort-Dialogfeld.
Für sonstige interaktive Rückfragen (z. B. AUR-Bestätigungen, PKGBUILD editieren, Auswahl bei mehreren Anbietern) gibt es ein Eingabefeld, mit dem du direkt in den laufenden Prozess "hineintippen" kannst.
Jederzeit abbrechbar über einen eigenen „Abbrechen“-Button (killt den Prozess sauber).
Mehrfachauswahl & Sammelinstallation

Checkbox pro Paketkarte, eine Auswahl-Leiste erscheint automatisch, sobald mindestens ein Paket ausgewählt ist. „Ausgewählte installieren“ baut daraus einen kombinierten Befehl (pacman -S paket1 paket2 ... bzw. yay -S ...) und führt ihn live im GUI aus.

Updates prüfen

Ein Klick auf „Updates prüfen“ zeigt:

Ausstehende Repo-Updates via checkupdates (aus pacman-contrib, kein root nötig, greift nicht in die Paketdatenbank ein).
Ausstehende AUR-Updates via yay -Qua.

„Jetzt aktualisieren“ startet direkt yay -Syu (bzw. sudo pacman -Syu, falls yay fehlt) im Live-Ausgabe-Fenster.

Befehl kopieren

Jede Paketkarte zeigt den zugehörigen Terminal-Befehl an – per Klick landet er in der Zwischenablage, falls du ihn lieber selbst im Terminal ausführen möchtest.

Voraussetzungen
Komponente	Zweck	Pflicht?
Arch-basiertes System mit pacman	Repo-Suche & -Installation	Ja
yay	AUR-Suche, -Installation, System-Update	Optional (ohne yay: reine Repo-Funktionalität)
Python 3.10+	Laufzeitumgebung Ja
customtkinter	GUI-Toolkit	Ja (wird von install.sh installiert)
pacman-contrib (checkupdates)	Repo-Update-Check	Optional

Hinweis: Die Live-Ausgabe nutzt Pythons pty-Modul und ist damit auf Linux/Unix beschränkt (kein Windows/macOS-Support).

Installation
Option A – automatisches Install-Skript (empfohlen)
bash
git clone https://github.com/<DEIN-USERNAME>/<DEIN-REPO>.git
cd <DEIN-REPO>
./install.sh

Das Skript:

kopiert paketsuche.py nach ~/.local/bin/paketsuche (ausführbar),
installiert customtkinter, falls es fehlt (per yay oder pip --user),
legt einen .desktop-Eintrag in ~/.local/share/applications/ an, damit die App im Anwendungsmenü unter „Paketsuche“ erscheint,
prüft, ob ~/.local/bin im PATH liegt, und gibt bei Bedarf einen Hinweis aus.

Kein root erforderlich – die Installation ist rein benutzerbezogen.

Option B – manuell, ohne Installation
bash
pip install customtkinter --break-system-packages
python3 paketsuche.py
Nutzung
App über das Anwendungsmenü („Paketsuche“) oder per paketsuche im Terminal starten.
Suchbegriff eingeben, Enter drücken (oder auf „Suchen“ klicken).
Über die Steuerleiste Suchquelle, Anzeige-Filter, Quellen-Filter und Sortierung anpassen.
Pro Paket: Details ansehen, installieren/deinstallieren, oder Befehl kopieren.
Für mehrere Pakete gleichzeitig: Checkboxen anhaken → „Ausgewählte installieren“.
Regelmäßig auf „Updates prüfen“ klicken, um das System aktuell zu halten.
Wie es funktioniert
Suche: ruft pacman -Ss <begriff> und yay -Ssa <begriff> als Subprozesse auf und parst deren zweizeiliges Ausgabeformat.
Live-Installation: startet den Installationsbefehl über pty.fork() in einem eigenen Pseudo-Terminal, liest die Ausgabe zeichenweise im Hintergrund-Thread und zeigt sie live im Textfeld an. Erkennt sudo-Passwortabfragen an typischen Textmustern und öffnet dann automatisch ein maskiertes Eingabefeld; die Eingabe wird direkt in das Pseudo-Terminal geschrieben (Echo wird dabei von sudo selbst unterdrückt, das Passwort erscheint also nicht im Log).
Kategorien: einfache Muster-Erkennung per Regex auf Paketnamen (CATEGORY_RULES in paketsuche.py) – erweiterbar nach Bedarf.
Deinstallation
bash
./uninstall.sh

Entfernt die Programmdatei und den Menüeintrag. customtkinter bleibt installiert (falls andere Programme es nutzen).

Troubleshooting / FAQ

„customtkinter“ wird nicht gefunden → pip install customtkinter --break-system-packages ausführen, oder install.sh erneut starten.

Keine AUR-Ergebnisse → Prüfen, ob yay installiert ist (yay --version). Ohne yay funktioniert nur die Repo-Suche.

„Für Repo-Updates wird pacman-contrib benötigt“ → sudo pacman -S pacman-contrib installieren, dann liefert checkupdates Ergebnisse.

Das sudo-Passwortfenster erscheint nicht → Manche Sprachumgebungen/Locales verwenden andere Prompt-Texte als „password“/„Passwort“. In dem Fall kannst du das Passwort auch direkt in das Antwort-Eingabefeld im Live-Fenster tippen und mit Enter senden.

App startet, aber das Fenster ist leer/verzerrt → Sicherstellen, dass eine aktuelle customtkinter-Version installiert ist (pip install --upgrade customtkinter --break-system-packages).

Roadmap

Ideen für zukünftige Erweiterungen (Beiträge willkommen):

 Abhängigkeitsbaum-Visualisierung (pactree)
 Paket-Historie / kürzlich installierte Pakete
 Systemtray-Icon mit Update-Benachrichtigung
 AUR-Popularität/Votes über die AUR-RPC-API anzeigen
 Konfigurierbare Kategorie-Regeln über eine externe Datei

Issues und Pull Requests sind jederzeit willkommen:

Repository forken
Feature-Branch erstellen (git checkout -b feature/mein-feature)
Änderungen committen
Branch pushen und Pull Request öffnen

Medic0re.DX@proton.me
