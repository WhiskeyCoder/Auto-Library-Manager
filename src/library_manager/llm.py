from __future__ import annotations

import json
import re
import threading
from typing import Any, Literal

import requests

from .models import ClassificationResult
from .open_catalog import OpenCategoryCatalog, canonicalize_logical
from .prompts_open import build_open_user_prompt, open_system_prompt
from .taxonomy import disk_to_logical
from .title_match import shortlist_for_llm

Phase = Literal["title", "excerpt", "combined"]


class LocalLLMClient:
    def __init__(
        self,
        base_url: str = "http://127.0.0.1:1234",
        model: str | None = None,
        timeout: float = 180.0,
        max_concurrent_requests: int = 1,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model = model or self._detect_model()
        self._request_semaphore = threading.Semaphore(max(1, max_concurrent_requests))

    def _detect_model(self) -> str:
        try:
            r = requests.get(f"{self.base_url}/v1/models", timeout=10)
            r.raise_for_status()
            data = r.json()
            models = data.get("data") or []
            if models:
                return models[0]["id"]
        except Exception:
            pass
        return "local-model"

    def classify_open(
        self,
        *,
        title: str,
        relative_path: str,
        catalog: OpenCategoryCatalog,
        sample_text: str | None,
        phase: Phase | None = None,
    ) -> ClassificationResult:
        effective_phase: Phase
        if phase is not None:
            effective_phase = phase
        elif sample_text and sample_text.strip():
            effective_phase = "combined"
        else:
            effective_phase = "title"

        entries = _open_prompt_entries(title, catalog)
        prompt = build_open_user_prompt(
            title=title,
            relative_path=relative_path,
            phase=effective_phase,
            sample_text=sample_text,
            existing_entries=entries,
        )
        raw = self._chat(
            prompt,
            phase=effective_phase,
            system_prompt_override=open_system_prompt(effective_phase),
        )
        return _parse_open_classification(raw, catalog)

    def _chat(
        self,
        user_prompt: str,
        *,
        phase: Phase,
        system_prompt_override: str | None = None,
    ) -> str:
        if system_prompt_override is None:
            raise ValueError("system_prompt_override is required")
        sys_content = system_prompt_override
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": sys_content},
                {"role": "user", "content": user_prompt},
            ],
            "temperature": 0.05,
            "max_tokens": 768,
        }
        with self._request_semaphore:
            r = requests.post(
                f"{self.base_url}/v1/chat/completions",
                json=payload,
                timeout=self.timeout,
            )
        r.raise_for_status()
        data = r.json()
        return data["choices"][0]["message"]["content"]


def _open_prompt_entries(title: str, catalog: OpenCategoryCatalog, limit: int = 80) -> list[tuple[str, str]]:
    from .taxonomy import logical_to_disk

    all_entries = catalog.prompt_entries()
    if len(all_entries) <= limit:
        return all_entries
    hints = catalog.hints_for_disk()
    disks = [logical_to_disk(logical, catalog.folder_prefix) for logical, _ in all_entries]

    short_disks = shortlist_for_llm(title, disks, limit=limit, category_hints=hints)
    picked: list[tuple[str, str]] = []
    seen: set[str] = set()
    for disk in short_disks:
        logical = disk_to_logical(disk)
        if logical in seen:
            continue
        seen.add(logical)
        for l, d in all_entries:
            if l == logical:
                picked.append((l, d))
                break
    for logical, desc in all_entries:
        if logical in seen:
            continue
        if logical.count("/") == 0:
            picked.append((logical, desc))
            seen.add(logical)
        if len(picked) >= limit:
            break
    return picked[:limit]


def _parse_open_classification(raw: str, catalog: OpenCategoryCatalog) -> ClassificationResult:
    parsed = _extract_json(raw)
    if not parsed:
        return ClassificationResult(None, "none", "Could not parse model response")

    category = parsed.get("category")
    confidence = str(parsed.get("confidence", "none")).lower()
    reason = str(parsed.get("reason", ""))
    description = str(parsed.get("description") or "")

    if category in (None, "null", ""):
        return ClassificationResult(None, "none", reason)

    raw_cat = str(category).strip()
    if not canonicalize_logical(raw_cat):
        return ClassificationResult(None, "none", "Invalid category path")

    try:
        disk, logical, is_new, desc = catalog.plan_proposal(
            raw_cat, description=description
        )
    except ValueError as exc:
        return ClassificationResult(None, "none", str(exc))

    if confidence not in {"high", "low", "none"}:
        confidence = "low"

    return ClassificationResult(
        disk,
        confidence,
        reason,
        is_new_subcategory=is_new,
        logical_path=logical,
        guide_description=desc,
    )


def _extract_json(raw: str) -> dict[str, Any] | None:
    raw = raw.strip()
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass
    m = re.search(r"\{[\s\S]*\}", raw)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except json.JSONDecodeError:
        return None

