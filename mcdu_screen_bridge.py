"""
Bridge: MCDU-Pop-out des iniBuilds A330 (MSFS 2024) -> WinCtrl/WinWing MCDU über MobiFlight.
Version 1.0.2

Der A330 exportiert seinen MCDU-Inhalt nicht (siehe probe_mcdu_export.py). Deshalb wird
das Pop-out-Fenster ("WASMINSTRUMENT") abfotografiert, in das 24x14-Zeichenraster zerlegt,
jede Zelle mit einer Sammlung bekannter Zeichenbilder (glyphs/) verglichen und das Ergebnis
im MobiFlight-Format an ws://localhost:8320/winwing/cdu-captain geschickt.

Befehle:
  python mcdu_screen_bridge.py run        # Bridge betreiben
  python mcdu_screen_bridge.py show       # einmal erkennen und als Text ausgeben
  python mcdu_screen_bridge.py record     # Seiten mit unbekannten Zeichen als Snapshot speichern
  python mcdu_screen_bridge.py teach snapshots/x.txt --image snapshots/x.png   # Zeichen per Abschrift lernen
  python mcdu_screen_bridge.py collect    # unbekannte Zeichen nach unknown/ sammeln
  python mcdu_screen_bridge.py sheet      # Übersichtsbild unknown/_sheet.png zum Benennen
  python mcdu_screen_bridge.py label 3=A 7=←   # unbekannte Zeichen benennen (Nummern aus dem Übersichtsbild)
"""

__version__ = "1.0.2"

import argparse
import asyncio
import ctypes
import hashlib
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

HERE = Path(__file__).resolve().parent
GLYPH_DIR = HERE / "glyphs"
UNKNOWN_DIR = HERE / "unknown"

WINDOW_TITLE = "WASMINSTRUMENT"           # Standardname des MSFS-Pop-outs
WINDOW_OVERRIDE = None                    # fester Fenstertitel per --window
MOBIFLIGHT_URL = "ws://localhost:8320/winwing/cdu-captain"

COLS, ROWS = 24, 14

# Geometrie im Referenzmaß: Pop-out-Inhalt (dunkle Displayfläche) auf 582x522 Pixel skaliert.
REF_W, REF_H = 582, 522
COL0_CENTER, COL_PITCH = 33.5, 22.57      # Mitte der Spalte 0, Spaltenabstand
ROW0_BOTTOM, ROW_PITCH = 52.4, 34.4       # Unterkante (Grundlinie) der Zeile 0, Zeilenabstand
CELL_W, CELL_H = 24, 34                   # Ausschnitt pro Zeichen
BASELINE = 30                             # Zeile der Unterkante im Ausschnitt
EDGE = 3                                  # Randstreifen oben/unten im Zeilenausschnitt (Anschnitte der Nachbarzeilen)
INK = 40                                  # Helligkeit ab der ein Pixel als Schrift gilt
BACKGROUND = 22                           # Helligkeit des Displayhintergrunds (inkl. Rauschen)
SMALL_MAX_HEIGHT = 23                     # Große Schrift ist ~27 px hoch, kleine ~20 px
MATCH_LIMIT = 0.12                        # Abweichung (glyph_distance), bis zu der ein Zeichen als erkannt gilt
SAME_LIMIT = 0.05                         # darunter gilt ein Zeichenbild als bereits bekannt


# ---------------------------------------------------------------- Fenster aufnehmen

user32, gdi32 = ctypes.windll.user32, ctypes.windll.gdi32
user32.SetProcessDPIAware()


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [("biSize", wintypes.DWORD), ("biWidth", wintypes.LONG), ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD), ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG), ("biClrUsed", wintypes.DWORD), ("biClrImportant", wintypes.DWORD)]


def find_window(title=None):
    """Pop-out-Fenster suchen. Mit festem Titel (--window) nur genau diesen. Sonst zuerst das
    MSFS-Standardfenster "WASMINSTRUMENT", ersatzweise ein MSFS-Fenster mit "MCDU" im Titel
    (z.B. "MCDU Cpt (Custom)" aus dem MSFS Pop Out Panel Manager); Kapitänsseite bevorzugt."""
    title = title or WINDOW_OVERRIDE
    windows = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            buf = ctypes.create_unicode_buffer(n + 1)
            user32.GetWindowTextW(hwnd, buf, n + 1)
            cls = ctypes.create_unicode_buffer(64)
            user32.GetClassNameW(hwnd, cls, 64)
            windows.append((hwnd, buf.value, cls.value))
        return True

    user32.EnumWindows(cb, 0)
    if title:
        return next((h for h, t, _ in windows if t.upper() == title.upper()), None)
    exact = [h for h, t, _ in windows if t.upper() == WINDOW_TITLE]
    if exact:
        return exact[0]
    # MSFS-Fenster haben die Klasse "AceApp"
    mcdu = [(h, t.upper()) for h, t, c in windows if c == "AceApp" and "MCDU" in t.upper()]
    mcdu.sort(key=lambda ht: not any(k in ht[1] for k in ("CPT", "CAPT", "LEFT", "1")))
    return mcdu[0][0] if mcdu else None


