# Kontext für Claude Code: WinCtrl-MCDU-Bridge für den iniBuilds A330 (MSFS 2024)

Diese Datei fasst die bisherige Entwicklung zusammen, damit in einer neuen Sitzung ohne Vorwissen weitergearbeitet
werden kann. Sprache im Projekt: Deutsch (Kommentare, Ausgaben, README).

## Ziel

Ein WinCtrl-MCDU (baugleich WinWing, USB-HID) soll den Bildschirm des Kapitäns-MCDU im Microsoft/iniBuilds
**A330-300P2F (RR)** in MSFS 2024 anzeigen. Die Tasten laufen getrennt über ein MobiFlight-Profil (flightsim.to,
„Inibuilds A330 WINWING/WINCTRL Mobiflight profile“). Dieses Projekt liefert nur das **Display**.

## Warum Bildschirmabgriff

- Der A330 hat **keinen MCDU-Export**. Getestet mit `probe_mcdu_export.py` (SimConnect Client Data):
  `iniAirbusMCDU_1/2/3`, `iniAirbusMCDU`, `A330MCDU(_1/_2)`, `A330_MCDU`, `iniA330MCDU`, `A340MCDU`, `A300MCDU`,
  `iniMCDU_1/2`, `MCDU_1/2`, `MCDUA`, `MCDU_CPT`, `MCDU_FO` u. a. Alle liefern die Exception ILLEGAL_OPERATION,
  der Bereich existiert also nicht. Die Gegenprobe mit einem selbst angelegten Bereich wurde erkannt.
- iniBuilds exportiert nur beim A300 (`iniAirbusMCDU_1`) und A340 (`A340MCDU`, `iniAirbusMCDU_2`): 24×14 Zellen,
  3 Byte je Zelle (Zeichen, Farbe, Flags). Vorlage dafür: `ini_a340_winwing_cdu.py` im MobiFlight-Repo.
  Bekommt der A330 je einen Export (EFB „External Hardware → MCDU Export“), wäre das der bessere Weg.
- Windows-OCR (winrt) war zu ungenau („MCDU“ → `"CDU`). Deshalb wird jede Zelle mit eingelernten Vorlagen verglichen.

## Datenfluss

```
MSFS-Pop-out ──PrintWindow──► Normalisieren ──► 24×14 zerlegen ──► Vorlagenvergleich ──► JSON ──WebSocket──► MobiFlight ──USB──► MCDU
```

- **MobiFlight (ab v11)**: `ws://localhost:8320/winwing/cdu-captain` (auch `cdu-co-pilot`, `cdu-observer`).
  Zuerst `{"Target":"Font","Data":"AirbusThales"}`, dann `{"Target":"Display","Data":[336 Zellen]}`.
  Eine Zelle ist `[Zeichen, Farbcode, Größe 0=groß/1=klein]` oder `[]`. Farbcodes: `a w c g m r y o e k`.
  SimAppPro darf nicht parallel laufen.
- MobiFlight hat ein Mapping `^airbus a330` → `xplane_default_fmc.py` (für X-Plane). Falls das beim MSFS-A330 anspringt
  und das Display leert, in `Scripts\ScriptMappings.json` deaktivieren. Noch nicht beobachtet, nur ein Verdacht.

## Aufbau von `mcdu_screen_bridge.py` (Version in `__version__`)

- **Fenster** (`find_window`): Titel `WASMINSTRUMENT` (MSFS-Standard), sonst ein Fenster der Klasse `AceApp`
  mit „MCDU“ im Titel (Pop Out Panel Manager: „MCDU Cpt (Custom)“), Kapitänsseite bevorzugt. `--window` setzt den Titel fest.
- **Aufnahme** (`capture`): `PrintWindow(..., 3)` (PW_CLIENTONLY|PW_RENDERFULLCONTENT). Das funktioniert auch bei
  verdecktem Fenster, aber **nicht bei minimiertem**: MSFS zeichnet es dann nicht, es kommt nur Schwarz (getestet).
