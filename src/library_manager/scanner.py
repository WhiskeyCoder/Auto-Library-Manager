from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .categories import (
    MANUAL_SORT_FOLDER,
    is_archive_file,
    is_book_file,
    is_category_dir,
    is_chapter_bundle_dir,
    is_sort_candidate_path,
)


@dataclass(frozen=True)
class SortItem:
    """A single book file or a chapter-split folder to move as one unit."""

    path: Path
    display_title: str
    relative_path: str
    is_bundle: bool
    source_archive: str | None = None
    skip_hoist: bool = False


def is_internal_workspace_path(path: Path, library_root: Path) -> bool:
    """Temporary extract dirs — only the archive pipeline should touch these."""
    try:
        rel = path.relative_to(library_root.resolve())
    except ValueError:
        return False
    if ".library-extract" in rel.parts:
        return True
    return any(part.startswith("_nested_") for part in rel.parts)


def collect_sort_items(
    library_root: Path,
    *,
    sort_from_folders: set[str] | None = None,
    skip_manual: bool = True,
    hash_dirs_are_sorted: bool = True,
) -> list[SortItem]:
    library_root = library_root.resolve()
    sort_from = sort_from_folders or set()
    items: list[SortItem] = []
    seen_bundles: set[Path] = set()
    candidate_kw = {
        "sort_from_folders": sort_from,
        "skip_manual": skip_manual,
        "hash_dirs_are_sorted": hash_dirs_are_sorted,
    }

    for dirpath, dirnames, filenames in _walk(library_root):
        current = Path(dirpath)
        dirnames[:] = [
            d
            for d in dirnames
            if d not in {".library-extract"}
            and not d.startswith("_nested_")
            and _should_descend_into(
                d, current, library_root, sort_from, hash_dirs_are_sorted
            )
        ]

        for name in sorted(dirnames):
            child = current / name
            if is_chapter_bundle_dir(child) and is_sort_candidate_path(
                child, library_root, **candidate_kw
            ):
                bundle = child.resolve()
                if bundle in seen_bundles:
                    continue
                seen_bundles.add(bundle)
                rel = bundle.relative_to(library_root).as_posix()
                items.append(
                    SortItem(
                        path=bundle,
                        display_title=bundle.name,
                        relative_path=rel,
                        is_bundle=True,
                    )
                )

        for name in sorted(filenames):
            path = current / name
            if is_internal_workspace_path(path, library_root):
                continue
            if is_archive_file(path):
                if not is_sort_candidate_path(path, library_root, **candidate_kw):
                    continue
                rel = path.relative_to(library_root).as_posix()
                items.append(
                    SortItem(
                        path=path.resolve(),
                        display_title=path.stem,
                        relative_path=rel,
                        is_bundle=False,
                    )
                )
                continue
            if not is_book_file(path):
                continue
            if path.parent.resolve() in seen_bundles:
                continue
            if not is_sort_candidate_path(path, library_root, **candidate_kw):
                continue
            rel = path.relative_to(library_root).as_posix()
            items.append(
                SortItem(
                    path=path.resolve(),
                    display_title=path.stem,
                    relative_path=rel,
                    is_bundle=False,
                )
            )

    return items


def repoint_sort_item(item: SortItem, new_path: Path, library_root: Path) -> SortItem:
    new_path = new_path.resolve()
    library_root = library_root.resolve()
    return SortItem(
        path=new_path,
        display_title=item.display_title,
        relative_path=new_path.relative_to(library_root).as_posix(),
        is_bundle=item.is_bundle,
        source_archive=item.source_archive,
        skip_hoist=item.skip_hoist,
    )


def _walk(root: Path):
    import os

    for dirpath, dirnames, filenames in os.walk(root):
        yield dirpath, dirnames, filenames


def _should_descend_into(
    name: str,
    parent: Path,
    library_root: Path,
    sort_from_folders: set[str],
    hash_dirs_are_sorted: bool = True,
) -> bool:
    if not hash_dirs_are_sorted:
        return True
    if not is_category_dir(name):
        return True
    if not sort_from_folders:
        return False
    if name in sort_from_folders:
        return True
    try:
        rel = parent.relative_to(library_root)
    except ValueError:
        return False
    return any(folder in rel.parts for folder in sort_from_folders)