def window_title(hwnd):
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def capture(hwnd):
    """Fensterinhalt als RGB-Array; funktioniert auch, wenn das Fenster verdeckt ist."""
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    w, h = r.right, r.bottom
    if w <= 0 or h <= 0:
        return None
    hdc = user32.GetDC(hwnd)
    mdc = gdi32.CreateCompatibleDC(hdc)
    bmp = gdi32.CreateCompatibleBitmap(hdc, w, h)
    old = gdi32.SelectObject(mdc, bmp)
    try:
        user32.PrintWindow(hwnd, mdc, 3)  # PW_CLIENTONLY | PW_RENDERFULLCONTENT
        bmi = BITMAPINFOHEADER(ctypes.sizeof(BITMAPINFOHEADER), w, -h, 1, 32, 0, 0, 0, 0, 0, 0)
        buf = ctypes.create_string_buffer(w * h * 4)
        gdi32.GetDIBits(mdc, bmp, 0, h, buf, ctypes.byref(bmi), 0)
    finally:
        gdi32.SelectObject(mdc, old)
        gdi32.DeleteObject(bmp)
        gdi32.DeleteDC(mdc)
        user32.ReleaseDC(hwnd, hdc)
    return np.frombuffer(buf, np.uint8).reshape(h, w, 4)[:, :, 2::-1].copy()


def normalize(rgb):
    """Displayfläche (alles außer schwarzem Rand) suchen und aufs Referenzmaß skalieren."""
    lum = rgb.max(axis=2)
    ys, xs = np.where(lum >= 3)
    if len(xs) == 0:
        return None
    crop = rgb[ys.min():ys.max() + 1, xs.min():xs.max() + 1]
    if crop.shape[1] < 100 or crop.shape[0] < 100:
        return None
    if crop.shape[:2] != (REF_H, REF_W):
        crop = np.asarray(Image.fromarray(crop).resize((REF_W, REF_H), Image.BILINEAR))
    return crop.astype(np.int16)


# ---------------------------------------------------------------- Zellen zerlegen

class Cell:
    __slots__ = ("row", "col", "shape", "size", "color", "rgb")

    def __init__(self, row, col, shape, size, color, rgb):
        self.row, self.col, self.shape, self.size, self.color, self.rgb = row, col, shape, size, color, rgb


def nearest_color(rgb):
    """Farbcode über Sättigung und Farbton (die Displayfarben sind gedämpft, z.B. Grün ≈ 80,190,80)."""
    r, g, b = rgb
    hi, lo = max(rgb), min(rgb)
    if hi == 0 or (hi - lo) / hi < 0.25:
        return "w" if hi > 120 else "e"
    if hi == r:
        hue = (60 * (g - b) / (hi - lo)) % 360
    elif hi == g:
        hue = 60 * (b - r) / (hi - lo) + 120
    else:
        hue = 60 * (r - g) / (hi - lo) + 240
    for limit, code in ((20, "r"), (55, "a"), (75, "y"), (160, "g"), (255, "c"), (340, "m")):
        if hue < limit:
            return code
    return "r"


