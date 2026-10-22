import sys
from pathlib import Path

import pytest

# Das Skript liegt im Projektordner, nicht in einem Paket
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

if sys.platform != "win32":
    pytest.skip("Die Bridge läuft nur unter Windows (ctypes/user32/gdi32).", allow_module_level=True)
