from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .category_inventory import write_category_inventory


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write a text inventory of # category folders (for post-run merge review).",
    )
    parser.add_argument(
        "library_root",
        type=Path,
        help="Output root to scan (folder that contains your # category tree)",
    )
    parser.add_argument(
        "-o",
        "--output",
        type=Path,
        default=None,
        help="Report file path (default: <library_root>/category-inventory-<timestamp>.txt)",
    )
    parser.add_argument(
        "--stdout",
        action="store_true",
        help="Print report to stdout instead of writing a file",
    )

    args = parser.parse_args(argv)
    root = args.library_root.resolve()

    if not root.is_dir():
        print(f"Not a directory: {root}", file=sys.stderr)
        return 1

    try:
        if args.stdout:
            from .category_inventory import build_inventory_report

            print(build_inventory_report(root), end="")
            return 0
        out = write_category_inventory(root, args.output)
    except OSError as exc:
        print(f"Failed to write inventory: {exc}", file=sys.stderr)
        return 1

    print(f"Wrote {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