- **Normalisieren**: Displayfläche = Bereich mit Helligkeit ≥ 3, skaliert auf 582×522 (Referenzmaß aus einem Pop-out
  von ca. 598×568). Stark abweichende Fenstergrößen machen die Erkennung unsicherer.
- **Zerlegen** (`segment`): Zeile r hat ihre Unterkante bei `52.4 + r·34.4`, Spalte c ihre Mitte bei `33.5 + c·22.57`.
  Ausschnitt je Zeile 34 px hoch. Zusammenhängende Pixelgruppen (Union-Find über Pixelläufe, `components`) werden
  über ihre Mitte einer Spalte zugeordnet. Gruppen breiter als 1,3 Zellen (aneinanderstoßende Kästchen ☐) werden
  pixelweise verteilt, schmale Randstücke der Nachbarspalte zugeschlagen.
  - `EDGE = 3`: Gruppen ganz im oberen oder unteren Randstreifen verwerfen. Das sind Anschnitte der Nachbarzeile
    (Fehler „Fragezeichen unter EDDB“).
  - **Grundlinie**: Je Zeile wird die häufigste Unterkante hoher Zeichen auf y=30 geschoben, weil manche Seiten
    (F-PLN) Text einige Pixel höher setzen.
  - Hintergrund (Helligkeit 22) abziehen und normieren, damit graue und weiße Schrift gleich aussehen.
- **Vergleich** (`glyph_distance`): Anteil deutlich verschiedener Pixel (|Δ| > 0,5) an allen Schriftpixeln,
  Bestwert über ±1 px in x und y. Ab `MATCH_LIMIT = 0.12` gilt ein Zeichen als unbekannt (`?`).
  Ergebnisse werden je Zellform zwischengespeichert. Eine bekannte Seite kostet dann wenige ms,
  eine neue bis etwa 0,5 s.
  - Eine grobe Vorauswahl (2×2-Mittelung o. ä.) wurde ausprobiert und **verworfen**: Graue und weiße D/O liegen grob
    zu nah beieinander, das richtige Zeichen landete bis auf Rang 14.
  - **Wichtig:** Ein Zeichen, das ganz fehlt, wird meist dem ähnlichsten bekannten zugeordnet (fehlendes O → D),
    nicht als `?` markiert. Neue Zeichen erst sauber einlernen.
- **Größe**: kommt aus der Vorlage (`L_`/`S_`). Satzzeichen übernehmen die Größe vom nächsten Buchstaben oder der
  nächsten Ziffer derselben Zeile, bevorzugt in gleicher Farbe.
- **Farbe** (`nearest_color`): über HSV. Gemessen: weiß ohne Sättigung, Helligkeit 205–239; grau ohne Sättigung,
  Helligkeit 62–71; grün 120–123°; cyan 194–196°; amber 41–42°; magenta 300°. Gelb und rot kamen noch nicht vor.
- **Betrieb** (`cmd_run`): Die Erkennung läuft immer. Die MobiFlight-Verbindung wird im Hintergrund-Task aufgebaut,
  weil ein Versuch ohne laufendes MobiFlight unter Windows ca. 3 s blockiert. Neuer Versuch alle 5 s.
  Gesendet wird nur bei Änderung.
- **Nur eine Instanz** (`ensure_single_instance`): benannter Mutex `Local\WinCtrlMcduScreenBridge`, nur für `run`.
  Eine zweite Instanz endet mit Exit-Code 3, den `start_bridge.bat` gesondert auswertet. Die Batchdatei braucht
  CRLF-Zeilenenden (siehe `.gitattributes`), sonst springt `goto` unter Umständen falsch.
- **Beenden** (`stop`, `stop_bridge.bat`): `run` legt zusätzlich das Ereignis `Local\WinCtrlMcduScreenBridgeStop` an
  und prüft es in jedem Schleifendurchlauf. `stop` setzt es und wartet, bis der Mutex verschwunden ist.
  Exit-Codes 4 (keine Bridge) und 5 (Zeitüberschreitung). Kein hartes Beenden, keine PID-Dateien.
