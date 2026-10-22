"""Erkennung gegen die Referenzseiten in snapshots/ prüfen (Bild + Abschrift) und einige Hilfsfunktionen.

Aufruf im Projektordner:  python -m pytest
"""

from pathlib import Path

import numpy as np
import pytest

import mcdu_screen_bridge as m

SNAPSHOTS = sorted((Path(m.HERE) / "snapshots").glob("*.txt"))


@pytest.fixture(scope="module")
def lib():
    return m.GlyphLibrary()


def read_transcript(path):
    lines = path.read_text(encoding="utf-8").splitlines()
    return [(line + " " * m.COLS)[:m.COLS] for line in [*lines, *[""] * m.ROWS][:m.ROWS]]


def mismatches(lib, transcript):
    """Liste (Zeile, Spalte, erwartet, erkannt) und Anzahl der verglichenen Zeichen."""
    lines = read_transcript(transcript)
    grid, _ = m.recognize(lib, m.segment(m.load_image(transcript.with_suffix(".png"))))
    wrong, total = [], 0
    for r in range(m.ROWS):
        for c in range(m.COLS):
            got = " " if grid[r][c] is None else grid[r][c][0]
            if got != " " or lines[r][c] != " ":
                total += 1
                if got != lines[r][c]:
                    wrong.append((r, c, lines[r][c], got))
    return wrong, total


def test_snapshots_present():
    assert SNAPSHOTS, "keine Referenzseiten in snapshots/"


@pytest.mark.parametrize("transcript", SNAPSHOTS, ids=lambda p: p.stem)
def test_snapshot_recognized(lib, transcript):
    wrong, total = mismatches(lib, transcript)
    assert total > 0
    assert not wrong, f"{len(wrong)} von {total} Zeichen falsch (Zeile, Spalte, erwartet, erkannt): {wrong}"


def test_snapshot_total(lib):
    """Gesamtzahl wie in CLAUDE.md dokumentiert; ändert sich, sobald Referenzseiten hinzukommen."""
    total = sum(mismatches(lib, t)[1] for t in SNAPSHOTS)
    assert total == 3509


@pytest.mark.parametrize(("rgb", "code"), [
    ((230, 230, 230), "w"), ((66, 66, 66), "e"), ((80, 190, 80), "g"), ((70, 190, 230), "c"),
    ((235, 165, 45), "a"), ((220, 110, 220), "m"),
])
def test_nearest_color(rgb, code):
    assert m.nearest_color(rgb) == code


def test_components_joins_diagonals():
    mask = np.zeros((5, 6), bool)
    mask[0, 0] = mask[1, 1] = mask[2, 2] = True   # diagonal verbunden
    mask[4, 5] = True                              # eigene Gruppe
    groups = sorted(len(ys) for ys, _ in m.components(mask))
    assert groups == [1, 3]


@pytest.mark.parametrize("pair", ["3", "99=A", "0=AB"])
def test_parse_label_rejects(pair):
    with pytest.raises(m.BridgeError):
        m.parse_label(pair, {"0": "L_x.png"})


def test_parse_label_accepts():
    index = {"0": "L_x.png"}
    assert m.parse_label("0=A", index) == ("0", "A")
    assert m.parse_label("0=space", index) == ("0", " ")
    assert m.parse_label("0=skip", index) == ("0", None)
