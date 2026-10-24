from __future__ import annotations

import os
from pathlib import Path

CATEGORY_PREFIX = "# "
MANUAL_SORT_FOLDER = "# MANUAL SORT NEEDED"

ARCHIVE_EXTENSIONS = {".zip", ".rar", ".7z"}

BOOK_EXTENSIONS = {
    ".pdf",
    ".epub",
    ".djvu",
    ".mobi",
    ".azw",
    ".azw3",
    ".cbz",
    ".cbr",
    ".fb2",
    ".xps",
    ".txt",
    ".md",
    ".markdown",
    ".text",
    ".rtf",
    ".docx",
    ".doc",
    ".html",
    ".htm",
    *ARCHIVE_EXTENSIONS,
}


def is_category_dir(name: str) -> bool:
    return name.startswith(CATEGORY_PREFIX)


def category_relative_path(path: Path, library_root: Path) -> str | None:
    """Return posix-style category path like '# Construction/# Blacksmithing' or None."""
    try:
        rel = path.relative_to(library_root)
    except ValueError:
        return None
    parts = [p for p in rel.parts if is_category_dir(p)]
    if not parts:
        return None
    return "/".join(parts)


def discover_category_destinations(library_root: Path) -> list[str]:
    """All category folder paths (each leaf and intermediate) that can receive books."""
    library_root = library_root.resolve()
    seen: set[str] = set()

    for dirpath, dirnames, _ in os.walk(library_root):
        current = Path(dirpath)
        try:
            rel = current.relative_to(library_root)
        except ValueError:
            continue
        rel_parts = [p for p in rel.parts if is_category_dir(p)]
        if not rel_parts:
            continue
        cat = "/".join(rel_parts)
        seen.add(cat)
        for name in dirnames:
            if is_category_dir(name):
                seen.add(f"{cat}/{name}")

    destinations = sorted(seen, key=lambda s: (s.count("/"), s.lower()))
    return [d for d in destinations if d != MANUAL_SORT_FOLDER]


def destination_path(library_root: Path, category_rel: str) -> Path:
    return library_root.joinpath(*category_rel.split("/"))


def ensure_category_dir(library_root: Path, category_rel: str) -> Path:
    dest = destination_path(library_root, category_rel)
    dest.mkdir(parents=True, exist_ok=True)
    return dest


def is_archive_file(path: Path) -> bool:
    return path.is_file() and path.suffix.lower() in ARCHIVE_EXTENSIONS


def is_book_file(path: Path) -> bool:
    suffix = path.suffix.lower()
    return path.is_file() and suffix in BOOK_EXTENSIONS and suffix not in ARCHIVE_EXTENSIONS


CHAPTER_NAME_RE = __import__("re").compile(
    r"(?i)^(?:ch(?:apter)?[\s._-]*\d+|part[\s._-]*\d+|\d{1,3}|[ivxlc]+\.?\s*$|.+[\s._-]ch(?:apter)?[\s._-]*\d+).+\.(pdf|epub)$"
)


def looks_like_chapter_filename(name: str) -> bool:
    if CHAPTER_NAME_RE.match(name):
        return True
    lower = name.lower()
    if "chapter" in lower or lower.startswith("ch") and any(c.isdigit() for c in lower[:6]):
        return True
    stem = Path(name).stem
    if stem.isdigit() and len(stem) <= 3:
        return True
    return False


def is_chapter_bundle_dir(path: Path) -> bool:
    """Directory named like a book title containing mostly chapter-split files."""
    if not path.is_dir() or is_category_dir(path.name):
        return False
    children = list(path.iterdir())
    if not children:
        return False
    book_files = [c for c in children if c.is_file() and is_book_file(c)]
    if len(book_files) < 2:
        return False
    chapter_like = sum(1 for f in book_files if looks_like_chapter_filename(f.name))
    return chapter_like >= max(2, len(book_files) * 0.5)


def is_sort_candidate_path(
    item: Path,
    library_root: Path,
    *,
    sort_from_folders: set[str],
    skip_manual: bool,
    hash_dirs_are_sorted: bool = True,
) -> bool:
    """Whether this file or bundle dir should be classified and moved."""
    library_root = library_root.resolve()
    item = item.resolve()
    try:
        rel = item.relative_to(library_root)
    except ValueError:
        return False

    if skip_manual and MANUAL_SORT_FOLDER in rel.parts:
        return False

    if sort_from_folders:
        for folder in sort_from_folders:
            if folder in rel.parts:
                return True
        return False

    if not hash_dirs_are_sorted:
        return True

    # Input is the sorted library: skip items already under a # category tree
    for part in rel.parts[:-1] if item.is_file() else rel.parts:
        if is_category_dir(part):
            return False
    return True
