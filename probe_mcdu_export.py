"""
Prüft, ob das geladene Flugzeug (Ziel: Microsoft/iniBuilds A330) einen MCDU-Export
per SimConnect Client Data anbietet - so wie iniBuilds es beim A300 ("iniAirbusMCDU_1")
und A340 ("A340MCDU", "iniAirbusMCDU_2") macht.

Vorgehen:
  1. Verbindet sich per SimConnect mit dem laufenden MSFS 2024.
  2. Meldet, welches Flugzeug geladen ist.
  3. Abonniert eine Liste möglicher Client-Data-Namen. Liefert einer davon Daten,
     existiert der Export. Der Inhalt wird als 24x14-MCDU-Raster ausgegeben
     (Format wie bei A300/A340: 3 Bytes pro Zeichen = Symbol, Farbe, Flags).

Benötigt nur Python 3 (64 bit) und die SimConnect.dll aus dem MSFS 2024 SDK.

Aufruf:
  python probe_mcdu_export.py                    # Standardnamen, 20 s lauschen
  python probe_mcdu_export.py --seconds 60       # länger lauschen
  python probe_mcdu_export.py --names A330MCDU X # zusätzliche Namen testen
  python probe_mcdu_export.py --dll "C:\\pfad\\SimConnect.dll"
"""

import argparse
import ctypes
import os
import sys
import time
from ctypes import wintypes
from pathlib import Path

# Bekannte iniBuilds-Namen (A300/A340) plus naheliegende Varianten für den A330.
DEFAULT_NAMES = [
    "iniAirbusMCDU_1",
    "iniAirbusMCDU_2",
    "iniAirbusMCDU_3",
    "iniAirbusMCDU",
    "A330MCDU",
    "A330MCDU_1",
    "A330MCDU_2",
    "A330_MCDU",
    "iniA330MCDU",
    "A340MCDU",
    "A300MCDU",
    "iniMCDU_1",
    "iniMCDU_2",
    "MCDU_1",
    "MCDU_2",
    "MCDUA",
]

MCDU_COLUMNS, MCDU_ROWS = 24, 14
MCDU_CHAR_SIZE = 3
MCDU_DATA_SIZE = MCDU_COLUMNS * MCDU_ROWS * MCDU_CHAR_SIZE  # 1008

COLOR_NAMES = {0: "white", 1: "cyan", 2: "amber", 3: "green", 4: "grey", 5: "red", 6: "yellow", 7: "magenta"}
SPECIAL_CHARS = {"a": "←", "b": "→", "e": "↑", "f": "↓", "!": "☐", "o": "☐", "d": "°", "c": "Δ", "p": "■"}

# SimConnect-Konstanten (aus SimConnect.h des MSFS 2024 SDK)
RECV_ID_EXCEPTION = 1
RECV_ID_OPEN = 2
RECV_ID_QUIT = 3
RECV_ID_SYSTEM_STATE = 15
RECV_ID_CLIENT_DATA = 16
PERIOD_ONCE = 1
PERIOD_SECOND = 4
FLAG_DEFAULT = 0
UNUSED = 0xFFFFFFFF

EXCEPTION_NAMES = {
    1: "ERROR", 2: "SIZE_MISMATCH", 3: "UNRECOGNIZED_ID", 7: "NAME_UNRECOGNIZED",
    10: "TOO_MANY_MAPS", 12: "TOO_MANY_REQUESTS", 18: "INVALID_DATA_TYPE",
    19: "INVALID_DATA_SIZE", 20: "DATA_ERROR",
    25: "ILLEGAL_OPERATION (Bereich existiert nicht)", 26: "ALREADY_SUBSCRIBED",
    28: "DEFINITION_ERROR", 29: "DUPLICATE_ID", 31: "OUT_OF_BOUNDS",
}

REQ_AIRCRAFT = 1

# IDs pro Kandidat: Client-Data-ID, 1-Byte-Probe-Definition, volle Definition
CD_BASE, DEF_PROBE_BASE, DEF_FULL_BASE = 1000, 2000, 3000
REQ_PROBE_BASE, REQ_FULL_BASE = 4000, 5000