def segment(img):
    """Liefert alle belegten Zellen. Zeichen werden über ihre Pixel einer Spalte zugeordnet,
    so dass kleine Verschiebungen im Raster nicht stören."""
    lum = img.max(axis=2)
    cells = []
    for row in range(ROWS):
        bottom = int(round(ROW0_BOTTOM + row * ROW_PITCH))
        y0, y1 = max(0, bottom - CELL_H + 3), min(REF_H, bottom + 3)
        band = lum[y0:y1]
        ink_cols = (band > INK).any(axis=0)
        if not ink_cols.any():
            continue
        xs = np.where(ink_cols)[0]
        # Zusammenhängende Zeichenteile bilden und über ihre Mitte einer Spalte zuordnen.
        # Teile breiter als eine Zelle (z.B. aneinanderstoßende Kästchen) werden pixelweise verteilt.
        by_col = {}
        for comp_y, comp_x in components(band > INK):
            # Pixelgruppen ganz im Randstreifen sind Anschnitte der Nachbarzeile (manche Seiten
            # setzen Text einige Pixel höher/tiefer) und keine eigenen Zeichen.
            if comp_y.min() >= band.shape[0] - EDGE or comp_y.max() < EDGE:
                continue
            if comp_x.max() - comp_x.min() + 1 <= CELL_W * 1.3:
                col = int(round(((comp_x.min() + comp_x.max()) / 2 - COL0_CENTER) / COL_PITCH))
                if 0 <= col < COLS:
                    by_col.setdefault(col, []).append((comp_y, comp_x))
            else:
                cols = np.round((comp_x - COL0_CENTER) / COL_PITCH).astype(int)
                # schmale Randstücke (Rahmen, die knapp in die Nachbarspalte ragen) zur Nachbarspalte schlagen
                uniq = np.unique(cols)
                for col in uniq:
                    sel = cols == col
                    if np.ptp(comp_x[sel]) + 1 < CELL_W / 3:
                        target = col + 1 if col == uniq[0] else col - 1
                        cols[sel] = target
                for col in np.unique(cols):
                    if 0 <= col < COLS:
                        sel = cols == col
                        by_col.setdefault(int(col), []).append((comp_y[sel], comp_x[sel]))
        glyphs = []
        for col, parts in by_col.items():
            ys = np.concatenate([p[0] for p in parts])
            xs = np.concatenate([p[1] for p in parts])
            xa = int(round((xs.min() + xs.max()) / 2 - CELL_W / 2))
            inside = (xs - xa >= 0) & (xs - xa < CELL_W)
            if inside.any():
                glyphs.append((col, ys[inside], xs[inside], xa))
        # Grundlinie der Zeile: häufigste Unterkante der hohen Zeichen. Manche Seiten (z.B. F-PLN)
        # setzen Text einige Pixel höher; so werden alle Zeichen gleich ausgerichtet verglichen.
        bottoms = [ys.max() for _, ys, _, _ in glyphs if np.ptp(ys) + 1 >= 15]
        shift = BASELINE - max(set(bottoms), key=bottoms.count) if bottoms else 0
        for col, ys, xs, xa in glyphs:
            ty = ys + shift
            ok = (ty >= 0) & (ty < y1 - y0)
            ys, xs, ty = ys[ok], xs[ok], ty[ok]
            if len(xs) == 0:
                continue
            patch = np.zeros((y1 - y0, CELL_W), np.int16)
            patch[ty, xs - xa] = band[ys, xs]
            height = ys.max() - ys.min() + 1
            # Hintergrund abziehen, damit graue und weiße Schrift gleich aussehen
            shape = np.clip((patch.astype(np.float32) - BACKGROUND) / (patch.max() - BACKGROUND), 0, 1)
            bright = patch[ty, xs - xa] > patch.max() * 0.6
            sub = img[y0 + ys[bright], xs[bright]]
            rgb = tuple(int(v) for v in sub.mean(axis=0)) if len(sub) else (255, 255, 255)
            cells.append(Cell(row, col, shape, 1 if height <= SMALL_MAX_HEIGHT else 0, nearest_color(rgb), rgb))
    return cells


def components(mask):
    """Zusammenhängende Pixelgruppen (8er-Nachbarschaft) als Paare (ys, xs)."""
    # Waagrechte Pixelläufe je Zeile bilden und Läufe benachbarter Zeilen, die sich
    # (diagonal) berühren, per Union-Find verbinden.
    h, w = mask.shape
    m = np.zeros((h, w + 2), np.int8)
    m[:, 1:-1] = mask
    d = np.diff(m, axis=1)
    run_y, run_x0 = np.nonzero(d == 1)
    _, run_x1 = np.nonzero(d == -1)          # exklusives Ende, gleiche Reihenfolge
    n = len(run_y)
    parent = list(range(n))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_row = {}
    for i, y in enumerate(run_y.tolist()):
        by_row.setdefault(y, []).append(i)
    x0s, x1s = run_x0.tolist(), run_x1.tolist()
    for y, cur in by_row.items():
        for j in by_row.get(y - 1, ()):
            for i in cur:
                if x0s[i] <= x1s[j] and x0s[j] <= x1s[i]:  # Überlappung inkl. Diagonale
                    ri, rj = find(i), find(j)
                    if ri != rj:
                        parent[ri] = rj
    labels = np.zeros((h, w), np.int64)
    for i in range(n):
        labels[run_y[i], x0s[i]:x1s[i]] = find(i)
    ys, xs = np.nonzero(mask)
    lab = labels[ys, xs]
    order = np.argsort(lab, kind="stable")
    ys, xs, lab = ys[order], xs[order], lab[order]
    cuts = np.nonzero(np.diff(lab))[0] + 1
    return list(zip(np.split(ys, cuts), np.split(xs, cuts)))


# ---------------------------------------------------------------- Zeichensammlung

def glyph_char(path):
    # Dateiname: <S|L>_<Codepoint hex>_<id>.png
    return chr(int(path.stem.split("_")[1], 16))


class GlyphLibrary:
    def __init__(self):
        self.chars, self.sizes, self.shapes, self.synthetic = [], [], [], []
        GLYPH_DIR.mkdir(exist_ok=True)
        for p in sorted(GLYPH_DIR.glob("*.png")):
            self.synthetic.append(p.stem.endswith("_synth"))
            self.chars.append(glyph_char(p))
            self.sizes.append(1 if p.stem.startswith("S") else 0)
            self.shapes.append(np.asarray(Image.open(p), np.float32) / 255)
        self._restack()

    def _restack(self):
        self.stack = np.stack(self.shapes) if self.shapes else None
        self.cache = {}

    def match(self, shape):
        if self.stack is None or self.stack.shape[1:] != shape.shape:
            return None, 1.0, 0
        # Unveränderte Zellen kommen von Bild zu Bild immer wieder: Ergebnis zwischenspeichern
        key = np.round(shape * 16).astype(np.uint8).tobytes()
        i, dist = self.cache.get(key, (None, None))
        if i is None:
            # Eine grobe Vorauswahl lohnt nicht: z.B. graue und weiße D/O liegen grob zu nah beieinander.
            d = glyph_distance(self.stack, shape)
            i = int(d.argmin())
            dist = float(d[i])
            if len(self.cache) > 20000:
                self.cache.clear()
            self.cache[key] = (i, dist)
        self.last_synthetic = self.synthetic[i]
        return self.chars[i], dist, self.sizes[i]

    def add(self, ch, size, shape, key):
        p = GLYPH_DIR / f"{'S' if size else 'L'}_{ord(ch):04X}_{key}.png"
        Image.fromarray((shape * 255).astype(np.uint8)).save(p)
        self.synthetic.append(key == "synth")
        self.chars.append(ch)
        self.sizes.append(size)
        self.shapes.append(shape)
        self._restack()


