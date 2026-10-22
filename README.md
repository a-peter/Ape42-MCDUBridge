# WinCtrl MCDU ↔ iniBuilds A330 (MSFS 2024)

Version 1.0.4. Die installierte Version zeigt `python mcdu_screen_bridge.py --version`.

Der Microsoft/iniBuilds A330 gibt den MCDU-Inhalt nicht nach außen. Deshalb liest
`mcdu_screen_bridge.py` den Bildschirm aus dem Pop-out-Fenster ab und schickt ihn über
MobiFlight an das WinCtrl-MCDU. Die **Tasten** laufen getrennt über ein MobiFlight-Profil,
z. B. „Inibuilds A330 WINWING/WINCTRL Mobiflight profile“ auf flightsim.to.

```
MSFS-Pop-out "WASMINSTRUMENT" ──Bildaufnahme──► mcdu_screen_bridge.py ──WebSocket──► MobiFlight ──USB──► MCDU
```

## Einrichtung

1. Python 3.10 oder neuer (64 bit) und die Pakete installieren:
   ```
   python -m pip install -r requirements.txt
   ```
2. MobiFlight Connector (ab Version 11) starten, das MCDU muss erkannt sein. **SimAppPro nicht gleichzeitig laufen lassen.**
3. Im A330 das Kapitäns-MCDU als Pop-out öffnen (Rechtsklick + Alt). Das Fenster heißt `WASMINSTRUMENT`.
   Es darf verdeckt oder auf einem anderen Monitor liegen, aber **nicht minimiert** sein, denn MSFS zeichnet ein
   minimiertes Pop-out nicht.

   Gefunden wird zuerst ein Fenster namens `WASMINSTRUMENT`, sonst ein MSFS-Fenster mit „MCDU“ im Titel (Kapitänsseite
   bevorzugt). Damit klappt auch der **MSFS Pop Out Panel Manager**, der das Fenster nach dem dort vergebenen Namen
   benennt, z. B. „MCDU Cpt (Custom)“. Ein anderer Titel lässt sich bei allen Befehlen fest vorgeben:
   ```
   python mcdu_screen_bridge.py run --window "MCDU Cpt (Custom)"
   ```
4. Bridge starten:
   ```
   python mcdu_screen_bridge.py run
   ```
   Optionen: `--learn` speichert unbekannte Zeichen in `unknown/`, `--interval 0.2` stellt die Sekunden zwischen zwei
   Aufnahmen ein, `--url` legt die MobiFlight-Adresse fest (Standard: `cdu-captain`).

   Alternativ per Doppelklick auf `start_bridge.bat` (startet `run --debug`).

   `run` läuft nur **einmal**: Eine zweite Instanz meldet „Die MCDU-Bridge läuft bereits“ und beendet sich mit
   Exit-Code 3. Das gilt auch beim direkten Start mit `python`. Andere Befehle wie `show` oder `snap` sind davon nicht betroffen.

5. Bridge beenden: Strg+C in ihrem Fenster, oder von außen per Doppelklick auf `stop_bridge.bat` bzw.
   ```
   python mcdu_screen_bridge.py stop
   ```
   `stop` sendet ein Stopp-Signal und wartet (Standard 10 s, `--timeout`), bis die Bridge sich sauber beendet und die
   MobiFlight-Verbindung geschlossen hat. Exit-Codes: 0 beendet, 4 es läuft keine Bridge, 5 Bridge reagiert nicht.

### Debug-Modus

```
python mcdu_screen_bridge.py run --debug
```

- Zeigt den erkannten Bildschirm farbig in der Konsole, in den MCDU-Farben. Unbekannte Zeichen erscheinen als weißes `?` auf Rot.
  Groß- und Kleinschrift lassen sich in der Konsole nicht unterscheiden.
- Neu gezeichnet wird nur, wenn sich etwas ändert. Dafür wird ein Hash über Zeichen und Farbe aller Zellen sowie den MobiFlight-Status gebildet.
  Vor jeder Ausgabe wird die Konsole gelöscht.
- Darunter stehen Uhrzeit, MobiFlight-Status, Zeichenzahl, unbekannte Zeichen und die Dauer der Erkennung.
- Läuft auch ohne MobiFlight. Die Bridge versucht dann alle 5 s im Hintergrund, sich zu verbinden.
- Benötigt eine Konsole mit Farbunterstützung (Windows Terminal oder Windows 10/11-Konsole).

Zur Geschwindigkeit: Eine Seite, die schon einmal erkannt wurde, braucht wegen des Zwischenspeichers nur noch wenige Millisekunden.
Beim ersten Erscheinen einer neuen Seite kann die Erkennung bis etwa 0,5 s dauern.

## So funktioniert die Erkennung

