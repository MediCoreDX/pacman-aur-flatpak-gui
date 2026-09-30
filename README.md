# Paketsuche – Pacman & AUR

Eine grafische Paketverwaltung für Arch Linux und Arch-basierte Distributionen
wie Manjaro. Die App durchsucht offizielle Pacman-Repositories und optional das
AUR über `yay`. Sie verwendet `pacman` und `yay` als Unterprozesse; sie ersetzt
keinen Paketmanager und umgeht keine Systemberechtigungen.

## Funktionen

- Repo- und AUR-Suche parallel mit nachladenden Teilergebnissen
- Suche abbrechen, Ergebnisliste filtern, sortieren und seitenweise anzeigen
- Ergebnisliste mit Mausrad scrollen (Linux, Windows und macOS)
- Paketdetails für installierte und verfügbare Pakete
- Pakete einzeln oder gesammelt installieren sowie installierte Pakete entfernen
- Interaktive Paketmanager-Ausgabe einschließlich Terminal-Eingaben und
  maskierter `sudo`-Passwortabfrage direkt im Fenster
- Update-Prüfung mit `checkupdates` und `yay -Qua`
- System-Update über `yay -Syu` oder, falls `yay` fehlt, `sudo pacman -Syu`
- Paketkategorien und Kopierfunktion für Paketmanager-Befehle

## Voraussetzungen

| Komponente | Erforderlich für |
| --- | --- |
| Linux / Unix mit `pty.fork()` | App und interaktive Paketmanager-Ausgabe |
| Python 3.10 oder neuer | App |
| Pacman | Suche und Verwaltung offizieller Pakete |
| `customtkinter` | grafische Oberfläche; installiert durch `install.sh` |
| `yay` | AUR-Suche/-Pakete und komfortable System-Updates (optional) |
| `pacman-contrib` | Repo-Update-Prüfung mit `checkupdates` (optional) |

`pacman` und `yay` müssen im `PATH` liegen. Für Systemänderungen fragt
`sudo` nach den üblichen Rechten. Die App verlangt selbst keine Root-Rechte.

## Installation

```bash
git clone https://github.com/MediCoreDX/pacman-aur-gui.git
cd pacman-aur-gui
bash install.sh
```

Das Installationsskript richtet die App unter
`${XDG_DATA_HOME:-~/.local/share}/paketsuche` in einer eigenen Python-Umgebung
ein und legt einen Startmenü-Eintrag sowie `~/.local/bin/paketsuche` an. Es
installiert keine Systempakete und benötigt kein `sudo`. Für die
Python-Abhängigkeit ist beim ersten Installieren eine Internetverbindung nötig.

Danach kannst du **Paketsuche – Pacman & AUR** im Anwendungsmenü starten. Falls
`~/.local/bin` in deinem `PATH` liegt, geht es auch im Terminal:

```bash
paketsuche
```

### Manuell starten

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python paketsuche.py
```

## Verwendung

1. Suchbegriff eingeben und Enter drücken.
2. Bei Bedarf die Suchquelle, installierten Status, Quelle oder Sortierung
   einstellen.
3. Paketdetails ansehen oder einzelne Pakete installieren/deinstallieren.
4. Für eine Sammelinstallation Pakete markieren und **Ausgewählte installieren**
   anklicken.
5. Für Systemupdates **Updates prüfen** öffnen und anschließend bewusst
   **Jetzt aktualisieren** auswählen.

Paketmanager-Befehle laufen in einem eingebetteten Pseudo-Terminal. Ein kleiner
separater PTY-Relay-Prozess hält `forkpty()` aus dem mehrthreadigen GUI-Prozess
heraus. Ausgabe und Rückfragen werden angezeigt; bei Abbruch wird zunächst die
Prozessgruppe beendet und nach drei Sekunden nötigenfalls erzwungen beendet.
AUR-Builds können trotzdem eigene PKGBUILDs und Rückfragen enthalten. Prüfe die
Vorschläge des Paketmanagers und AUR-Build-Skripte sorgfältig.

## Sicherheit und Verhalten

- Externe Befehle werden mit Argumentlisten und ohne Shell-String-Ausführung
  gestartet.
- Paketnamen aus Suchergebnissen werden vor Installationsaktionen validiert.
- `sudo` wird nur für Pacman-Aktionen verwendet, die administrative Rechte
  benötigen. AUR-Aktionen laufen als normaler Benutzer über `yay`.
- Deinstallieren verwendet `pacman -Rns`; lies die angezeigte Paketmanager-
  Zusammenfassung und bestätige nur, wenn die vorgeschlagenen Änderungen passen.
- Die Update-Schaltfläche führt ein vollständiges Systemupgrade aus.
- Die AUR ist nutzergepflegt und nicht Teil der offiziellen Arch-Repositories.

## Updates und Deinstallation

Zum Aktualisieren den Repository-Ordner aktualisieren und das
Installationsskript erneut ausführen:

```bash
cd pacman-aur-gui
git pull --ff-only
bash install.sh
```

Zum Entfernen:

```bash
bash Uninstall.sh
```

Das Deinstallationsskript entfernt App, virtuelle Umgebung, Startmenü-Eintrag
und Launcher. Systempakete und andere Python-Installationen bleiben unverändert.

## Fehlerbehebung

- **`yay` nicht gefunden:** Repo-Suche bleibt verfügbar; installiere `yay`, um
  AUR-Funktionen zu nutzen.
- **Repo-Updates nicht verfügbar:** `checkupdates` ist Teil von
  `pacman-contrib`; installiere das Paket über deine Distribution.
- **App startet nicht:** Starte sie testweise im Terminal mit `paketsuche` oder
  `~/.local/share/paketsuche/venv/bin/python
  ~/.local/share/paketsuche/paketsuche.py`, um die Fehlermeldung zu sehen.
- **Abbruch bei AUR-Builds:** Ein Build oder ein Kindprozess kann je nach
  Paketmanager-Verhalten noch kurz zum Beenden brauchen.

## Entwicklung und Tests

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m unittest discover -v
```

Beiträge, Fehlerberichte und Verbesserungsvorschläge sind willkommen.