def glyph_distance(stack, shape):
    """Anteil deutlich verschiedener Pixel an allen Schriftpixeln (bestes von ±1 px Verschiebung).
    Reagiert auch auf kleine Details wie den Haken der 1 gegenüber dem I."""
    stack = stack.reshape((-1,) + shape.shape)
    best = np.full(len(stack), 1.0)
    for dx in (-1, 0, 1):
        for dy in (-1, 0, 1):
            s = np.roll(np.roll(shape, dx, axis=1), dy, axis=0)
            differ = (np.abs(stack - s) > 0.5).sum(axis=(1, 2))
            ink = ((stack > 0.5) | (s > 0.5)).sum(axis=(1, 2))
            best = np.minimum(best, differ / np.maximum(ink, 1))
    return best


def recognize(lib, cells):
    grid = [[None] * COLS for _ in range(ROWS)]
    unknown = []
    for c in cells:
        ch, dist, size = lib.match(c.shape)
        if ch is None or dist > MATCH_LIMIT:
            unknown.append(c)
            ch = "?"
        else:
            c.size = size  # Größe aus der Bibliothek ist verlässlicher als die gemessene Höhe
        grid[c.row][c.col] = (ch, c)
    # Satzzeichen (. - / [ ] ° ...) sehen in beiden Größen fast gleich aus: Größe vom nächsten
    # Buchstaben/Ziffer derselben Zeile übernehmen, bevorzugt in gleicher Farbe.
    for row in grid:
        anchors = [g for g in row if g is not None and g[0].isalnum()]
        for g in row:
            if g is None or g[0].isalnum() or g[0] == "?" or not anchors:
                continue
            c = g[1]
            c.size = min(anchors, key=lambda a: (a[1].color != c.color, abs(a[1].col - c.col)))[1].size
    return grid, unknown


def grid_text(grid):
    lines = ["+" + "-" * COLS + "+"]
    for row in grid:
        lines.append("|" + "".join(" " if g is None else g[0] for g in row) + "|")
    lines.append("+" + "-" * COLS + "+")
    return "\n".join(lines)


def to_mobiflight(grid):
    data = []
    for row in grid:
        for g in row:
            if g is None or g[0] == " ":
                data.append([])
            else:
                ch, c = g
                data.append([ch, c.color, c.size])
    return json.dumps({"Target": "Display", "Data": data})


def save_unknown(cells):
    UNKNOWN_DIR.mkdir(exist_ok=True)
    new = 0
    for c in cells:
        img = Image.fromarray((c.shape * 255).astype(np.uint8))
        key = hashlib.sha1(np.round(c.shape * 8).astype(np.uint8).tobytes()).hexdigest()[:10]
        p = UNKNOWN_DIR / f"{'S' if c.size else 'L'}_{key}.png"
        if not p.exists():
            img.save(p)
            new += 1
    return new


def dedupe_unknown():
    """Fast gleiche unbekannte Zeichen zusammenfassen (Kantenglättung erzeugt Varianten)."""
    files = sorted(UNKNOWN_DIR.glob("?_*.png"))
    keep, shapes = [], []
    for p in files:
        s = np.asarray(Image.open(p), np.float32) / 255
        if shapes and glyph_distance(np.stack(shapes), s).min() < SAME_LIMIT:
            p.unlink()
        else:
            keep.append(p)
            shapes.append(s)
    return keep


