from __future__ import annotations

from typing import Literal

Phase = Literal["title", "excerpt", "combined"]


def open_system_prompt(phase: Phase) -> str:
    if phase == "combined":
        return """You are an expert librarian building a clean subject taxonomy for a personal ebook library.

You receive a Title block and a Content block from the first pages of the book.
Use BOTH together: the title often names the subject; the content confirms or refines it.

Invent the best Category / Subcategory path. You are NOT limited to a fixed list. Examples:
- Science / Astronomy
- Cooking / Italian Cuisine
- History / Ancient Rome

Rules:
- Use 2–4 path segments when possible (broad → specific), e.g. Cooking / Campfire Recipes.
- Title Case words; keep acronyms uppercase (NASA, PDF, API).
- No "#" characters in the path.
- NEVER use filenames, URL slugs, HTML ids, wiki redirect labels (e.g. 0-other_names_for_…), or navigation link text as a category.
- Scraped HTML may be noisy — ignore menus and link labels; infer the real subject (e.g. Cooking / Campfire Recipes).
- Reuse an existing library path EXACTLY when equivalent.
- confidence "high" when title and/or content clearly support the path.
- Output JSON only."""

    if phase == "title":
        return """You are an expert librarian building a clean subject taxonomy for a personal ebook library.

You only have the Title block (no page content was extracted — scanned PDF, OCR missing, etc.).
Invent the best path if the title is clear enough; otherwise confidence "low" or "none".

You are NOT limited to a fixed list. Same path rules as combined step (2–4 segments, Title Case, no "#").
Output JSON only."""

    return """You are an expert librarian building a clean subject taxonomy for a personal ebook library.

Decide the PRIMARY subject from the evidence provided.
You are NOT limited to a fixed list. Output JSON only."""


def build_open_user_prompt(
    *,
    title: str,
    relative_path: str,
    phase: Phase,
    sample_text: str | None,
    existing_entries: list[tuple[str, str]],
) -> str:
    existing_block = _format_existing(existing_entries)
    evidence = _evidence_block(title, phase, sample_text)
    bundle = (
        "yes"
        if "/" in relative_path and not relative_path.startswith("#")
        else "no"
    )

    return f"""# Task
Choose or create exactly ONE taxonomy path for this ebook.

# Book location
- Library path: {relative_path}
- Chapter bundle (move as folder): {bundle}
- Classification mode: {phase}

# Paths already used in this library (reuse exact text when equivalent; do not create near-duplicates)
{existing_block}

# Examples (format only)
{{"category": "Science / Botany", "confidence": "high", "reason": "Title and content discuss plant identification.", "description": "Botany and field guides."}}
{{"category": null, "confidence": "low", "reason": "Title is only an ISBN.", "description": ""}}

{evidence}

# JSON shape
{{
  "category": "<Sector / Genre / Sub-Genre or null>",
  "confidence": "high" | "low" | "none",
  "reason": "<one sentence citing title and/or content>",
  "description": "<short guide for this path; required when category is set>"
}}

Respond with JSON only."""


def _format_existing(entries: list[tuple[str, str]]) -> str:
    if not entries:
        return "(none yet — you define the first paths)"
    lines: list[str] = []
    for logical, desc in entries[:120]:
        if desc:
            lines.append(f"- {logical} — {desc}")
        else:
            lines.append(f"- {logical}")
    if len(entries) > 120:
        lines.append(f"… and {len(entries) - 120} more (still reuse if equivalent)")
    return "\n".join(lines)


def _evidence_block(title: str, phase: Phase, sample_text: str | None) -> str:
    if phase == "combined" and sample_text and sample_text.strip():
        content = sample_text.strip()
        if len(content) > 10000:
            content = content[:10000] + "\n…[truncated]"
        return f"""# Evidence (use BOTH sections)

Title:
====
{title}

Content:
----
{content}
----
"""
    if phase == "title":
        return f"""# Evidence

Title:
====
{title}

Content:
----
(not available — no extractable text from first pages)
----
"""
    if not sample_text or not sample_text.strip():
        return "# Evidence\n(empty)\n"
    content = sample_text.strip()
    if len(content) > 10000:
        content = content[:10000] + "\n…[truncated]"
    return f"""# Evidence

Title:
====
{title}

Content:
----
{content}
----
"""
