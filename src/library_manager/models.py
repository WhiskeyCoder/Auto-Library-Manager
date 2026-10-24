from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ClassificationResult:
    category: str | None
    confidence: str  # high | low | none
    reason: str
    source: str = "unknown"  # title | excerpt | keyword | manual
    is_new_subcategory: bool = False
    logical_path: str = ""
    guide_description: str = ""
