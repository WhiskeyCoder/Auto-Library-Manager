"""Extract pages → one LLM call (Title + Content) → manual."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .categories import MANUAL_SORT_FOLDER
from .llm import LocalLLMClient
from .models import ClassificationResult
from .mover import hoist_to_library_root, move_item, move_to_manual
from .open_catalog import OpenCategoryCatalog
from .scanner import SortItem, repoint_sort_item
from .text_extract import (
    BookSample,
    build_classification_title,
    extract_book_sample,
    pick_sample_file_for_bundle,
)
from .taxonomy_validate import scrub_content_for_classification
from .taxonomy import disk_to_logical


@dataclass
class CategorizeOutcome:
    item: SortItem
    result: ClassificationResult
    destination: str | None
    moved_to: Path | None
    dry_run: bool
    classify_result: ClassificationResult | None = None
    book_sample: BookSample | None = None


def _accept(result: ClassificationResult) -> bool:
    return result.confidence == "high" and bool(result.category)


def _with_source(result: ClassificationResult, source: str) -> ClassificationResult:
    return ClassificationResult(
        result.category,
        result.confidence,
        result.reason,
        source=source,
        is_new_subcategory=result.is_new_subcategory,
        logical_path=result.logical_path,
        guide_description=result.guide_description,
    )


def _finalize_success(
    item: SortItem,
    result: ClassificationResult,
    *,
    scan_root: Path,
    output_root: Path,
    catalog: OpenCategoryCatalog,
    dry_run: bool,
) -> CategorizeOutcome:
    if result.is_new_subcategory and result.logical_path:
        catalog.register_path(
            result.logical_path,
            result.guide_description,
            dry_run=dry_run,
        )
    elif result.category:
        catalog.ensure_on_disk(result.category, dry_run=dry_run)

    moved = move_item(
        item.path,
        output_root,
        result.category,
        dry_run=dry_run,
        prune_root=scan_root,
    )
    return CategorizeOutcome(item, result, result.category, moved, dry_run)


def _display_path(result: ClassificationResult) -> str:
    if result.logical_path:
        return result.logical_path.replace("/", " / ")
    if result.category:
        return disk_to_logical(result.category).replace("/", " / ")
    return "unknown"


def classify_and_move_categorize(
    item: SortItem,
    *,
    library_root: Path,
    output_root: Path,
    catalog: OpenCategoryCatalog,
    llm: LocalLLMClient,
    dry_run: bool,
    pages: int,
    use_ocr: bool = True,
    flatten_to_root: bool = True,
    hash_dirs_are_sorted: bool = True,
) -> CategorizeOutcome:
    if flatten_to_root and not item.skip_hoist:
        hoisted = hoist_to_library_root(
            item.path,
            library_root,
            dry_run=dry_run,
            hash_dirs_are_sorted=hash_dirs_are_sorted,
        )
        if hoisted != item.path.resolve():
            item = repoint_sort_item(item, hoisted, library_root)

    label = build_classification_title(
        item.path, item.display_title, source_archive=item.source_archive
    )

    sample_path = item.path
    if item.is_bundle:
        picked = pick_sample_file_for_bundle(item.path)
        if picked:
            sample_path = picked

    book_sample = extract_book_sample(
        sample_path, max_pages=pages, use_ocr=use_ocr
    )

    page_text = book_sample.text if book_sample.substantive else None
    if page_text:
        page_text = scrub_content_for_classification(page_text)
        if len(page_text.strip()) < 80:
            page_text = book_sample.text
    source = "combined" if page_text else "title"

    raw = llm.classify_open(
        title=label,
        relative_path=item.relative_path,
        catalog=catalog,
        sample_text=page_text,
    )
    classify_result = _with_source(raw, source)

    if _accept(classify_result):
        out = _finalize_success(
            item,
            classify_result,
            scan_root=library_root,
            output_root=output_root,
            catalog=catalog,
            dry_run=dry_run,
        )
        out.classify_result = classify_result
        out.book_sample = book_sample
        return out

    if classify_result.category and classify_result.confidence == "low":
        reason = (
            f"Model suggested {_display_path(classify_result)} but was not confident "
            f"({classify_result.reason})"
        )
    elif classify_result.reason:
        reason = classify_result.reason
    elif book_sample.substantive:
        reason = "Title + content did not yield high confidence"
    elif book_sample.ocr_note:
        reason = f"Title-only failed; no page text ({book_sample.ocr_note})"
    else:
        reason = "Could not classify from title or first pages"

    final = ClassificationResult(None, "none", reason, source=source)
    moved = move_to_manual(
        item.path, output_root, dry_run=dry_run, prune_root=library_root
    )
    return CategorizeOutcome(
        item,
        final,
        MANUAL_SORT_FOLDER,
        moved,
        dry_run,
        classify_result=classify_result,
        book_sample=book_sample,
    )


def format_sample_log(sample: BookSample | None, pages: int) -> str:
    if sample is None:
        return f"  extract → (not run; first {pages} pages)"
    fmt = sample.file_format or "file"
    if sample.substantive:
        unit = "section(s)" if fmt == "epub" else "page(s)"
        return (
            f"  extract → {sample.char_count} chars from {fmt} via {sample.method} "
            f"({sample.pages_attempted} {unit})"
        )
    if sample.ocr_note:
        return f"  extract → no usable text from {fmt} — {sample.ocr_note}"
    if sample.ocr_attempted:
        return f"  extract → no usable text from {fmt} after OCR ({pages} pages)"
    if fmt == "djvu":
        return f"  extract → could not read DJVU (MuPDF); file may be damaged"
    if fmt == "epub":
        return f"  extract → could not read EPUB body (tried MuPDF + ebooklib)"
    if fmt in {"txt", "md", "markdown", "text", "docx", "rtf", "html", "htm"}:
        return f"  extract → could not read {fmt.upper()} content"
    return (
        f"  extract → no text from first {pages} page(s) of {fmt}"
        + ("; use OCR for scans" if fmt == "pdf" else "")
    )


def format_classify_log(result: ClassificationResult | None, *, used_content: bool) -> str:
    mode = "title+content" if used_content else "title-only"
    if result is None:
        return f"  classify → ({mode}) no response"
    if _accept(result):
        return f"  classify → ({mode}) {_display_path(result)} [{result.confidence}]"
    if result.category:
        return (
            f"  classify → ({mode}) {_display_path(result)} [{result.confidence}] "
            f"— not confident"
        )
    return f"  classify → ({mode}) unknown [{result.confidence}]"
