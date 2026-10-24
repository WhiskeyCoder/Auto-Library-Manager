#!/usr/bin/env python3
"""Launch the Auto Library Manager GUI (adds ./src to sys.path)."""
from __future__ import annotations

import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from library_manager.gui import main

if __name__ == "__main__":
    raise SystemExit(main())
