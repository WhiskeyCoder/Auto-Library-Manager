from __future__ import annotations

import json
import threading
from concurrent.futures import FIRST_COMPLETED, Future, ThreadPoolExecutor, wait
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from .archive_extract import process_archive_items
from .categories import (
    MANUAL_SORT_FOLDER,
    discover_category_destinations,
    ensure_category_dir,
    is_archive_file,
)
from .categorize import (
    classify_and_move_categorize,
    format_classify_log,
    format_sample_log,
)
from .llm import LocalLLMClient
from .models import ClassificationResult
from .open_catalog import DEFAULT_REGISTRY_NAME, OpenCategoryCatalog
from .paths import effective_output_root
from .job_callbacks import JobCallbacks


@dataclass
class CategorizeConfig:
    library_root: Path
    output_root: Path | None = None
    llm_url: str = "http://127.0.0.1:1234"
    model: str | None = None
    dry_run: bool = False
    pages: int = 5
    limit: int = 0
    from_folders: list[str] = field(default_factory=list)
    include_manual: bool = False
    registry_path: Path | None = None
    log_path: Path | None = None
    concurrency: int = 4
    save_registry: bool = True
    use_ocr: bool = True
    flatten_to_root: bool = True


@dataclass
class CategorizeStats:
    sorted_ok: int = 0
    manual: int = 0
    errors: int = 0
    cancelled: bool = False