class RECV(ctypes.Structure):
    _fields_ = [("dwSize", wintypes.DWORD), ("dwVersion", wintypes.DWORD), ("dwID", wintypes.DWORD)]


class RECV_EXCEPTION(ctypes.Structure):
    _fields_ = RECV._fields_ + [("dwException", wintypes.DWORD), ("dwSendID", wintypes.DWORD), ("dwIndex", wintypes.DWORD)]


class RECV_CLIENT_DATA(ctypes.Structure):
    # Entspricht SIMCONNECT_RECV_SIMOBJECT_DATA; die Nutzdaten folgen ab sizeof(...)
    _fields_ = RECV._fields_ + [
        ("dwRequestID", wintypes.DWORD), ("dwObjectID", wintypes.DWORD), ("dwDefineID", wintypes.DWORD),
        ("dwFlags", wintypes.DWORD), ("dwentrynumber", wintypes.DWORD), ("dwoutof", wintypes.DWORD),
        ("dwDefineCount", wintypes.DWORD),
    ]


class RECV_SYSTEM_STATE(ctypes.Structure):
    _fields_ = RECV._fields_ + [
        ("dwRequestID", wintypes.DWORD), ("dwInteger", wintypes.DWORD),
        ("fFloat", ctypes.c_float), ("szString", ctypes.c_char * 260),
    ]


def find_dll(explicit):
    candidates = []
    if explicit:
        candidates.append(Path(explicit))
    for env in ("MSFS2024_SDK", "MSFS_SDK"):
        if os.environ.get(env):
            candidates.append(Path(os.environ[env]) / "SimConnect SDK" / "lib" / "SimConnect.dll")
    candidates += [
        Path(__file__).with_name("SimConnect.dll"),
        Path(r"C:\MSFS 2024 SDK\SimConnect SDK\lib\SimConnect.dll"),
        Path(r"C:\MSFS SDK\SimConnect SDK\lib\SimConnect.dll"),
    ]
    for c in candidates:
        if c.is_file():
            return c
    sys.exit("SimConnect.dll nicht gefunden. Bitte MSFS 2024 SDK installieren oder --dll angeben.")


