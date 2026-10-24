from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path


@dataclass(frozen=True)
class TaxonomyEntry:
    logical_path: str
    description: str
    disk_path: str


@dataclass
class Taxonomy:
    folder_prefix: str
    entries: list[TaxonomyEntry] = field(default_factory=list)

    @property
    def descriptions_by_disk(self) -> dict[str, str]:
        return {e.disk_path: e.description for e in self.entries if e.description}

    def description_for(self, disk_category: str) -> str:
        direct = self.descriptions_by_disk.get(disk_category)
        if direct:
            return direct
        logical = disk_to_logical(disk_category).lower()
        for entry in self.entries:
            if entry.logical_path.lower() == logical:
                return entry.description
        return ""


def disk_to_logical(disk_path: str) -> str:
    parts: list[str] = []
    for segment in disk_path.replace("\\", "/").split("/"):
        segment = segment.strip()
        if not segment:
            continue
        if segment.startswith("# "):
            parts.append(segment[2:].strip())
        elif segment.startswith("#"):
            parts.append(segment[1:].strip())
        else:
            parts.append(segment)
    return "/".join(parts)


def logical_to_disk(logical_path: str, folder_prefix: str = "# ") -> str:
    segments = [s.strip() for s in logical_path.replace("\\", "/").split("/") if s.strip()]
    return "/".join(f"{folder_prefix}{segment}" for segment in segments)


def load_taxonomy(path: Path) -> Taxonomy:
    raw = json.loads(path.read_text(encoding="utf-8"))
    folder_prefix = str(raw.get("folder_prefix", "# "))
    entries: list[TaxonomyEntry] = []
    # allow_new_subcategories read by CLI via raw JSON

    categories = raw.get("categories")
    if isinstance(categories, dict):
        for logical, description in categories.items():
            entries.append(_entry(str(logical), str(description), folder_prefix))
    elif isinstance(categories, list):
        for item in categories:
            if isinstance(item, str):
                entries.append(_entry(item, "", folder_prefix))
            elif isinstance(item, dict):
                logical = str(item.get("path") or item.get("category") or "").strip()
                description = str(item.get("description") or item.get("desc") or "").strip()
                disk_override = item.get("disk_path") or item.get("folder")
                if disk_override:
                    disk_path = str(disk_override).replace("\\", "/")
                else:
                    disk_path = logical_to_disk(logical, folder_prefix)
                entries.append(
                    TaxonomyEntry(
                        logical_path=logical.replace("\\", "/"),
                        description=description,
                        disk_path=disk_path,
                    )
                )
        entries = _dedupe_entries(entries)
    else:
        raise ValueError("taxonomy JSON must have a 'categories' object or array")

    if not entries and isinstance(categories, list):
        for item in categories:
            if isinstance(item, dict) and not item.get("path"):
                raise ValueError("taxonomy category object missing 'path'")

    return Taxonomy(folder_prefix=folder_prefix, entries=entries)


def _dedupe_entries(entries: list[TaxonomyEntry]) -> list[TaxonomyEntry]:
    """Merge duplicate logical paths (case-insensitive); keep the longer description."""
    by_key: dict[str, TaxonomyEntry] = {}
    for entry in entries:
        key = entry.logical_path.replace("\\", "/").strip("/").lower()
        if not key:
            continue
        existing = by_key.get(key)
        if not existing:
            by_key[key] = entry
            continue
        if len(entry.description) > len(existing.description):
            by_key[key] = entry
    return sorted(by_key.values(), key=lambda e: e.logical_path.lower())


def _entry(logical: str, description: str, folder_prefix: str) -> TaxonomyEntry:
    logical = logical.replace("\\", "/").strip("/")
    return TaxonomyEntry(
        logical_path=logical,
        description=description.strip(),
        disk_path=logical_to_disk(logical, folder_prefix),
    )


def merge_descriptions(
    discovered_categories: list[str],
    taxonomy: Taxonomy | None,
) -> dict[str, str]:
    """Map each on-disk category path to prompt description text."""
    hints: dict[str, str] = {}
    if not taxonomy:
        return hints

    by_logical = {e.logical_path.lower(): e.description for e in taxonomy.entries}
    for cat in discovered_categories:
        logical = disk_to_logical(cat).lower()
        if logical in by_logical and by_logical[logical]:
            hints[cat] = by_logical[logical]
            continue
        for entry in taxonomy.entries:
            if cat == entry.disk_path or cat.startswith(entry.disk_path + "/"):
                if entry.description:
                    hints[cat] = entry.description
                break
    return hints


def enrich_search_text(disk_category: str, description: str) -> str:
    base = disk_to_logical(disk_category).replace("/", " ")
    if description:
        return f"{base} {description}"
    return base


def append_taxonomy_entry(path: Path, logical_path: str, description: str) -> bool:
    """Append a category to category-guide.json if missing. Returns True if added."""
    logical_path = logical_path.replace("\\", "/").strip("/")
    data = json.loads(path.read_text(encoding="utf-8"))
    categories = data.get("categories")

    if isinstance(categories, list):
        for item in categories:
            if not isinstance(item, dict):
                continue
            existing = str(item.get("path") or "").replace("\\", "/").strip("/")
            if existing.lower() == logical_path.lower():
                return False
        categories.append(
            {
                "path": logical_path,
                "description": description.strip(),
                "auto_created": True,
            }
        )
    elif isinstance(categories, dict):
        if logical_path in categories:
            return False
        categories[logical_path] = description.strip()
    else:
        raise ValueError("taxonomy JSON must have a 'categories' object or array")

    path.write_text(
        json.dumps(data, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return True


def all_taxonomy_disk_paths(taxonomy: Taxonomy) -> list[str]:
    return [e.disk_path for e in taxonomy.entries]


def write_example_taxonomy(path: Path) -> None:
    example = {
        "folder_prefix": "# ",
        "allow_new_subcategories": True,
        "categories": [
            {
                "path": "Science/Biology",
                "description": "General biology and life sciences.",
            },
            {
                "path": "Cooking/Baking",
                "description": "Baking, bread, pastries, and oven techniques.",
            },
            {
                "path": "History/Ancient Rome",
                "description": "Roman history, culture, and primary sources.",
            },
        ],
    }
    path.write_text(json.dumps(example, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
