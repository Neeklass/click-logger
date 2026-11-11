"""
klick_logger.py – Zeichnet Klicks und Eingaben unter Windows als Markdown-Anleitung auf.
Keine Screenshots, nur Text: welches Fenster, welches Element, welche Aktion.

Installation:
    pip install pynput uiautomation

Start:
    python klick_logger.py --titel "Rechnung in DATEV erfassen"
    python klick_logger.py --titel "VPN einrichten" --text      (Texteingaben inhaltlich mitschreiben)
    python klick_logger.py --out C:\\Doku\\vpn.md

Hotkeys während der Aufnahme (in HOTKEYS unten anpassbar):
    Strg+Umschalt+F7   Notiz/Hinweis einfügen
    Strg+Umschalt+F8   Pause / Fortsetzen
    Strg+Umschalt+F9   Aufnahme beenden   (alternativ Strg+C im Konsolenfenster)

Die Markdown-Datei wird nach jedem Schritt neu geschrieben, es geht also nichts verloren,
falls das Skript abstürzt.
"""

import argparse
import ctypes
import datetime as dt
import queue
import threading
import time
from pathlib import Path

import tkinter as tk
from tkinter import simpledialog

import uiautomation as auto
from pynput import keyboard, mouse

# ---------------------------------------------------------------- Konfiguration

HOTKEYS = {
    keyboard.Key.f7: "note",
    keyboard.Key.f8: "pause",
    keyboard.Key.f9: "stop",
}  # jeweils mit Strg+Umschalt

DOPPELKLICK_SEKUNDEN = 0.5
MAX_NAMENSLAENGE = 60

TYPEN = {
    "Button": "Schaltfläche",
    "SplitButton": "Schaltfläche",
    "MenuItem": "Menüpunkt",
    "MenuBar": "Menüleiste",
    "Menu": "Menü",
    "Edit": "Textfeld",
    "Document": "Dokument",
    "TabItem": "Registerkarte",
    "Tab": "Registerkarten",
    "ListItem": "Listeneintrag",
    "List": "Liste",
    "TreeItem": "Baumeintrag",
    "Tree": "Baumansicht",
    "CheckBox": "Kontrollkästchen",
    "RadioButton": "Optionsfeld",
    "ComboBox": "Auswahlliste",
    "Hyperlink": "Link",
    "Text": "Text",
    "Image": "Bild",
    "Pane": "Bereich",
    "Window": "Fenster",
    "Group": "Gruppe",
    "DataItem": "Tabelleneintrag",
    "DataGrid": "Tabelle",
    "Table": "Tabelle",
    "HeaderItem": "Spaltenkopf",
    "Header": "Kopfzeile",
    "ToolBar": "Symbolleiste",
    "StatusBar": "Statusleiste",
    "TitleBar": "Titelleiste",
    "Slider": "Schieberegler",
    "Spinner": "Drehfeld",
    "ScrollBar": "Bildlaufleiste",
    "Thumb": "Bildlauf",
    "ToolTip": "Tooltip",
    "Custom": "Element",
}

SONDERTASTEN = {
    keyboard.Key.enter: "Enter",
    keyboard.Key.tab: "Tab",
    keyboard.Key.esc: "Esc",
    keyboard.Key.delete: "Entf",
    keyboard.Key.home: "Pos1",
    keyboard.Key.end: "Ende",
    keyboard.Key.page_up: "Bild↑",
    keyboard.Key.page_down: "Bild↓",
    keyboard.Key.insert: "Einfg",
}
SONDERTASTEN.update({getattr(keyboard.Key, f"f{i}"): f"F{i}" for i in range(1, 13)})

PFEILE = {
    keyboard.Key.up: "↑",
    keyboard.Key.down: "↓",
    keyboard.Key.left: "←",
    keyboard.Key.right: "→",
}

STRG = {keyboard.Key.ctrl, keyboard.Key.ctrl_l, keyboard.Key.ctrl_r}
ALT = {keyboard.Key.alt, keyboard.Key.alt_l, keyboard.Key.alt_r}
ALTGR = {keyboard.Key.alt_gr}
UMSCHALT = {keyboard.Key.shift, keyboard.Key.shift_l, keyboard.Key.shift_r}
WIN = {keyboard.Key.cmd, keyboard.Key.cmd_l, keyboard.Key.cmd_r}
MODIFIKATOREN = STRG | ALT | ALTGR | UMSCHALT | WIN