- Ohne `--debug` wird der MobiFlight-Status nur bei einem Wechsel ausgegeben, nicht bei jedem Verbindungsversuch.
- **Debug** (`run --debug`): Farbige Konsolenausgabe (ANSI 24 bit, VT-Modus per SetConsoleMode, stdout UTF-8).
  Neu gezeichnet wird nur, wenn sich der Hash über Zeichen+Farbe, den MobiFlight-Status oder den Fensterzustand ändert.

## Zeichensammlung `glyphs/`

- Dateiname `<L|S>_<Unicode-Codepoint hex>_<id>.png`, 24×34 Pixel Graustufen, aktuell 137 Bilder: A–Z und 0–9 in
  groß und klein sowie `" % ( ) * + - . / < = > [ ] ° ← ↑ → ↓ ☐ Δ`.
- `*_synth.png` sind aus großen Zeichen verkleinert (`synth`). Übrig ist nur noch das **kleine J**.
  `teach` ersetzt eine Nachbildung automatisch, sobald ein echtes Exemplar vorkommt.

## Einlernen und Prüfen

- `record [--all] --seconds N`: Speichert Seiten mit unbekannten Zeichen (oder jede geänderte Seite) in `snapshots/`.
- `show --image x.png`: Zeigt die Erkennung. Sie ist die beste Vorlage für die Abschrift.
- Abschrift `x.txt`: 14 Zeilen, spaltengenau, UTF-8 mit LF (siehe `.gitattributes`).
- `teach x.txt --image x.png`: Übernimmt neue Zeichenbilder (Größe bei Symbolen vom Nachbarbuchstaben) und meldet
  Abweichungen zwischen Bild und Abschrift.
- `snapshots/`: 29 Referenzseiten (Bild + Abschrift), Stand v1.0.3 mit **0 Fehlern bei 3509 Zeichen**.
  Prüfung nach Änderungen an Zerlegung oder Vergleich:

```python
import glob, numpy as np, mcdu_screen_bridge as m
from PIL import Image
lib = m.GlyphLibrary(); bad = tot = 0
for t in sorted(glob.glob("snapshots/*.txt")):
    lines = [(l + " " * 24)[:24] for l in (open(t, encoding="utf-8").read().splitlines() + [""] * 14)[:14]]
    grid, _ = m.recognize(lib, m.segment(np.asarray(Image.open(t[:-4] + ".png").convert("RGB")).astype(np.int16)))
    for r in range(14):
        for c in range(24):
            got = " " if grid[r][c] is None else grid[r][c][0]
            if got != " " or lines[r][c] != " ":
                tot += 1; bad += got != lines[r][c]
print("Fehler", bad, "von", tot)
```

## Weitergabe-Paket

`dist/winctrl_mcdu_bridge.zip` (nicht versioniert): Ordner `winctrl_mcdu_bridge/` mit `mcdu_screen_bridge.py`,
`README.md`, `glyphs/` und einem leeren `snapshots/`. Nach jeder Änderung am Skript neu bauen.

## Offene Punkte

- **Test mit echter Hardware und MobiFlight steht aus.** Bisher nur gegen einen nachgebauten WebSocket-Server geprüft.
- Kann die MCDU-Schrift „AirbusThales“ die Zeichen `"`, `Δ`, `☐` darstellen? Sonst vor dem Senden ersetzen.
- Kleines J ist nur nachgebildet. Gelb und rot sind ungetestet.
- Nur die Kapitänsseite. Für die FO-Seite bräuchte es ein zweites Fenster und eine zweite URL.
- Die Geometrie-Konstanten sind auf dieses Pop-out abgestimmt. Bei einem anderen Flugzeug neu vermessen.

## Konventionen

- Versionsnummer in `__version__` und in der Beschreibung oben im Skript, Git-Tag `vX.Y.Z`.
- Abhängigkeiten nur `numpy`, `pillow`, `websockets`; Python 3 64 bit; nur Windows (ctypes/user32/gdi32).