- Das Bild wird auf ein festes Maß gebracht und in 24×14 Zellen zerlegt. Wie groß das Pop-out-Fenster ist, spielt deshalb keine Rolle.
- Jede Zelle wird mit den Zeichenbildern in `glyphs/` verglichen. Dateinamen: `L_`/`S_` für groß/klein, dann der Unicode-Codepoint.
- Die Farbe ergibt sich aus Farbton und Sättigung (weiß, grau, grün, cyan, amber, gelb, magenta, rot).
- Unbekannte Zeichen erscheinen als `?`.

## Neue Zeichen einlernen

Stand 19.10.2026: 135 Zeichenbilder, alle Buchstaben A–Z und Ziffern 0–9 in groß und klein, dazu
`% ( ) * + - . / < = > [ ] ° ← ↑ → ↓ ☐ Δ`. Auf 3040 Zeichen aus 26 abgeschriebenen Seiten (u. a. F-PLN mit Route,
PERF TAKE OFF, FUEL PRED, IRS INIT) gab es 0 Fehler.

Nachträglich eingelernt: kleiner `*` (APPR „CONF3*“), `"` (Wiederholungszeichen in der F-PLN-WIND-Spalte) und ein echtes kleines Y.

**Achtung:** Das kleine **J** ist bisher nicht im Simulator vorgekommen. Es wurde mit
`synth J` aus dem großen verkleinert (Dateien `S_…_synth.png`). Das ist eine Notlösung. Sobald so ein Zeichen auf einer
Seite auftaucht, diese Seite abschreiben und per `teach` einlernen. Das echte Zeichenbild ersetzt dann automatisch das künstliche.
Mit `record --all` wird jede geänderte Seite gespeichert, auch wenn kein Zeichen unbekannt ist.

Ein Zeichen, das in der Sammlung ganz fehlt, wird **nicht** als `?` markiert, sondern dem ähnlichsten bekannten
zugeordnet. Ein fehlendes O würde z. B. als D erkannt. `record` findet deshalb nur Zeichen, die sich deutlich von allen
bekannten unterscheiden. Bei Zweifeln ist ein Abgleich mit `show` am zuverlässigsten.

1. `python mcdu_screen_bridge.py record --seconds 300` – speichert jede Seite mit unbekannten Zeichen als Bild in `snapshots/`.
2. Zu jedem Bild eine Textdatei mit gleichem Namen anlegen (`.txt`, UTF-8): 14 Zeilen, je Zeile der Text genau in seinen Spalten.
   Mit `python mcdu_screen_bridge.py show --image snapshots/<bild>.png` sieht man, welche Spalten belegt sind.
3. `python mcdu_screen_bridge.py teach snapshots/<bild>.txt --image snapshots/<bild>.png` – übernimmt die neuen Zeichen
   und meldet Abweichungen zwischen Abschrift und Bild.

Alternativ: `collect`, dann `sheet` (Übersichtsbild `unknown/_sheet.png`), dann `label 0=J 1=K …`.

## Bekannte Grenzen

- Die Geometrie ist auf das A330-MCDU abgestimmt (`COL0_CENTER`, `COL_PITCH`, `ROW0_BOTTOM`, `ROW_PITCH` im Skript).
- Nur die Kapitänsseite. Für die FO-Seite bräuchte es ein zweites Pop-out und die URL `…/cdu-co-pilot`; dafür ist die Fensterauswahl noch nicht ausgelegt.
- MobiFlight enthält eine Zuordnung `^airbus a330` → `xplane_default_fmc.py` (für X-Plane). Falls MobiFlight dieses Skript
  beim MSFS-A330 startet und das Display leert, den Eintrag in `Scripts\ScriptMappings.json` deaktivieren.

## probe_mcdu_export.py

Prüft, ob ein Flugzeug den MCDU-Inhalt per SimConnect-Client-Data exportiert, wie iniBuilds es beim A300 (`iniAirbusMCDU_1`)
und A340 (`A340MCDU`, `iniAirbusMCDU_2`) tut.

```
python probe_mcdu_export.py
python probe_mcdu_export.py --seconds 60 --names WeitererName
```

Ergebnis am 19.10.2026 mit dem A330-300P2F (RR): Keiner von 23 getesteten Namen existiert, darunter `MCDUA` und `MCDU_CPT`.
Die Gegenprobe mit einem selbst angelegten Bereich wurde erkannt, das Skript funktioniert also.
Bei neuen A330-Updates lohnt sich eine erneute Prüfung, vor allem wenn das EFB einen Punkt „External Hardware → MCDU Export“ bekommt.
Dann wäre eine Bridge ohne Bildschirmabgriff möglich, nach dem Muster von MobiFlights `ini_a340_winwing_cdu.py`.