def run_categorize(config: CategorizeConfig, callbacks: JobCallbacks) -> CategorizeStats:
    stats = CategorizeStats()
    library_root = config.library_root.resolve()
    output_root = effective_output_root(library_root, config.output_root)
    try:
        output_root.mkdir(parents=True, exist_ok=True)
        if not config.dry_run:
            ensure_category_dir(output_root, MANUAL_SORT_FOLDER)
    except OSError as exc:
        callbacks.on_log(f"Cannot create output folder {output_root}: {exc}")
        return stats

    registry = config.registry_path or (output_root / DEFAULT_REGISTRY_NAME)
    disk_categories = discover_category_destinations(output_root)
    catalog = OpenCategoryCatalog.load(
        output_root,
        registry_path=registry,
        disk_categories=disk_categories,
    )
    catalog.persist_registry = config.save_registry
    if output_root != library_root:
        callbacks.on_log(f"Input (scan): {library_root}")
        callbacks.on_log(f"Output (sorted): {output_root}")
    callbacks.on_log(
        f"AI-chosen paths | known: {len(catalog.known_logical_paths())} "
        f"(disk + {registry.name})"
    )

    sort_from = set(config.from_folders)
    hash_dirs_are_sorted = library_root.resolve() == output_root.resolve()
    if not hash_dirs_are_sorted:
        callbacks.on_log(
            "Input ≠ output: scanning all nested subfolders (including inside # folders)."
        )
    from .scanner import SortItem, collect_sort_items

    items = collect_sort_items(
        library_root,
        sort_from_folders=sort_from,
        skip_manual=not config.include_manual,
        hash_dirs_are_sorted=hash_dirs_are_sorted,
    )
    if config.limit > 0:
        items = items[: config.limit]

    if not items:
        callbacks.on_log("No books matched the current filters.")
        return stats

    workers = max(1, min(config.concurrency, 16))
    log_path = config.log_path or (output_root / ".library-categorize-log.jsonl")
    log_lock = threading.Lock()

    def safe_log(msg: str) -> None:
        with log_lock:
            callbacks.on_log(msg)

    total = len(items)
    done_count = 0
    progress_lock = threading.Lock()

    try:
        llm = LocalLLMClient(
            base_url=config.llm_url,
            model=config.model,
            max_concurrent_requests=workers,
        )
    except Exception as exc:
        callbacks.on_log(f"Cannot reach LLM at {config.llm_url}: {exc}")
        return stats

    callbacks.on_log(
        f"Pipeline: extract {config.pages} page(s) → one LLM call (Title+Content) → manual | "
        f"Model: {llm.model} | Items: {total} | LLM concurrency: {workers} | "
        f"Dry run: {config.dry_run}"
    )

    def process_one(idx: int, item) -> None:
        nonlocal done_count
        if callbacks.should_cancel():
            return
        safe_log(f"[{idx}/{total}] {item.relative_path}")
        try:

            def run_book(sub: SortItem) -> None:
                source_path = sub.path.resolve()
                outcome = classify_and_move_categorize(
                    sub,
                    library_root=library_root,
                    output_root=output_root,
                    catalog=catalog,
                    llm=llm,
                    dry_run=config.dry_run,
                    pages=config.pages,
                    use_ocr=config.use_ocr,
                    flatten_to_root=config.flatten_to_root,
                    hash_dirs_are_sorted=hash_dirs_are_sorted,
                )
                if outcome.item.path.resolve() != source_path:
                    safe_log(f"  hoist → library root ({outcome.item.path.name})")
                safe_log(format_sample_log(outcome.book_sample, config.pages))
                used_content = bool(
                    outcome.book_sample and outcome.book_sample.substantive
                )
                safe_log(
                    format_classify_log(
                        outcome.classify_result, used_content=used_content
                    )
                )
                record = {
                    "ts": datetime.now(timezone.utc).isoformat(),
                    "pipeline": "categorize-open",
                    "item": sub.relative_path,
                    "title": sub.display_title,
                    "bundle": sub.is_bundle,
                    "category": outcome.destination,
                    "logical_path": outcome.result.logical_path,
                    "is_new_subcategory": outcome.result.is_new_subcategory,
                    "confidence": outcome.result.confidence,
                    "source": outcome.result.source,
                    "reason": outcome.result.reason,
                    "classify_step": _step_record(outcome.classify_result),
                    "extract": _sample_record(outcome.book_sample),
                    "moved_to": str(outcome.moved_to) if outcome.moved_to else None,
                    "dry_run": outcome.dry_run,
                }
                with log_lock:
                    with log_path.open("a", encoding="utf-8") as f:
                        f.write(json.dumps(record, ensure_ascii=False) + "\n")
                if outcome.destination == MANUAL_SORT_FOLDER:
                    stats.manual += 1
                    safe_log(
                        f"  → manual check ({MANUAL_SORT_FOLDER}) — {outcome.result.reason}"
                    )
                else:
                    stats.sorted_ok += 1
                    tag = "NEW " if outcome.result.is_new_subcategory else ""
                    display = outcome.result.logical_path or outcome.destination
                    safe_log(
                        f"  → {tag}{display.replace('/', ' / ')} "
                        f"[{outcome.result.confidence}, {outcome.result.source}]"
                    )

            if is_archive_file(item.path):
                process_archive_items(
                    item,
                    library_root,
                    output_root,
                    dry_run=config.dry_run,
                    flatten_to_root=config.flatten_to_root,
                    on_log=safe_log,
                    process_book=run_book,
                )
            else:
                run_book(item)
        except Exception as exc:
            stats.errors += 1
            safe_log(f"  ERROR: {exc}")
        finally:
            with progress_lock:
                done_count += 1
                callbacks.on_progress(done_count, total)

    if workers == 1:
        for idx, item in enumerate(items, start=1):
            if callbacks.should_cancel():
                stats.cancelled = True
                break
            process_one(idx, item)
    else:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures: list[Future[None]] = []
            for idx, item in enumerate(items, start=1):
                if callbacks.should_cancel():
                    stats.cancelled = True
                    break
                futures.append(pool.submit(process_one, idx, item))
            pending = set(futures)
            while pending:
                if callbacks.should_cancel():
                    stats.cancelled = True
                    for fut in pending:
                        fut.cancel()
                    break
                finished, pending = wait(pending, timeout=0.25, return_when=FIRST_COMPLETED)

    callbacks.on_log(
        f"Finished. Categorized: {stats.sorted_ok}, manual: {stats.manual}, "
        f"errors: {stats.errors}"
        + (" (cancelled)" if stats.cancelled else "")
    )
    return stats


def _sample_record(sample: object) -> dict | None:
    from .text_extract import BookSample

    if not isinstance(sample, BookSample):
        return None
    return {
        "method": sample.method,
        "file_format": sample.file_format,
        "char_count": sample.char_count,
        "substantive": sample.substantive,
        "pages_attempted": sample.pages_attempted,
        "ocr_attempted": sample.ocr_attempted,
        "ocr_note": sample.ocr_note,
    }


def _step_record(result: object) -> dict | None:
    if not isinstance(result, ClassificationResult):
        return None
    return {
        "category": result.category,
        "logical_path": result.logical_path,
        "confidence": result.confidence,
        "reason": result.reason,
    }
