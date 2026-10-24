from __future__ import annotations

import os
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .categories import BOOK_EXTENSIONS, is_category_dir
from .taxonomy import disk_to_logical


@dataclass
class CategoryNode:
    rel_path: str
    direct_books: int = 0
    total_books: int = 0
    children: dict[str, CategoryNode] = field(default_factory=dict)


def _is_book(path: Path) -> bool:
    if not path.is_file():
        return False
    suffix = path.suffix.lower()
    return suffix in BOOK_EXTENSIONS


def scan_category_tree(library_root: Path) -> CategoryNode:
    """Build a tree of # category folders with book counts."""
    library_root = library_root.resolve()
    root = CategoryNode(rel_path="")

    for dirpath, dirnames, filenames in os.walk(library_root):
        current = Path(dirpath)
        try:
            rel = current.relative_to(library_root)
        except ValueError:
            continue

        rel_parts = [p for p in rel.parts if is_category_dir(p)]
        if not rel_parts and current != library_root:
            # Skip non-category folders unless we're at library root
            if not any(is_category_dir(n) for n in dirnames):
                dirnames[:] = [d for d in dirnames if is_category_dir(d)]
            continue

        node = root
        built: list[str] = []
        for part in rel_parts:
            built.append(part)
            key = "/".join(built)
            if key not in node.children:
                node.children[key] = CategoryNode(rel_path=key)
            node = node.children[key]

        direct = sum(1 for name in filenames if _is_book(current / name))
        node.direct_books += direct
        node.total_books += direct

        dirnames[:] = sorted(d for d in dirnames if is_category_dir(d))

    _rollup_totals(root)
    return root


def _rollup_totals(node: CategoryNode) -> int:
    total = node.direct_books
    for child in node.children.values():
        total += _rollup_totals(child)
    node.total_books = total
    return total


def all_category_paths(library_root: Path) -> list[str]:
    """Sorted posix paths of every # category folder (including intermediates)."""
    library_root = library_root.resolve()
    seen: set[str] = set()

    for dirpath, dirnames, _ in os.walk(library_root):
        current = Path(dirpath)
        try:
            rel = current.relative_to(library_root)
        except ValueError:
            continue
        rel_parts = [p for p in rel.parts if is_category_dir(p)]
        if rel_parts:
            seen.add("/".join(rel_parts))
        for name in dirnames:
            if is_category_dir(name):
                parts = rel_parts + [name]
                seen.add("/".join(parts))

    return sorted(seen, key=lambda s: (s.count("/"), s.lower()))


def _logical_tail(rel_path: str) -> str:
    logical = disk_to_logical(rel_path.replace("/", "\\"))
    if "/" in logical:
        return logical.rsplit("/", 1)[-1]
    return logical


def overlap_hints(paths: list[str]) -> list[str]:
    """Human-readable lines about names that appear in more than one place."""
    lines: list[str] = []
    by_tail: dict[str, list[str]] = defaultdict(list)
    top_level: dict[str, str] = {}

    for path in paths:
        tail = _logical_tail(path).lower()
        by_tail[tail].append(path)
        if path.count("/") == 0:
            top_level[tail] = path

    dup_tails = {k: v for k, v in by_tail.items() if len(v) > 1}
    if dup_tails:
        lines.append("=== Same category name in multiple paths (review for merges) ===")
        for tail in sorted(dup_tails):
            lines.append(f"  [{_logical_tail(dup_tails[tail][0])}]")
            for p in sorted(dup_tails[tail], key=str.lower):
                lines.append(f"    {p}")
        lines.append("")

    if top_level:
        lines.append("=== Top-level category also nested elsewhere ===")
        for tail, top_path in sorted(top_level.items(), key=lambda x: x[0]):
            nested = [p for p in paths if p != top_path and _logical_tail(p).lower() == tail]
            if nested:
                lines.append(f"  {top_path}")
                for p in sorted(nested, key=str.lower)[:40]:
                    lines.append(f"    also: {p}")
                if len(nested) > 40:
                    lines.append(f"    ... and {len(nested) - 40} more")
        lines.append("")

    science_like = sorted(
        p for p in paths if p.count("/") == 0 and "science" in _logical_tail(p).lower()
    )
    if len(science_like) > 1:
        lines.append("=== Top-level folders containing 'Science' (often overlap) ===")
        for p in science_like:
            lines.append(f"  {p}")
        lines.append("")

    stem_dupes: dict[str, list[str]] = defaultdict(list)
    for path in paths:
        if path.count("/") != 0:
            continue
        for other in paths:
            if other == path or other.count("/") == 0:
                continue
            if other.startswith(path + "/"):
                continue
            if _logical_tail(path).lower() in disk_to_logical(other).lower():
                stem_dupes[path].append(other)

    related = {k: v for k, v in stem_dupes.items() if v}
    if related:
        lines.append("=== Top-level name appears inside other branches (sample) ===")
        for top in sorted(related, key=str.lower)[:25]:
            lines.append(f"  {top}")
            for p in sorted(related[top], key=str.lower)[:8]:
                lines.append(f"    {p}")
        lines.append("")

    return lines


def _format_tree(node: CategoryNode, indent: int = 0) -> list[str]:
    lines: list[str] = []
    for key in sorted(node.children, key=str.lower):
        child = node.children[key]
        name = key.split("/")[-1]
        prefix = "  " * indent
        if child.direct_books == child.total_books:
            counts = f"{child.total_books} books"
        else:
            counts = f"{child.direct_books} here, {child.total_books} in tree"
        lines.append(f"{prefix}{name}  ({counts})")
        lines.extend(_format_tree(child, indent + 1))
    return lines


def build_inventory_report(library_root: Path) -> str:
    library_root = library_root.resolve()
    if not library_root.is_dir():
        raise FileNotFoundError(f"Not a directory: {library_root}")

    paths = all_category_paths(library_root)
    tree = scan_category_tree(library_root)
    generated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    lines: list[str] = [
        f"Category inventory: {library_root}",
        f"Generated: {generated}",
        f"Category folders: {len(paths)}",
        "",
        "=== Tree (top-level # categories) ===",
    ]
    lines.extend(_format_tree(tree))
    lines.append("")
    lines.append("=== Flat list (every # path) ===")
    for p in paths:
        logical = disk_to_logical(p.replace("/", "\\"))
        depth = p.count("/")
        indent = "  " * depth
        lines.append(f"{indent}{logical}")
    lines.append("")
    lines.extend(overlap_hints(paths))
    return "\n".join(lines).rstrip() + "\n"


def write_category_inventory(library_root: Path, output_path: Path | None = None) -> Path:
    library_root = library_root.resolve()
    if output_path is None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        output_path = library_root / f"category-inventory-{stamp}.txt"
    else:
        output_path = output_path.resolve()

    text = build_inventory_report(library_root)
    output_path.write_text(text, encoding="utf-8")
    return output_path