KLICKARTEN = {
    mouse.Button.left: "Linksklick",
    mouse.Button.right: "Rechtsklick",
    mouse.Button.middle: "Mittelklick",
}

# ---------------------------------------------------------------- Hilfsfunktionen


def md(text: str) -> str:
    """Text einzeilig, gekürzt und Markdown-sicher machen."""
    text = " ".join((text or "").split())
    if len(text) > MAX_NAMENSLAENGE:
        text = text[: MAX_NAMENSLAENGE - 1] + "…"
    for zeichen in "\\*_`|[]<>#":
        text = text.replace(zeichen, "\\" + zeichen)
    return text


def typname(ctrl) -> str:
    roh = (ctrl.ControlTypeName or "").replace("Control", "")
    return TYPEN.get(roh, roh or "Element")


def element_info(ctrl) -> dict:
    """Name, Typ und Fenster eines UI-Elements ermitteln.
    Hat das Element selbst keinen Namen, wird bis zu 3 Ebenen nach oben gesucht."""
    info = {"name": "", "typ": "Element", "fenster": "", "hwnd": 0, "passwort": False}
    if ctrl is None:
        return info
    try:
        info["typ"] = typname(ctrl)
        info["passwort"] = bool(getattr(ctrl, "IsPassword", False))

        kandidat, ebene = ctrl, 0
        while kandidat is not None and ebene <= 3:
            name = (kandidat.Name or "").strip() or (kandidat.AutomationId or "").strip()
            if name:
                info["name"] = name
                if ebene > 0:
                    info["typ"] = typname(kandidat)
                break
            kandidat, ebene = kandidat.GetParentControl(), ebene + 1

        top = ctrl.GetTopLevelControl()
        if top is not None:
            info["fenster"] = (top.Name or "").strip()
            info["hwnd"] = top.NativeWindowHandle
    except Exception:
        pass
    return info


def beschreibe(info: dict, praefix: str) -> str:
    if info["name"]:
        return f"{praefix} auf **{md(info['name'])}** ({info['typ']})"
    return f"{praefix} in {info['typ']}"


# ---------------------------------------------------------------- Rekorder


