"""Reject obvious URL/wiki slugs — keep validation narrow so good paths are not blocked."""

from __future__ import annotations

import re

_SLUG_LINE = re.compile(
    r"(other_names_for|other names for|^\s*\d+[-_][a-z0-9_\-]{6,})",
    re.IGNORECASE,
)
_OBVIOUS_SLUG_SEGMENT = re.compile(
    r"other_names_for|names_for|^\d+[-_][a-z0-9_\-]{4,}",
    re.IGNORECASE,
)
_WIN_INVALID = re.compile(r'[<>:"/\\|?*\x00-\x1f]')


def scrub_content_for_classification(text: str) -> str:
    """Drop lines that look like link slugs / redirect labels from scraped HTML."""
    if not text:
        return text
    kept: list[str] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            kept.append(line)
            continue
        if _SLUG_LINE.search(stripped):
            continue
        if re.fullmatch(r"[a-z0-9_\-]{14,}", stripped, re.IGNORECASE):
            continue
        if stripped.count("_") >= 4 and " " not in stripped:
            continue
        kept.append(line)
    return "\n".join(kept)


def sanitize_path_segment(segment: str) -> str:
    """Make a single folder name safe on Windows."""
    segment = segment.strip().strip(".")
    segment = _WIN_INVALID.sub(" ", segment)
    segment = re.sub(r"\s+", " ", segment).strip()
    return segment


def validate_logical_path(logical: str) -> str | None:
    """Return an error message if the path is clearly not a library subject path."""
    logical = logical.replace("\\", "/").strip().strip("/")
    if not logical:
        return "empty category path"
    parts = [p.strip() for p in logical.split("/") if p.strip()]
    if not parts:
        return "empty category path"
    if len(parts) > 4:
        return "category path too deep"
    for seg in parts:
        err = _validate_segment(seg)
        if err:
            return err
    return None


def _validate_segment(seg: str) -> str | None:
    if len(seg) > 96:
        return f"segment too long: {seg[:40]}…"
    if not sanitize_path_segment(seg):
        return "empty segment after sanitization"
    if "_" in seg and " " not in seg:
        return "path looks like a file slug (underscores)"
    if _OBVIOUS_SLUG_SEGMENT.search(seg.replace(" ", "_")):
        return "path looks like a wiki/link slug"
    low = seg.lower()
    if low.startswith("0-other") or low.startswith("0_other"):
        return "path looks like a numbered redirect slug"
    return None
