from __future__ import annotations

import re
from functools import lru_cache

from .models import ClassificationResult
from .taxonomy import enrich_search_text

_STOPWORDS = frozenset(
    {
        "a",
        "an",
        "the",
        "and",
        "or",
        "of",
        "on",
        "in",
        "to",
        "for",
        "with",
        "from",
        "by",
        "at",
        "is",
        "as",
        "be",
        "it",
        "its",
        "about",
        "quick",
        "ugly",
        "text",
        "book",
        "handbook",
        "guide",
        "manual",
        "basics",
        "advanced",
        "introduction",
        "vol",
        "volume",
        "edition",
        "pdf",
        "year",
    }
)

# Generic title→category hints for shortlisting (not user-specific tuning).
_EXTRA_TITLE_WORDS: dict[str, tuple[str, ...]] = {
    "honey": ("bee", "beekeeping", "apiary", "hive"),
    "bicycle": ("bike", "cycling", "pedal"),
    "pedal": ("bike", "cycling", "bicycle"),
    "electricity": ("electric", "electrical", "electronics"),
    "electric": ("electricity", "electrical", "electronics"),
    "accounting": ("finance", "business", "bookkeeping"),
    "algebra": ("mathematics", "math", "sciences"),
    "python": ("programming", "software", "development"),
    "javascript": ("programming", "web", "software"),
    "cooking": ("food", "recipe", "kitchen"),
    "recipe": ("cooking", "food", "kitchen"),
}


def normalize_title(title: str) -> str:
    t = title.replace("_", " ").replace("-", " ")
    t = re.sub(r"\s+", " ", t).strip().lower()
    return t


@lru_cache(maxsize=4096)
def title_tokens(title: str) -> frozenset[str]:
    norm = normalize_title(title)
    raw = re.findall(r"[a-z0-9]{2,}", norm)
    tokens: set[str] = set()
    for w in raw:
        if w in _STOPWORDS or w.isdigit():
            continue
        tokens.add(w)
        if len(w) > 4 and w.endswith("s"):
            tokens.add(w[:-1])
    expanded: set[str] = set(tokens)
    for w in tokens:
        for extra in _EXTRA_TITLE_WORDS.get(w, ()):
            expanded.add(extra)
    return frozenset(expanded)


def category_search_text(category_rel: str) -> str:
    parts = []
    for segment in category_rel.split("/"):
        segment = segment.strip()
        if segment.startswith("# "):
            segment = segment[2:]
        elif segment.startswith("#"):
            segment = segment[1:].strip()
        parts.append(segment)
    return " ".join(parts).lower()


@lru_cache(maxsize=4096)
def category_tokens(category_rel: str) -> frozenset[str]:
    text = category_search_text(category_rel)
    return frozenset(re.findall(r"[a-z0-9]{2,}", text))


def score_category(
    title: str,
    category_rel: str,
    *,
    category_hints: dict[str, str] | None = None,
) -> float:
    t_tokens = title_tokens(title)
    if not t_tokens:
        return 0.0
    hint = (category_hints or {}).get(category_rel, "")
    combined = enrich_search_text(category_rel, hint).lower()
    c_tokens = frozenset(re.findall(r"[a-z0-9]{2,}", combined))
    norm_title = normalize_title(title)
    c_text = combined

    score = 0.0
    for ct in c_tokens:
        if ct in _STOPWORDS:
            continue
        if ct in t_tokens:
            score += 3.0 if len(ct) >= 5 else 2.0
        elif ct in norm_title:
            score += 2.5

    for ct in c_tokens:
        if len(ct) < 5:
            continue
        if ct in norm_title:
            score += 1.0

    if c_text and c_text in norm_title:
        score += 5.0

    return score


def rank_categories(
    title: str,
    categories: list[str],
    limit: int = 35,
    *,
    category_hints: dict[str, str] | None = None,
) -> list[tuple[str, float]]:
    scored = [
        (c, score_category(title, c, category_hints=category_hints)) for c in categories
    ]
    scored.sort(key=lambda x: (-x[1], x[0].lower()))
    positive = [x for x in scored if x[1] > 0]
    if len(positive) >= limit:
        return positive[:limit]
    # Pad with next-best so the model still has options when title words are obscure.
    out = positive[:]
    for cat, s in scored:
        if len(out) >= limit:
            break
        if cat not in {c for c, _ in out}:
            out.append((cat, s))
    return out


def is_plausible_llm_choice(
    title: str,
    category: str,
    *,
    min_score: float = 1.0,
    category_hints: dict[str, str] | None = None,
) -> bool:
    """Reject obvious wrong picks (e.g. Cooking -> Gambling)."""
    if score_category(title, category, category_hints=category_hints) >= min_score:
        return True
    norm_title = normalize_title(title)
    c_text = category_search_text(category)
    if not c_text:
        return False
    for part in re.split(r"[/\s]+", c_text):
        part = part.strip()
        if len(part) >= 4 and part in norm_title:
            return True
    return False


def keyword_classification(
    title: str,
    categories: list[str],
    *,
    strong_threshold: float = 4.0,
    weak_threshold: float = 3.0,
    category_hints: dict[str, str] | None = None,
) -> ClassificationResult | None:
    from .text_extract import is_weak_filename_title

    if is_weak_filename_title(title):
        return None

    ranked = rank_categories(
        title, categories, limit=5, category_hints=category_hints
    )
    if not ranked:
        return None
    best_cat, best_score = ranked[0]
    second_score = ranked[1][1] if len(ranked) > 1 else 0.0

    if best_score >= strong_threshold:
        return ClassificationResult(
            best_cat,
            "high",
            f"Title keywords match folder ({best_score:.0f})",
            source="keyword",
        )
    if best_score >= weak_threshold and best_score > second_score + 0.5:
        return ClassificationResult(
            best_cat,
            "high",
            f"Best title keyword match ({best_score:.0f})",
            source="keyword",
        )
    return None


def is_plausible_new_subcategory(
    title: str,
    logical_path: str,
    *,
    category_hints: dict[str, str] | None = None,
) -> bool:
    leaf = logical_path.split("/")[-1].strip()
    if not leaf:
        return False
    norm = normalize_title(title)
    leaf_l = leaf.lower()
    if leaf_l in norm.split() or leaf_l in title_tokens(title):
        return True
    from .taxonomy import logical_to_disk

    disk = logical_to_disk(logical_path)
    hint = f"Books about {leaf}"
    return score_category(title, disk, category_hints={**(category_hints or {}), disk: hint}) >= 2.0


def shortlist_for_llm(
    title: str,
    categories: list[str],
    limit: int = 35,
    *,
    category_hints: dict[str, str] | None = None,
) -> list[str]:
    ranked = rank_categories(
        title, categories, limit=limit, category_hints=category_hints
    )
    return [c for c, _ in ranked]
