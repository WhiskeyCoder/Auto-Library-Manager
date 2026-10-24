from __future__ import annotations

from pathlib import Path


def effective_output_root(library_root: Path, output_root: Path | None) -> Path:
    """Where # category folders and sorted books are written."""
    if output_root is not None:
        text = str(output_root).strip()
        if text:
            return Path(text).resolve()
    return library_root.resolve()