class Rekorder:
    def __init__(self, titel: str, ausgabe: Path, text_mitschreiben: bool):
        self.titel = titel
        self.ausgabe = ausgabe
        self.text_mitschreiben = text_mitschreiben
        self.start = dt.datetime.now()

        self.events: "queue.Queue[tuple]" = queue.Queue()
        self.stumm = threading.Event()  # gesetzt während Notiz-Dialog
        self.pausiert = False
        self.gedrueckt: set = set()

        self.schritte: list[dict] = []
        self.puffer = ""
        self.puffer_ziel: dict | None = None
        self.letzter_klick = None  # (button, element_key, zeit)

        try:
            self.konsole_hwnd = ctypes.windll.kernel32.GetConsoleWindow()
        except Exception:
            self.konsole_hwnd = 0

        self.tk_root = tk.Tk()
        self.tk_root.withdraw()

    # ---------- Listener (laufen in eigenen Threads, nur Events einreihen)

    def on_click(self, x, y, button, pressed):
        if pressed and not self.stumm.is_set():
            self.events.put(("click", x, y, button, time.time()))

    def on_press(self, key):
        if self.stumm.is_set():
            return
        if key in MODIFIKATOREN:
            self.gedrueckt.add(key)
            return

        strg = bool(self.gedrueckt & STRG)
        alt = bool(self.gedrueckt & ALT)
        altgr = bool(self.gedrueckt & ALTGR)
        umschalt = bool(self.gedrueckt & UMSCHALT)
        win = bool(self.gedrueckt & WIN)

        # Eigene Hotkeys
        if strg and umschalt and not alt and key in HOTKEYS:
            self.events.put((HOTKEYS[key],))
            return

        if key == keyboard.Key.backspace:
            self.events.put(("backspace",))
            return

        # AltGr (auf deutschen Tastaturen = Strg+Alt) erzeugt normale Zeichen wie @ € {
        ist_kombi = (strg or alt or win) and not (altgr or (strg and alt))

        if isinstance(key, keyboard.KeyCode):
            if ist_kombi:
                self.events.put(("key", self.kombi_name(key, strg, alt, umschalt, win)))
            elif key.char and key.char.isprintable():
                self.events.put(("char", key.char))
            return

        if key == keyboard.Key.space and not ist_kombi:
            self.events.put(("char", " "))
        elif key in SONDERTASTEN or key in PFEILE or ist_kombi:
            self.events.put(("key", self.kombi_name(key, strg, alt, umschalt, win)))

    def on_release(self, key):
        self.gedrueckt.discard(key)

    @staticmethod
    def kombi_name(key, strg, alt, umschalt, win) -> str:
        teile = []
        if strg:
            teile.append("Strg")
        if alt:
            teile.append("Alt")
        if umschalt:
            teile.append("Umschalt")
        if win:
            teile.append("Win")

        if isinstance(key, keyboard.KeyCode):
            vk = getattr(key, "vk", None)
            if vk and (0x30 <= vk <= 0x39 or 0x41 <= vk <= 0x5A):
                teile.append(chr(vk))
            elif key.char and key.char.isprintable():
                teile.append(key.char.upper())
            else:
                teile.append("?")
        else:
            teile.append(SONDERTASTEN.get(key) or PFEILE.get(key) or key.name.capitalize())
        return "+".join(teile)

    # ---------- Verarbeitung (Hauptthread, hier läuft UI Automation)

    def schritt(self, fenster: str, text: str, **meta):
        self.schritte.append({"art": "schritt", "fenster": fenster, "text": text, **meta})
        self.speichern()

    def puffer_leeren(self):
        if not self.puffer:
            return
        ziel = self.puffer_ziel or element_info(None)
        feld = f" in **{md(ziel['name'])}** ({ziel['typ']})" if ziel["name"] else ""
        if ziel["passwort"]:
            text = f"Passwort eingeben{feld}"
        elif self.text_mitschreiben:
            eingabe = self.puffer.replace("`", "'")
            text = f"Eingabe{feld}: `{eingabe}`"
        else:
            text = f"Text eingeben{feld}"
        self.puffer, self.puffer_ziel = "", None
        self.schritt(ziel["fenster"], text)

    def verarbeite_klick(self, x, y, button, zeit):
        try:
            ctrl = auto.ControlFromPoint(x, y)
        except Exception:
            ctrl = None
        info = element_info(ctrl)

        if self.konsole_hwnd and info["hwnd"] == self.konsole_hwnd:
            return  # Klicks in die eigene Konsole ignorieren

        self.puffer_leeren()
        key = (info["fenster"], info["name"], info["typ"])

        if (
            button == mouse.Button.left
            and self.letzter_klick
            and self.letzter_klick[0] == button
            and self.letzter_klick[1] == key
            and zeit - self.letzter_klick[2] < DOPPELKLICK_SEKUNDEN
            and self.schritte
            and self.schritte[-1].get("klick")
        ):
            self.schritte[-1]["text"] = beschreibe(info, "Doppelklick")
            self.schritte[-1]["klick"] = False
            self.letzter_klick = None
            self.speichern()
            return

        self.letzter_klick = (button, key, zeit)
        art = KLICKARTEN.get(button, "Klick")
        self.schritt(info["fenster"], beschreibe(info, art), klick=True)

    def verarbeite_zeichen(self, zeichen):
        if not self.puffer:
            try:
                self.puffer_ziel = element_info(auto.GetFocusedControl())
            except Exception:
                self.puffer_ziel = element_info(None)
        self.puffer += zeichen

    def verarbeite_taste(self, name):
        self.puffer_leeren()
        fenster = ""
        try:
            fenster = element_info(auto.GetForegroundControl())["fenster"]
        except Exception:
            pass
        # Wiederholte Pfeiltasten zusammenfassen: "Taste ↓ (3×)"
        if self.schritte and self.schritte[-1].get("taste") == name:
            letzter = self.schritte[-1]
            letzter["anzahl"] += 1
            letzter["text"] = f"Taste **{name}** ({letzter['anzahl']}×)"
            self.speichern()
            return
        self.schritt(fenster, f"Taste **{name}**", taste=name, anzahl=1)

    def notiz(self):
        self.puffer_leeren()
        self.stumm.set()
        try:
            self.tk_root.attributes("-topmost", True)
            text = simpledialog.askstring("Notiz", "Hinweis für die Anleitung:", parent=self.tk_root)
        finally:
            self.gedrueckt.clear()
            self.stumm.clear()
        if text and text.strip():
            self.schritte.append({"art": "notiz", "text": text.strip()})
            self.speichern()
            print("  Notiz eingefügt.")

    # ---------- Ausgabe

    def speichern(self):
        z = [
            f"# {self.titel}",
            "",
            f"*Aufgezeichnet am {self.start:%d.%m.%Y um %H:%M} Uhr*",
            "",
        ]
        aktuelles_fenster, nr = None, 0
        for s in self.schritte:
            if s["art"] == "notiz":
                z += ["", f"> **Hinweis:** {s['text']}", ""]
                continue
            if s["fenster"] != aktuelles_fenster:
                aktuelles_fenster = s["fenster"]
                z += ["", f"## {md(aktuelles_fenster) or 'Unbekanntes Fenster'}", ""]
            nr += 1
            z.append(f"{nr}. {s['text']}")
        self.ausgabe.write_text("\n".join(z).replace("\n\n\n", "\n\n") + "\n", encoding="utf-8")

    # ---------- Hauptschleife

    def run(self):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # korrekte Koordinaten bei Skalierung
        except Exception:
            pass

        self.speichern()
        print(f"Aufnahme läuft → {self.ausgabe.resolve()}")
        print("  Strg+Umschalt+F7 = Notiz | F8 = Pause | F9 = Beenden (jeweils mit Strg+Umschalt)")

        m = mouse.Listener(on_click=self.on_click)
        k = keyboard.Listener(on_press=self.on_press, on_release=self.on_release)
        m.start()
        k.start()

        try:
            with auto.UIAutomationInitializerInThread():
                while True:
                    try:
                        ev = self.events.get(timeout=0.2)
                    except queue.Empty:
                        continue

                    typ = ev[0]
                    if typ == "stop":
                        break
                    if typ == "pause":
                        self.puffer_leeren()
                        self.pausiert = not self.pausiert
                        print("  ⏸ Pausiert." if self.pausiert else "  ▶ Fortgesetzt.")
                        continue
                    if self.pausiert:
                        continue

                    if typ == "click":
                        self.verarbeite_klick(*ev[1:])
                    elif typ == "char":
                        self.verarbeite_zeichen(ev[1])
                    elif typ == "backspace":
                        self.puffer = self.puffer[:-1]
                    elif typ == "key":
                        self.verarbeite_taste(ev[1])
                    elif typ == "note":
                        self.notiz()
        except KeyboardInterrupt:
            pass
        finally:
            m.stop()
            k.stop()
            self.puffer_leeren()
            self.speichern()
            anzahl = sum(1 for s in self.schritte if s["art"] == "schritt")
            print(f"Fertig: {anzahl} Schritte gespeichert in {self.ausgabe.resolve()}")


def main():
    parser = argparse.ArgumentParser(description="Klicks und Eingaben als Markdown-Anleitung aufzeichnen.")
    parser.add_argument("--titel", default="Anleitung", help="Überschrift der Anleitung")
    parser.add_argument("--out", type=Path, help="Zieldatei (.md)")
    parser.add_argument(
        "--text",
        action="store_true",
        help="Eingegebenen Text inhaltlich mitschreiben (Passwortfelder werden nie mitgeschrieben)",
    )
    args = parser.parse_args()

    ausgabe = args.out or Path(f"Aufzeichnung_{dt.datetime.now():%Y-%m-%d_%H%M}.md")
    Rekorder(args.titel, ausgabe, args.text).run()


if __name__ == "__main__":
    main()
