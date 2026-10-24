from __future__ import annotations

import shutil
from pathlib import Path

from .categories import MANUAL_SORT_FOLDER, destination_path, is_category_dir


def _resolve_target(output_root: Path, category_rel: str, name: str, dry_run: bool) -> Path:
    output_root = output_root.resolve()
    dest_parent = destination_path(output_root, category_rel)
    if not dry_run:
        output_root.mkdir(parents=True, exist_ok=True)
        dest_parent.mkdir(parents=True, exist_ok=True)
    return unique_destination(dest_parent, name)


def unique_destination(dest_dir: Path, name: str) -> Path:
    candidate = dest_dir / name
    if not candidate.exists():
        return candidate
    stem = Path(name).stem
    suffix = Path(name).suffix
    n = 2
    while True:
        candidate = dest_dir / f"{stem} ({n}){suffix}"
        if not candidate.exists():
            return candidate
        n += 1


def _prune_empty_ancestors(start: Path, library_root: Path) -> None:
    """Remove empty non-# folders left behind after a move (up to library root)."""
    library_root = library_root.resolve()
    current = start.resolve()

    while current != library_root:
        try:
            current.relative_to(library_root)
        except ValueError:
            return
        if not current.is_dir():
            return
        if is_category_dir(current.name):
            return
        try:
            if any(current.iterdir()):
                return
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _is_under_category_tree(
    path: Path, library_root: Path, *, hash_dirs_are_sorted: bool = True
) -> bool:
    if not hash_dirs_are_sorted:
        return False
    library_root = library_root.resolve()
    try:
        rel = path.relative_to(library_root)
    except ValueError:
        return True
    parts = rel.parts[:-1] if path.is_file() else rel.parts
    return any(is_category_dir(part) for part in parts)


def hoist_to_library_root(
    source: Path,
    library_root: Path,
    *,
    dry_run: bool,
    hash_dirs_are_sorted: bool = True,
) -> Path:
    """
    Move a book file or bundle folder to the library root when it lives in a
    non-# subfolder. Category trees and items already at root are unchanged.
    """
    library_root = library_root.resolve()
    source = source.resolve()
    if source.parent == library_root:
        return source
    if _is_under_category_tree(
        source, library_root, hash_dirs_are_sorted=hash_dirs_are_sorted
    ):
        return source

    target = unique_destination(library_root, source.name)
    if dry_run:
        return target
    parent_before_move = source.parent
    shutil.move(str(source), str(target))
    _prune_empty_ancestors(parent_before_move, library_root)
    return target


def move_item(
    source: Path,
    output_root: Path,
    category_rel: str,
    *,
    dry_run: bool,
    prune_root: Path | None = None,
) -> Path:
    source = source.resolve()
    if not dry_run and not source.exists():
        raise FileNotFoundError(f"Source file or folder no longer exists: {source}")
    target = _resolve_target(output_root, category_rel, source.name, dry_run)
    if dry_run:
        return target
    parent_before_move = source.parent
    shutil.move(str(source), str(target))
    _prune_empty_ancestors(parent_before_move, prune_root or output_root)
    return target


def move_to_manual(
    source: Path,
    output_root: Path,
    *,
    dry_run: bool,
    prune_root: Path | None = None,
) -> Path:
    source = source.resolve()
    if not dry_run and not source.exists():
        raise FileNotFoundError(f"Source file or folder no longer exists: {source}")
    target = _resolve_target(output_root, MANUAL_SORT_FOLDER, source.name, dry_run)
    if dry_run:
        return target
    parent_before_move = source.parent
    shutil.move(str(source), str(target))
    _prune_empty_ancestors(parent_before_move, prune_root or output_root)
    return target