class Probe:
    def __init__(self, dll_path, names):
        self.names = names
        self.dll = ctypes.WinDLL(str(dll_path))
        self.h = wintypes.HANDLE()
        self.send_ids = {}          # SendID -> Beschreibung, um Exceptions zuzuordnen
        self.alive = {}             # Name -> Anzahl empfangener Probe-Pakete
        self.full_requested = set()
        self.last_frame = {}        # Name -> letzte Rohdaten
        self.seen_exceptions = set()

        d = self.dll
        d.SimConnect_Open.argtypes = [ctypes.POINTER(wintypes.HANDLE), ctypes.c_char_p, wintypes.HWND,
                                      wintypes.DWORD, wintypes.HANDLE, wintypes.DWORD]
        d.SimConnect_Close.argtypes = [wintypes.HANDLE]
        d.SimConnect_MapClientDataNameToID.argtypes = [wintypes.HANDLE, ctypes.c_char_p, wintypes.DWORD]
        d.SimConnect_AddToClientDataDefinition.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD,
                                                           wintypes.DWORD, ctypes.c_float, wintypes.DWORD]
        d.SimConnect_RequestClientData.argtypes = [wintypes.HANDLE, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                                   wintypes.DWORD, wintypes.DWORD, wintypes.DWORD, wintypes.DWORD,
                                                   wintypes.DWORD]
        d.SimConnect_RequestSystemState.argtypes = [wintypes.HANDLE, wintypes.DWORD, ctypes.c_char_p]
        d.SimConnect_GetNextDispatch.argtypes = [wintypes.HANDLE, ctypes.POINTER(ctypes.POINTER(RECV)),
                                                 ctypes.POINTER(wintypes.DWORD)]
        d.SimConnect_GetLastSentPacketID.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        for f in ("SimConnect_Open", "SimConnect_Close", "SimConnect_MapClientDataNameToID",
                  "SimConnect_AddToClientDataDefinition", "SimConnect_RequestClientData",
                  "SimConnect_RequestSystemState", "SimConnect_GetNextDispatch", "SimConnect_GetLastSentPacketID"):
            getattr(d, f).restype = ctypes.c_long

    def call(self, what, func, *args):
        hr = func(self.h, *args)
        if hr != 0:
            print(f"  ! {what}: HRESULT 0x{hr & 0xFFFFFFFF:08X}")
            return
        sid = wintypes.DWORD()
        self.dll.SimConnect_GetLastSentPacketID(self.h, ctypes.byref(sid))
        self.send_ids[sid.value] = what

    def open(self):
        hr = self.dll.SimConnect_Open(ctypes.byref(self.h), b"MCDU Export Probe", None, 0, None, 0)
        if hr != 0:
            sys.exit("Keine Verbindung zu MSFS. Läuft der Simulator und ist ein Flug geladen?")

    def subscribe(self):
        d = self.dll
        self.call("RequestSystemState(AircraftLoaded)", d.SimConnect_RequestSystemState, REQ_AIRCRAFT, b"AircraftLoaded")
        for i, name in enumerate(self.names):
            cd = CD_BASE + i
            self.call(f"Map '{name}'", d.SimConnect_MapClientDataNameToID, name.encode(), cd)
            self.call(f"Def probe '{name}'", d.SimConnect_AddToClientDataDefinition, DEF_PROBE_BASE + i, 0, 1, 0.0, UNUSED)
            self.call(f"Def full '{name}'", d.SimConnect_AddToClientDataDefinition, DEF_FULL_BASE + i, 0, MCDU_DATA_SIZE, 0.0, UNUSED)
            # Probe jede Sekunde: kommt etwas an, existiert der Bereich.
            self.call(f"Request probe '{name}'", d.SimConnect_RequestClientData,
                      cd, REQ_PROBE_BASE + i, DEF_PROBE_BASE + i, PERIOD_SECOND, FLAG_DEFAULT, 0, 0, 0)

    def request_full(self, i):
        name = self.names[i]
        self.full_requested.add(name)
        self.call(f"Request full '{name}' ({MCDU_DATA_SIZE} Bytes)", self.dll.SimConnect_RequestClientData,
                  CD_BASE + i, REQ_FULL_BASE + i, DEF_FULL_BASE + i, PERIOD_SECOND, FLAG_DEFAULT, 0, 0, 0)

    def handle(self, p_recv):
        rid = p_recv.contents.dwID
        if rid == RECV_ID_OPEN:
            print("Verbunden mit MSFS.")
        elif rid == RECV_ID_QUIT:
            print("MSFS wurde beendet.")
            return False
        elif rid == RECV_ID_SYSTEM_STATE:
            st = ctypes.cast(p_recv, ctypes.POINTER(RECV_SYSTEM_STATE)).contents
            if st.dwRequestID == REQ_AIRCRAFT:
                print(f"Geladenes Flugzeug: {st.szString.decode(errors='replace')}")
        elif rid == RECV_ID_EXCEPTION:
            ex = ctypes.cast(p_recv, ctypes.POINTER(RECV_EXCEPTION)).contents
            what = self.send_ids.get(ex.dwSendID, f"SendID {ex.dwSendID}")
            key = (ex.dwException, what)
            if key not in self.seen_exceptions:  # periodische Requests melden denselben Fehler jede Sekunde
                self.seen_exceptions.add(key)
                print(f"  ! Exception {EXCEPTION_NAMES.get(ex.dwException, ex.dwException)} bei {what}")
        elif rid == RECV_ID_CLIENT_DATA:
            cd = ctypes.cast(p_recv, ctypes.POINTER(RECV_CLIENT_DATA)).contents
            payload_addr = ctypes.addressof(cd) + ctypes.sizeof(RECV_CLIENT_DATA)
            payload_len = cd.dwSize - ctypes.sizeof(RECV_CLIENT_DATA)
            data = ctypes.string_at(payload_addr, max(0, payload_len))
            req = cd.dwRequestID
            if REQ_PROBE_BASE <= req < REQ_PROBE_BASE + len(self.names):
                i = req - REQ_PROBE_BASE
                name = self.names[i]
                self.alive[name] = self.alive.get(name, 0) + 1
                if self.alive[name] == 1:
                    print(f"\n*** TREFFER: Client-Data-Bereich '{name}' liefert Daten! ***")
                    self.request_full(i)
            elif REQ_FULL_BASE <= req < REQ_FULL_BASE + len(self.names):
                name = self.names[req - REQ_FULL_BASE]
                data = data[:MCDU_DATA_SIZE]
                if self.last_frame.get(name) != data:
                    self.last_frame[name] = data
                    print_frame(name, data)
        return True

    def run(self, seconds):
        self.open()
        self.subscribe()
        print(f"Teste {len(self.names)} Namen, lausche {seconds} s ... (Strg+C bricht ab)")
        p_recv = ctypes.POINTER(RECV)()
        cb = wintypes.DWORD()
        end = time.time() + seconds
        try:
            while time.time() < end:
                if self.dll.SimConnect_GetNextDispatch(self.h, ctypes.byref(p_recv), ctypes.byref(cb)) == 0:
                    if not self.handle(p_recv):
                        break
                else:
                    time.sleep(0.05)
        except KeyboardInterrupt:
            pass
        finally:
            self.dll.SimConnect_Close(self.h)
        self.summary()

    def summary(self):
        print("\n===== Ergebnis =====")
        if not self.alive:
            print("Keiner der getesteten Namen liefert Daten.")
            print("=> Der A330 bietet (mit diesen Namen) keinen MCDU-Export per SimConnect an.")
            print("   Prüfe vorher im EFB des A330, ob es 'External Hardware' / 'MCDU Export' gibt und ob es aktiv ist.")
        for name, n in self.alive.items():
            frame = self.last_frame.get(name)
            used = sum(1 for k in range(0, len(frame or b""), MCDU_CHAR_SIZE) if frame[k] not in (0, 32))
            print(f"'{name}': {n} Probe-Pakete, volles Raster {'empfangen' if frame else 'NICHT empfangen'}"
                  + (f", {used} belegte Zeichen" if frame else ""))