def make_sheet():
    files = dedupe_unknown()
    if not files:
        print("Keine unbekannten Zeichen.")
        return
    per_row, sw, sh = 12, CELL_W * 3 + 10, 34 * 3 + 22
    sheet = Image.new("RGB", (per_row * sw, ((len(files) - 1) // per_row + 1) * sh), (40, 40, 40))
    d = ImageDraw.Draw(sheet)
    index = {}
    for i, p in enumerate(files):
        g = Image.open(p).resize((CELL_W * 3, 34 * 3), Image.NEAREST)
        x, y = (i % per_row) * sw, (i // per_row) * sh
        sheet.paste(g.convert("RGB"), (x + 5, y + 18))
        d.text((x + 5, y + 3), f"{i} {p.stem[0]}", fill=(255, 200, 0))
        index[str(i)] = p.name
    sheet.save(UNKNOWN_DIR / "_sheet.png")
    (UNKNOWN_DIR / "_index.json").write_text(json.dumps(index, indent=1))
    print(f"{len(files)} unbekannte Zeichen -> {UNKNOWN_DIR / '_sheet.png'}")


def label(pairs):
    index = json.loads((UNKNOWN_DIR / "_index.json").read_text())
    for pair in pairs:
        num, ch = pair.split("=", 1)
        if ch.lower() == "space":
            ch = " "
        if ch.lower() == "skip":
            (UNKNOWN_DIR / index[num]).unlink(missing_ok=True)
            continue
        src = UNKNOWN_DIR / index[num]
        size, key = src.stem.split("_", 1)
        dst = GLYPH_DIR / f"{size}_{ord(ch):04X}_{key}.png"
        GLYPH_DIR.mkdir(exist_ok=True)
        src.replace(dst)
        print(f"{num}: '{ch}' -> {dst.name}")


def teach(lib, cells, lines, key_prefix):
    """Zellen anhand einer Abschrift des Bildschirms (14 Zeilen Text) in die Bibliothek übernehmen."""
    lines = [(l + " " * COLS)[:COLS] for l in (lines + [""] * ROWS)[:ROWS]]
    by_pos = {(c.row, c.col): c for c in cells}
    added = problems = 0
    for (row, col), c in sorted(by_pos.items()):
        ch = lines[row][col]
        if ch == " ":
            print(f"  ? Zeile {row} Spalte {col}: Bild zeigt ein Zeichen, Abschrift ein Leerzeichen")
            problems += 1
            continue
        # Größe: bei Buchstaben/Ziffern aus der Höhe, bei Symbolen vom nächsten Buchstaben der Zeile
        if not ch.isalnum():
            neighbours = [n for n in cells if n.row == row and lines[row][n.col].isalnum()]
            if neighbours:
                c.size = min(neighbours, key=lambda n: abs(n.col - col)).size
        known, dist, _ = lib.match(c.shape)
        if known is not None and known != ch and dist < MATCH_LIMIT:
            print(f"  ! Zeile {row} Spalte {col}: '{ch}' sieht aus wie bekanntes '{known}' (Abstand {dist:.3f})")
            problems += 1
        if known == ch and dist < SAME_LIMIT and not lib.last_synthetic:
            continue
        lib.add(ch, c.size, c.shape, f"{key_prefix}{row:02d}{col:02d}")
        added += 1
        # ein echtes Exemplar ersetzt ein künstlich erzeugtes (siehe synthesize_small)
        synth = GLYPH_DIR / f"{'S' if c.size else 'L'}_{ord(ch):04X}_synth.png"
        if synth.exists():
            synth.unlink()
            print(f"  echtes '{ch}' ersetzt das künstliche Zeichenbild")
    for row in range(ROWS):
        for col in range(COLS):
            if lines[row][col] != " " and (row, col) not in by_pos:
                print(f"  ? Zeile {row} Spalte {col}: Abschrift '{lines[row][col]}', im Bild leer")
                problems += 1
    print(f"{added} neue Zeichenbilder, {problems} Auffälligkeiten, Bibliothek jetzt {len(lib.chars)}")


def synthesize_small(lib, chars):
    """Kleine Zeichenbilder aus den großen erzeugen, für Zeichen, die klein noch nie vorkamen.
    Der Maßstab wird aus Buchstaben bestimmt, die es in beiden Größen gibt. Nur als Notlösung:
    echte Exemplare (per teach) sind genauer; die erzeugten Dateien enthalten 'synth' im Namen."""
    def bbox(s):
        ys, xs = np.nonzero(s > 0.3)
        return ys.min(), ys.max(), xs.min(), xs.max()

    first = {}
    for ch, size, shape in zip(lib.chars, lib.sizes, lib.shapes):
        first.setdefault((ch, size), shape)
    pairs = [c for (c, s) in first if s == 0 and (c, 1) in first and c.isalpha() and c != "I"]
    if not pairs:
        sys.exit("Zu wenige Zeichen in beiden Größen, um den Maßstab zu bestimmen.")
    fy = float(np.median([(lambda a, b: (a[1] - a[0] + 1) / (b[1] - b[0] + 1))(bbox(first[c, 1]), bbox(first[c, 0])) for c in pairs]))
    fx = float(np.median([(lambda a, b: (a[3] - a[2] + 1) / (b[3] - b[2] + 1))(bbox(first[c, 1]), bbox(first[c, 0])) for c in pairs]))
    for ch in chars:
        if (ch, 0) not in first:
            print(f"'{ch}': kein großes Vorbild vorhanden, übersprungen")
            continue
        shape = first[ch, 0]
        y0, y1, x0, x1 = bbox(shape)
        g = Image.fromarray((shape[y0:y1 + 1, x0:x1 + 1] * 255).astype(np.uint8))
        w, h = max(1, round(g.width * fx)), max(1, round(g.height * fy))
        g = np.asarray(g.resize((w, h), Image.LANCZOS), np.float32) / 255
        out = np.zeros_like(shape)
        top = int(round(BASELINE - (BASELINE - y1) * fy)) - h + 1
        left = (CELL_W - w) // 2
        out[max(0, top):top + h, left:left + w] = g[max(0, -top):, :]
        lib.add(ch, 1, np.clip(out, 0, 1), "synth")
        print(f"'{ch}': kleines Zeichenbild erzeugt (Faktor {fx:.2f} x {fy:.2f})")


# ---------------------------------------------------------------- Betriebsarten

def grab_image():
    hwnd = find_window()
    if not hwnd:
        return None
    rgb = capture(hwnd)
    return normalize(rgb) if rgb is not None else None


def grab_cells():
    img = grab_image()
    return segment(img) if img is not None else None


def cmd_snap(args):
    img = grab_image()
    if img is None:
        sys.exit(" ".join(p for p in window_problem() if p))
    out = Path(args.file)
    out.parent.mkdir(parents=True, exist_ok=True)
    Image.fromarray(img.astype(np.uint8)).save(out)
    print(f"Gespeichert: {out}")


def cmd_record(args):
    """Speichert jede Seite, auf der unbekannte Zeichen stehen, als Snapshot (zum späteren Abschreiben)."""
    lib = GlyphLibrary()
    out = HERE / "snapshots"
    out.mkdir(exist_ok=True)
    pending = []  # Formen, die schon in einem Snapshot dieser Sitzung stecken
    end, n = time.time() + args.seconds, 0
    print(f"Nehme {args.seconds} s lang Seiten mit unbekannten Zeichen auf ...", flush=True)
    last_text = None
    while time.time() < end:
        img = grab_image()
        if img is not None:
            grid, unknown = recognize(lib, segment(img))
            fresh = [c for c in unknown
                     if not pending or glyph_distance(np.stack(pending), c.shape).min() >= SAME_LIMIT]
            text = grid_text(grid)
            if args.all and text != last_text:
                fresh = fresh or ["Seitenwechsel"]
            last_text = text
            if fresh:
                time.sleep(0.4)  # Seite fertig aufbauen lassen
                img = grab_image()
                n += 1
                name = out / f"rec_{time.strftime('%H%M%S')}_{n:02d}.png"
                Image.fromarray(img.astype(np.uint8)).save(name)
                pending += [c.shape for c in recognize(lib, segment(img))[1]]
                print(f"{name.name}: {len(fresh)} neue Zeichen", flush=True)
        time.sleep(0.5)
    print(f"Fertig, {n} Snapshots.")


def cmd_teach(args):
    img = Image.open(args.image).convert("RGB") if args.image else None
    cells = segment(np.asarray(img).astype(np.int16)) if img else grab_cells()
    lines = Path(args.transcript).read_text(encoding="utf-8").splitlines()  # auch bei CRLF kein \r in einer Spalte
    teach(GlyphLibrary(), cells, lines, Path(args.transcript).stem[:6] + "_")


def cmd_show(args):
    lib = GlyphLibrary()
    if args.image:
        cells = segment(np.asarray(Image.open(args.image).convert("RGB")).astype(np.int16))
    else:
        cells = grab_cells()
    if cells is None:
        sys.exit(" ".join(p for p in window_problem() if p))
    grid, unknown = recognize(lib, cells)
    print(grid_text(grid))
    print(f"{len(cells)} Zeichen, {len(unknown)} unbekannt, Bibliothek: {len(lib.chars)} Zeichenbilder")


def cmd_collect(args):
    end = time.time() + args.seconds
    total = 0
    lib = GlyphLibrary()
    print(f"Sammle {args.seconds} s lang unbekannte Zeichen ... MCDU-Seiten durchblättern!")
    while time.time() < end:
        cells = grab_cells()
        if cells:
            _, unknown = recognize(lib, cells)
            total += save_unknown(unknown)
        time.sleep(0.5)
    print(f"{total} neue Zeichenbilder gespeichert.")
    make_sheet()


# ---------------------------------------------------------------- Debug-Ausgabe

# Konsolenfarben (24 bit) je MobiFlight-Farbcode, an die Displayfarben des A330 angelehnt
ANSI_RGB = {
    "w": (230, 230, 230), "e": (120, 120, 120), "g": (80, 210, 80), "c": (70, 195, 235),
    "a": (235, 165, 45), "y": (235, 235, 60), "m": (220, 110, 220), "r": (235, 70, 70),
    "o": (90, 130, 255), "k": (195, 185, 125),
}


def enable_ansi():
    """Farb-/Steuersequenzen in der Windows-Konsole einschalten und UTF-8 ausgeben (für ←↑☐°Δ)."""
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except (AttributeError, ValueError):
        pass
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.GetStdHandle(-11)  # STD_OUTPUT_HANDLE
    mode = wintypes.DWORD()
    if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
        kernel32.SetConsoleMode(handle, mode.value | 0x0004)  # ENABLE_VIRTUAL_TERMINAL_PROCESSING


def screen_hash(grid):
    """Hash über Zeichen und Farbe jeder Zelle; die Schriftgröße zählt für die Anzeige nicht."""
    if grid is None:
        return "kein Fenster"
    cells = "".join(" ." if g is None else g[0] + (g[1].color if g[0] != "?" else "?") for row in grid for g in row)
    return hashlib.sha1(cells.encode("utf-8")).hexdigest()


def window_problem():
    """Warum kein Bild kommt: Ein minimiertes Pop-out zeichnet MSFS nicht, es liefert nur Schwarz."""
    hwnd = find_window()
    if not hwnd:
        if WINDOW_OVERRIDE:
            return ["Fenster", WINDOW_OVERRIDE[:COLS], "nicht gefunden"]
        return ["Kein MCDU-Pop-out", "gefunden", "", f"(Titel {WINDOW_TITLE}", "oder mit MCDU im Titel)"]
    name = window_title(hwnd)[:COLS]
    if user32.IsIconic(hwnd):
        return ["Fenster", name, "ist minimiert.", "", "Bitte wiederherstellen.", "Es darf verdeckt sein",
                "oder auf einem anderen", "Monitor liegen."]
    return ["Fenster", name, "liefert kein Bild"]


def render_debug(grid, status, problem=None):
    """Bildschirm löschen und den erkannten Inhalt farbig ausgeben."""
    out = ["\x1b[H\x1b[2J\x1b[3J"]  # Cursor nach oben links, Bildschirm und Scrollpuffer löschen
    border = "\x1b[38;2;90;90;90m+" + "-" * COLS + "+\x1b[0m"
    out.append(border)
    if grid is None:
        lines = problem or []
        first = (ROWS - len(lines)) // 2
        for row in range(ROWS):
            text = lines[row - first] if 0 <= row - first < len(lines) else ""
            out.append(f"\x1b[38;2;90;90;90m|\x1b[0m\x1b[38;2;235;165;45m{text:^{COLS}}\x1b[0m\x1b[38;2;90;90;90m|\x1b[0m")
    else:
        for row in grid:
            line = ["\x1b[38;2;90;90;90m|\x1b[0m"]
            for g in row:
                if g is None:
                    line.append(" ")
                elif g[0] == "?":
                    line.append("\x1b[1;97;41m?\x1b[0m")  # unbekanntes Zeichen: weiß auf rot
                else:
                    r, gr, b = ANSI_RGB.get(g[1].color, ANSI_RGB["w"])
                    line.append(f"\x1b[38;2;{r};{gr};{b}m{g[0]}")
            line.append("\x1b[0m\x1b[38;2;90;90;90m|\x1b[0m")
            out.append("".join(line))
    out.append(border)
    out.append(status)
    sys.stdout.write("\n".join(out) + "\n")
    sys.stdout.flush()


# ---------------------------------------------------------------- Bridge-Betrieb

INSTANCE_MUTEX = "Local\\WinCtrlMcduScreenBridge"
EXIT_ALREADY_RUNNING = 3   # eigener Exit-Code, damit eine Startdatei den Fall erkennen kann
_instance_handle = None    # Mutex-Handle bis Prozessende festhalten


def ensure_single_instance():
    """Nur eine laufende Bridge erlauben: benannter Windows-Mutex. Windows gibt ihn beim Prozessende
    automatisch frei, auch nach einem Absturz; es bleiben also keine Sperrdateien liegen."""
    global _instance_handle
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateMutexW.restype = wintypes.HANDLE
    kernel32.CreateMutexW.argtypes = [ctypes.c_void_p, wintypes.BOOL, wintypes.LPCWSTR]
    handle = kernel32.CreateMutexW(None, False, INSTANCE_MUTEX)
    if ctypes.get_last_error() == 183:  # ERROR_ALREADY_EXISTS
        print("Die MCDU-Bridge läuft bereits. Diese zweite Instanz wird beendet.", flush=True)
        sys.exit(EXIT_ALREADY_RUNNING)
    _instance_handle = handle


async def open_mobiflight(url):
    import websockets

    ws = await asyncio.wait_for(websockets.connect(url, ping_interval=None), 5)
    await ws.send(json.dumps({"Target": "Font", "Data": "AirbusThales"}))
    await asyncio.sleep(1)  # MobiFlight Zeit geben, die Schrift zu setzen
    return ws


async def cmd_run(args):
    lib = GlyphLibrary()
    if args.debug:
        enable_ansi()
    print(f"MCDU Screen Bridge {__version__} - Bibliothek: {len(lib.chars)} Zeichenbilder. "
          f"Verbinde mit {args.url} ...", flush=True)
    ws, connecting, next_try, last_sent, last_hash = None, None, 0.0, None, None
    last_problem = "start"
    last_connection = None
    connection = "Verbindungsaufbau ..."
    while True:
        now = time.monotonic()
        # MobiFlight-Verbindung im Hintergrund (wieder) aufbauen. Ein Versuch ohne laufendes
        # MobiFlight dauert unter Windows Sekunden; Erkennung und Anzeige laufen derweil weiter.
        if ws is None and connecting is None and now >= next_try:
            connecting = asyncio.create_task(open_mobiflight(args.url))
        if connecting is not None and connecting.done():
            try:
                ws = connecting.result()
                last_sent, connection = None, "verbunden"
            except Exception as e:
                next_try = now + 5
                connection = f"nicht verbunden ({type(e).__name__}), neuer Versuch alle 5 s"
            connecting = None

        t0 = time.perf_counter()
        cells = grab_cells()
        grid, unknown = recognize(lib, cells) if cells is not None else (None, [])
        took = (time.perf_counter() - t0) * 1000
        if grid is not None:
            if unknown and args.learn:
                save_unknown(unknown)
            msg = to_mobiflight(grid)
            if ws is not None and msg != last_sent:
                try:
                    await ws.send(msg)
                    last_sent = msg
                except Exception as e:
                    ws, next_try = None, time.monotonic() + 5
                    connection = f"Verbindung abgebrochen ({type(e).__name__}), neuer Versuch in 5 s"
        # ohne --debug den MobiFlight-Status nur bei einem Wechsel melden, nicht bei jedem neuen Versuch
        if not args.debug and connection != last_connection:
            print(f"MobiFlight: {connection}", flush=True)
            last_connection = connection

        problem = window_problem() if grid is None else None
        if not args.debug and problem != last_problem:
            print(" ".join(p for p in problem if p) if problem
                  else f"Fenster '{window_title(find_window())}' wird gelesen.", flush=True)
        last_problem = problem
        if args.debug:
            h = hashlib.sha1((screen_hash(grid) + connection + str(problem)).encode()).hexdigest()
            if h != last_hash:
                last_hash = h
                status = (f"{time.strftime('%H:%M:%S')}  MobiFlight: {connection}\n"
                          f"Zeichen: {0 if cells is None else len(cells)}, unbekannt: {len(unknown)}, "
                          f"Erkennung: {took:.0f} ms   (Strg+C beendet)   v{__version__}")
                render_debug(grid, status, problem)
        await asyncio.sleep(args.interval)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--version", action="version", version=f"mcdu_screen_bridge {__version__}")
    win = argparse.ArgumentParser(add_help=False)
    win.add_argument("--window", help="Titel des Pop-out-Fensters, z.B. \"MCDU Cpt (Custom)\" "
                                      "(Standard: WASMINSTRUMENT, sonst ein MSFS-Fenster mit MCDU im Titel)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", parents=[win])
    r.add_argument("--url", default=MOBIFLIGHT_URL)
    r.add_argument("--interval", type=float, default=0.2, help="Sekunden zwischen zwei Aufnahmen")
    r.add_argument("--learn", action="store_true", help="unbekannte Zeichen nach unknown/ speichern")
    r.add_argument("--debug", action="store_true",
                   help="erkannten Bildschirm farbig in der Konsole zeigen, neu gezeichnet nur bei Änderungen")
    sh = sub.add_parser("show", parents=[win])
    sh.add_argument("--image", help="gespeicherter Snapshot statt Live-Aufnahme")
    c = sub.add_parser("collect", parents=[win])
    c.add_argument("--seconds", type=int, default=120)
    sn = sub.add_parser("snap", parents=[win], help="Displayinhalt als PNG speichern")
    sn.add_argument("file", nargs="?", default=str(HERE / "snapshots" / "snap.png"))
    t = sub.add_parser("teach", parents=[win], help="Zeichen anhand einer Abschrift (Textdatei, 14 Zeilen) lernen")
    t.add_argument("transcript")
    t.add_argument("--image", help="gespeicherter Snapshot statt Live-Aufnahme")
    rc = sub.add_parser("record", parents=[win], help="Seiten mit unbekannten Zeichen als Snapshot speichern")
    rc.add_argument("--seconds", type=int, default=600)
    rc.add_argument("--all", action="store_true", help="jede geänderte Seite speichern, nicht nur bei unbekannten Zeichen")
    sy = sub.add_parser("synth", help="kleine Zeichenbilder aus großen erzeugen (Notlösung)")
    sy.add_argument("chars", help="z.B. 6789JKY")
    sub.add_parser("sheet")
    lb = sub.add_parser("label")
    lb.add_argument("pairs", nargs="+", help="Nummer=Zeichen, z.B. 3=A 4=space 5=skip")
    args = ap.parse_args()
    global WINDOW_OVERRIDE
    WINDOW_OVERRIDE = getattr(args, "window", None)

    if args.cmd == "run":
        ensure_single_instance()
        try:
            asyncio.run(cmd_run(args))
        except KeyboardInterrupt:
            pass
    elif args.cmd == "show":
        cmd_show(args)
    elif args.cmd == "collect":
        cmd_collect(args)
    elif args.cmd == "snap":
        cmd_snap(args)
    elif args.cmd == "teach":
        cmd_teach(args)
    elif args.cmd == "record":
        cmd_record(args)
    elif args.cmd == "synth":
        synthesize_small(GlyphLibrary(), args.chars)
    elif args.cmd == "sheet":
        make_sheet()
    elif args.cmd == "label":
        label(args.pairs)


if __name__ == "__main__":
    main()
