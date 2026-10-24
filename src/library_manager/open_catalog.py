"""AI-chosen category paths; disk + registry for deduplication only."""

from __future__ import annotations

import json
import re
import threading
from dataclasses import dataclass, field
from pathlib import Path

from .categories import ensure_category_dir
from .taxonomy_validate import sanitize_path_segment, validate_logical_path
from .taxonomy import (
    append_taxonomy_entry,
    disk_to_logical,
    logical_to_disk,
    load_taxonomy,
)

_ACRONYMS = frozenset(
    {
        "api",
        "aws",
        "cpu",
        "css",
        "gcp",
        "gpu",
        "html",
        "ids",
        "iot",
        "ips",
        "llm",
        "nlp",
        "pdf",
        "sql",
        "vpn",
    }
)

DEFAULT_REGISTRY_NAME = "ai-categories.json"


def logical_path_key(logical: str) -> str:
    logical = logical.replace("\\", "/").strip("/")
    parts: list[str] = []
    for segment in logical.split("/"):
        segment = segment.strip()
        if segment.startswith("# "):
            segment = segment[2:].strip()
        elif segment.startswith("#"):
            segment = segment[1:].strip()
        segment = re.sub(r"\s+", " ", segment)
        segment = segment.replace("-", " ")
        if segment:
            parts.append(segment.lower())
    return "/".join(parts)


def canonicalize_logical(raw: str) -> str:
    raw = raw.replace("\\", "/").strip().strip("/")
    if not raw:
        return ""
    parts: list[str] = []
    for segment in raw.split("/"):
        segment = segment.strip()
        if segment.startswith("# "):
            segment = segment[2:].strip()
        elif segment.startswith("#"):
            segment = segment[1:].strip()
        segment = sanitize_path_segment(re.sub(r"\s+", " ", segment))
        if segment:
            parts.append(_title_segment(segment))
    return "/".join(parts)


def _title_segment(segment: str) -> str:
    words = segment.split()
    out: list[str] = []
    for w in words:
        low = w.lower()
        if low in _ACRONYMS:
            out.append(low.upper())
        elif w.isupper() and len(w) <= 6:
            out.append(w)
        else:
            out.append(w[:1].upper() + w[1:].lower() if w else w)
    return " ".join(out)


@dataclass
class OpenCategoryCatalog:
    """Known paths from disk + registry; AI may propose new logical paths."""

    folder_prefix: str
    registry_path: Path
    output_root: Path
    persist_registry: bool = True
    _by_key: dict[str, str] = field(default_factory=dict)
    _descriptions: dict[str, str] = field(default_factory=dict)
    _disk_paths: set[str] = field(default_factory=set)
    _lock: threading.RLock = field(default_factory=threading.RLock)

    @classmethod
    def load(
        cls,
        output_root: Path,
        *,
        registry_path: Path | None = None,
        disk_categories: list[str],
    ) -> OpenCategoryCatalog:
        output_root = output_root.resolve()
        reg = registry_path or (output_root / DEFAULT_REGISTRY_NAME)
        folder_prefix = "# "
        catalog = cls(
            folder_prefix=folder_prefix,
            registry_path=reg,
            output_root=output_root,
        )
        if reg.is_file():
            try:
                tax = load_taxonomy(reg)
                folder_prefix = tax.folder_prefix
                catalog.folder_prefix = folder_prefix
                for entry in tax.entries:
                    catalog._ingest(entry.logical_path, entry.description, entry.disk_path)
            except (OSError, ValueError, json.JSONDecodeError):
                pass
        for disk in disk_categories:
            logical = disk_to_logical(disk)
            catalog._ingest(logical, catalog._descriptions.get(disk, ""), disk)
        return catalog

    def _ingest(self, logical: str, description: str, disk: str) -> None:
        logical = canonicalize_logical(logical)
        if not logical:
            return
        key = logical_path_key(logical)
        self._by_key[key] = logical
        disk = disk.replace("\\", "/")
        self._disk_paths.add(disk)
        if description.strip():
            self._descriptions[disk] = description.strip()

    def known_logical_paths(self) -> list[str]:
        with self._lock:
            return sorted(set(self._by_key.values()), key=str.lower)

    def prompt_entries(self) -> list[tuple[str, str]]:
        with self._lock:
            seen: set[str] = set()
            out: list[tuple[str, str]] = []
            for logical in sorted(set(self._by_key.values()), key=str.lower):
                if logical in seen:
                    continue
                seen.add(logical)
                disk = logical_to_disk(logical, self.folder_prefix)
                out.append((logical, self._descriptions.get(disk, "")))
            return out

    def hints_for_disk(self) -> dict[str, str]:
        with self._lock:
            return dict(self._descriptions)

    def plan_proposal(
        self,
        raw_category: str,
        *,
        description: str = "",
    ) -> tuple[str, str, bool, str]:
        """
        Returns (disk_path, logical_path, is_new, description) without mutating state.
        Merges duplicates via logical_path_key against known paths only.
        """
        proposed = canonicalize_logical(raw_category)
        if not proposed:
            raise ValueError("empty category path")
        path_err = validate_logical_path(proposed)
        if path_err:
            raise ValueError(path_err)
        if proposed.count("/") > 3:
            raise ValueError("category path too deep (max 4 levels)")

        key = logical_path_key(proposed)
        with self._lock:
            if key in self._by_key:
                canonical = self._by_key[key]
                disk = logical_to_disk(canonical, self.folder_prefix)
                desc = self._descriptions.get(disk, description)
                return disk, canonical, False, desc

            canonical = proposed
            disk = logical_to_disk(canonical, self.folder_prefix)
            desc = description.strip() or f"Books related to {canonical.split('/')[-1]}."
            return disk, canonical, True, desc

    def register_path(self, logical_path: str, description: str, *, dry_run: bool) -> str:
        """Record path, create folder, append registry. Returns disk path."""
        logical_path = canonicalize_logical(logical_path)
        key = logical_path_key(logical_path)
        disk = logical_to_disk(logical_path, self.folder_prefix)
        desc = description.strip() or f"Books related to {logical_path.split('/')[-1]}."
        with self._lock:
            self._by_key[key] = logical_path
            self._disk_paths.add(disk)
            self._descriptions[disk] = desc
        self.ensure_on_disk(disk, dry_run=dry_run)
        self.commit_registry(logical_path, desc, dry_run=dry_run)
        return disk

    def commit_registry(self, logical_path: str, description: str, *, dry_run: bool) -> None:
        if dry_run or not self.persist_registry:
            return
        reg = self.registry_path
        if not reg.is_file():
            reg.write_text(
                json.dumps(
                    {
                        "folder_prefix": self.folder_prefix,
                        "allow_new_subcategories": True,
                        "categories": [],
                    },
                    indent=2,
                    ensure_ascii=False,
                )
                + "\n",
                encoding="utf-8",
            )
        append_taxonomy_entry(reg, logical_path, description)

    def ensure_on_disk(self, disk_path: str, *, dry_run: bool) -> None:
        if dry_run:
            return
        ensure_category_dir(self.output_root, disk_path)
