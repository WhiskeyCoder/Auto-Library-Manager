from __future__ import annotations

import argparse
import sys
from pathlib import Path

from .categorize_runner import CategorizeConfig, run_categorize
from .categories import MANUAL_SORT_FOLDER
from .job_callbacks import JobCallbacks
from .paths import effective_output_root


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Smart categorize books into # folders using a local LLM (default 127.0.0.1:1234)",
    )
    parser.add_argument(
        "library_root",
        type=Path,
        help="Input folder to scan for books",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Output folder for # categories (default: same as input)",
    )
    parser.add_argument(
        "--llm-url",
        default="http://127.0.0.1:1234",
        help="OpenAI-compatible API base URL (default: http://127.0.0.1:1234)",
    )
    parser.add_argument(
        "--model",
        default=None,
        help="Model id (default: first model from /v1/models)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Classify and log without moving files",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="Process at most N items (0 = all)",
    )
    parser.add_argument(
        "--pages",
        type=int,
        default=5,
        help="Pages / sections to read for classification (default: 5)",
    )
    parser.add_argument(
        "--from-folder",
        action="append",
        default=[],
        metavar="NAME",
        help="Only process items under this folder name (repeatable)",
    )
    parser.add_argument(
        "--include-manual",
        action="store_true",
        help=f"Also re-process items already in {MANUAL_SORT_FOLDER}",
    )
    parser.add_argument(
        "--log",
        type=Path,
        default=None,
        help="Append JSON lines log (default: output/.library-categorize-log.jsonl)",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=4,
        help="Parallel LLM requests (default: 4, max 16)",
    )
    parser.add_argument(
        "--no-ocr",
        action="store_true",
        help="Disable OCR for scanned PDFs",
    )
    parser.add_argument(
        "--no-flatten",
        action="store_true",
        help="Do not hoist books from subfolders to input root before sorting",
    )

    args = parser.parse_args(argv)
    library_root = args.library_root.resolve()

    if not library_root.is_dir():
        print(f"Not a directory: {library_root}", file=sys.stderr)
        return 1

    config = CategorizeConfig(
        library_root=library_root,
        output_root=args.output,
        llm_url=args.llm_url,
        model=args.model,
        dry_run=args.dry_run,
        pages=args.pages,
        limit=args.limit,
        from_folders=list(args.from_folder),
        include_manual=args.include_manual,
        log_path=args.log,
        concurrency=max(1, min(args.concurrency, 16)),
        use_ocr=not args.no_ocr,
        flatten_to_root=not args.no_flatten,
    )

    out = effective_output_root(library_root, args.output)
    print(f"Input: {library_root}")
    print(f"Output: {out}")
    print(f"LLM: {args.llm_url} (no other network calls)")

    def on_log(msg: str) -> None:
        print(msg)

    stats = run_categorize(
        config,
        JobCallbacks(on_log=on_log, on_progress=lambda _d, _t: None, should_cancel=lambda: False),
    )
    return 0 if stats.errors == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
