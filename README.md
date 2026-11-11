# click-logger

Zeichnet Klicks und Tastatureingaben unter Windows auf und schreibt daraus eine
nummerierte **Markdown-Anleitung** – ohne Screenshots. Gedacht als Text-Ersatz für
den abgekündigten Windows Steps Recorder (psr.exe).

## Installation

```bash
pip install -r requirements.txt
```

## Nutzung

```bash
python klick_logger.py --titel "Rechnung in DATEV erfassen"
python klick_logger.py --titel "VPN einrichten" --out C:\Doku\vpn.md
python klick_logger.py --titel "Formular ausfüllen" --text   # Eingaben wörtlich mitschreiben
```

| Hotkey | Aktion |
|---|---|
| Strg+Umschalt+F7 | Notiz/Hinweis einfügen |
| Strg+Umschalt+F8 | Pause / Fortsetzen |
| Strg+Umschalt+F9 | Aufnahme beenden (alternativ Strg+C in der Konsole) |

## Beispielausgabe

```markdown
# Rechnung in DATEV erfassen

*Aufgezeichnet am 11.11.2026 um 08:45 Uhr*

## DATEV Unternehmen online

1. Linksklick auf **Belege** (Menüpunkt)
2. Linksklick auf **Neuer Beleg** (Schaltfläche)
3. Text eingeben in **Rechnungsnummer** (Textfeld)
4. Taste **Tab**

> **Hinweis:** Kostenstelle immer aus der Projektliste nehmen
```

## Funktionsweise

- `pynput` fängt Maus- und Tastatur-Events global ab.
- `uiautomation` (Windows UI Automation) liefert Name, Typ und Fenster des angeklickten Elements.
- Fensterwechsel erzeugen eine neue `##`-Überschrift.
- Getippter Text wird zu einem Schritt zusammengefasst; Passwortfelder werden nie mitgeschrieben.
- Doppelklicks und wiederholte Tasten werden zusammengefasst.
- Die Datei wird nach jedem Schritt gespeichert.

## Grenzen

- Qualität der Namen hängt von der App ab (ältere Fachanwendungen liefern oft nur „Bereich“).
- Programme mit Adminrechten erfordern, dass auch das Skript als Admin läuft.
- Globale Keyboard-Hooks können von Virenscannern/Firmen-Policies gemeldet werden.