def print_frame(name, data):
    print(f"\n--- '{name}': {len(data)} Bytes ---")
    if len(data) < MCDU_DATA_SIZE:
        print(f"Weniger Daten als erwartet. Rohdaten: {data[:96].hex(' ')}")
        return
    colors = {}
    print("+" + "-" * MCDU_COLUMNS + "+")
    for row in range(MCDU_ROWS):
        line = []
        for col in range(MCDU_COLUMNS):
            k = (row * MCDU_COLUMNS + col) * MCDU_CHAR_SIZE
            sym, color, flags = data[k], data[k + 1], data[k + 2]
            ch = " " if sym in (0, 32) else SPECIAL_CHARS.get(chr(sym), chr(sym) if 32 < sym < 127 else "?")
            if ch != " ":
                colors[COLOR_NAMES.get(color, color)] = colors.get(COLOR_NAMES.get(color, color), 0) + 1
            line.append(ch)
        print("|" + "".join(line) + "|")
    print("+" + "-" * MCDU_COLUMNS + "+")
    print(f"Farben: {colors}")
    print(f"Erste Bytes: {data[:48].hex(' ')}")


def main():
    ap = argparse.ArgumentParser(description="Prüft, ob das geladene Flugzeug einen MCDU-Export per SimConnect anbietet.")
    ap.add_argument("--seconds", type=int, default=20, help="Wie lange gelauscht wird (Standard 20)")
    ap.add_argument("--names", nargs="*", default=[], help="Zusätzliche Client-Data-Namen")
    ap.add_argument("--only", action="store_true", help="Nur die mit --names angegebenen Namen testen")
    ap.add_argument("--dll", help="Pfad zur SimConnect.dll")
    args = ap.parse_args()

    if sys.maxsize <= 2**32:
        sys.exit("Bitte 64-bit-Python verwenden.")
    names = args.names if args.only else DEFAULT_NAMES + [n for n in args.names if n not in DEFAULT_NAMES]
    dll = find_dll(args.dll)
    print(f"SimConnect.dll: {dll}")
    Probe(dll, names).run(args.seconds)


if __name__ == "__main__":
    main()
