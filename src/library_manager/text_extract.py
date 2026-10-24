from __future__ import annotations

import re
import shutil
import zipfile
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path

from .categories import BOOK_EXTENSIONS, is_book_file

_MIN_SUBSTANTIVE_CHARS = 80

# PyMuPDF can open these as multi-page documents (incl. EPUB, MOBI, FB2, DJVU, comics).
_FITZ_EXTENSIONS = {
    ".pdf",
    ".mobi",
    ".azw",
    ".azw3",
    ".fb2",
    ".djvu",
    ".xps",
    ".cbz",
    ".cbr",
}

_PLAIN_TEXT_EXTENSIONS = {".txt", ".md", ".markdown", ".text"}

_HTML_EXTENSIONS = {".html", ".htm"}

_DOCX_EXTENSIONS = {".docx"}

_WDOC_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


@dataclass(frozen=True)
class BookSample:
    """Text pulled from a book for the excerpt classification step."""

    text: str
    method: str  # text-layer | ocr | epub | plain-text | docx | empty
    pages_attempted: int
    substantive: bool
    file_format: str = ""
    ocr_attempted: bool = False
    ocr_note: str = ""

    @property
    def char_count(self) -> int:
        return len(self.text.strip())


def extract_sample_text(
    path: Path,
    max_pages: int = 3,
    max_chars: int = 12000,
    *,
    use_ocr: bool = True,
) -> str:
    return extract_book_sample(
        path, max_pages=max_pages, max_chars=max_chars, use_ocr=use_ocr
    ).text


def extract_book_sample(
    path: Path,
    max_pages: int = 3,
    max_chars: int = 12000,
    *,
    use_ocr: bool = True,
) -> BookSample:
    suffix = path.suffix.lower()
    fmt = suffix.lstrip(".") or "unknown"

    if suffix == ".epub":
        sample = _fitz_sample(
            path, max_pages=max_pages, max_chars=max_chars, use_ocr=use_ocr
        )
        if not sample.substantive:
            sample = _epub_ebooklib_sample(
                path, max_chars=max_chars, max_sections=max_pages
            )
        return _with_format(sample, fmt)

    if suffix in _FITZ_EXTENSIONS:
        sample = _fitz_sample(
            path, max_pages=max_pages, max_chars=max_chars, use_ocr=use_ocr
        )
        return _with_format(sample, fmt)

    if suffix in _PLAIN_TEXT_EXTENSIONS:
        sample = _plain_text_sample(path, max_pages=max_pages, max_chars=max_chars)
        return _with_format(sample, fmt)

    if suffix in _DOCX_EXTENSIONS:
        sample = _docx_sample(path, max_chars=max_chars)
        return _with_format(sample, fmt)

    if suffix == ".rtf":
        sample = _rtf_sample(path, max_chars=max_chars)
        return _with_format(sample, fmt)

    if suffix in _HTML_EXTENSIONS:
        sample = _html_sample(path, max_pages=max_pages, max_chars=max_chars)
        return _with_format(sample, fmt)

    if suffix == ".doc":
        return BookSample(
            "",
            "empty",
            0,
            False,
            file_format=fmt,
            ocr_note="Legacy .doc not supported — convert to PDF or DOCX",
        )

    return BookSample("", "empty", 0, False, file_format=fmt)


def _with_format(sample: BookSample, fmt: str) -> BookSample:
    if sample.file_format:
        return sample
    return BookSample(
        sample.text,
        sample.method,
        sample.pages_attempted,
        sample.substantive,
        file_format=fmt,
        ocr_attempted=sample.ocr_attempted,
        ocr_note=sample.ocr_note,
    )


def _is_substantive(text: str, path: Path) -> bool:
    t = text.strip()
    if len(t) < _MIN_SUBSTANTIVE_CHARS:
        return False
    stem = _humanize_stem(path.stem).lower()
    norm = re.sub(r"\s+", " ", t.lower())
    if norm == stem:
        return False
    if len(norm) < len(stem) + 40 and stem in norm and len(norm) < _MIN_SUBSTANTIVE_CHARS + 20:
        return False
    return True


def tesseract_on_path() -> bool:
    return shutil.which("tesseract") is not None


def extract_metadata_title(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix == ".epub":
        return _epub_metadata_title(path)
    if suffix in _FITZ_EXTENSIONS:
        return _pdf_metadata_title(path)
    if suffix in _PLAIN_TEXT_EXTENSIONS or suffix in _HTML_EXTENSIONS:
        return _plain_text_title(path)
    if suffix in _DOCX_EXTENSIONS:
        return _docx_title(path)
    return ""


def classification_label(path: Path, display_stem: str) -> str:
    """Filename stem; metadata only when it plausibly matches the filename."""
    meta = extract_metadata_title(path).strip()
    if not meta:
        return _humanize_stem(display_stem)
    if meta.lower() in display_stem.lower() or display_stem.lower() in meta.lower():
        return f"{_humanize_stem(display_stem)} | {meta}"
    if _metadata_matches_stem(display_stem, meta):
        return f"{_humanize_stem(display_stem)} | {meta}"
    return _humanize_stem(display_stem)


def build_classification_title(
    path: Path,
    display_stem: str,
    *,
    source_archive: str | None = None,
) -> str:
    """Title for the LLM; archive name anchors junk inner filenames from zips."""
    base = classification_label(path, display_stem)
    if not source_archive:
        return base
    archive_h = _humanize_stem(source_archive)
    if _filename_title_is_noise(display_stem):
        return archive_h
    return f"{archive_h} — {base}"


def _filename_title_is_noise(stem: str) -> bool:
    h = _humanize_stem(stem)
    if " " in h and len(h) >= 12:
        return False
    if re.search(r"other_names|^\d+-", h, re.I):
        return True
    low = stem.lower()
    vowels = sum(1 for c in low if c in "aeiou")
    if len(low) >= 6 and vowels <= max(1, len(low) // 5):
        return True
    if re.fullmatch(r"[a-zA-Z0-9_\-() ]+", stem) and len(stem) < 32 and " " not in h.strip():
        return True
    return False


def _humanize_stem(stem: str) -> str:
    return re.sub(r"[_]+", " ", stem).strip()


def _metadata_matches_stem(stem: str, meta: str) -> bool:
    from .title_match import normalize_title, title_tokens

    stem_norm = normalize_title(_humanize_stem(stem))
    meta_norm = normalize_title(meta)
    stem_t = title_tokens(stem_norm)
    meta_t = title_tokens(meta_norm)
    if stem_t & meta_t:
        return True
    for token in stem_t:
        if len(token) >= 5 and token in meta_norm:
            return True
    return False


def _pdf_metadata_title(path: Path) -> str:
    try:
        import fitz
    except ImportError:
        return ""
    try:
        with fitz.open(path) as doc:
            meta = doc.metadata or {}
            return str(meta.get("title") or "").strip()
    except Exception:
        return ""


def _epub_metadata_title(path: Path) -> str:
    try:
        from ebooklib import epub
    except ImportError:
        return ""
    try:
        book = epub.read_epub(str(path))
    except Exception:
        return ""
    for key in ("title", "TITL"):
        value = book.get_metadata("DC", key)
        if value and value[0][0]:
            return str(value[0][0]).strip()
    return ""


def _plain_text_title(path: Path) -> str:
    try:
        head = path.read_bytes()[:8000]
    except OSError:
        return ""
    text = _decode_bytes(head)
    for line in text.splitlines():
        line = line.strip()
        if len(line) >= 3:
            return line[:200]
    return ""


def _docx_title(path: Path) -> str:
    try:
        with zipfile.ZipFile(path) as zf:
            if "docProps/core.xml" not in zf.namelist():
                return ""
            root = ET.fromstring(zf.read("docProps/core.xml"))
            for tag in ("title", "{http://purl.org/dc/elements/1.1/}title"):
                el = root.find(f".//{tag}")
                if el is not None and el.text:
                    return el.text.strip()
    except Exception:
        return ""
    return ""


def _page_text(page) -> str:
    for mode in ("text", "blocks", "words"):
        try:
            if mode == "text":
                t = page.get_text("text").strip()
            elif mode == "blocks":
                blocks = page.get_text("blocks")
                t = "\n".join(
                    b[4].strip() for b in blocks if len(b) > 4 and str(b[4]).strip()
                ).strip()
            else:
                words = page.get_text("words")
                t = " ".join(w[4] for w in words if len(w) > 4).strip()
            if len(t) >= 40:
                return t
        except Exception:
            continue
    try:
        return page.get_text("text").strip()
    except Exception:
        return ""


def _page_text_ocr(page, *, dpi: int = 200) -> str:
    try:
        tp = page.get_textpage_ocr(dpi=dpi, full=False)
        return page.get_text(textpage=tp).strip()
    except Exception:
        return ""


def _fitz_sample(
    path: Path,
    max_pages: int,
    max_chars: int,
    *,
    use_ocr: bool,
) -> BookSample:
    try:
        import fitz
    except ImportError:
        return BookSample("", "empty", 0, False)

    chunks: list[str] = []
    ocr_attempted = False
    ocr_note = ""
    pages_attempted = 0
    method = "text-layer"

    try:
        with fitz.open(path) as doc:
            meta = doc.metadata or {}
            meta_title = str(meta.get("title") or "").strip()
            if meta_title:
                chunks.append(meta_title)

            if doc.page_count == 0:
                return BookSample(
                    "",
                    "empty",
                    0,
                    False,
                    ocr_note="Document has no pages",
                )

            read_pages = min(doc.page_count, max_pages)
            scan_limit = min(doc.page_count, max(read_pages, max_pages * 4))
            for i in range(scan_limit):
                pages_attempted = max(pages_attempted, i + 1)
                page_text = _page_text(doc.load_page(i))
                if page_text:
                    chunks.append(page_text)
                joined = "\n".join(chunks).strip()
                if _is_substantive(joined, path):
                    return BookSample(
                        joined[:max_chars],
                        method,
                        pages_attempted,
                        True,
                    )

            joined = "\n".join(chunks).strip()
            if _is_substantive(joined, path):
                return BookSample(
                    joined[:max_chars],
                    method,
                    pages_attempted,
                    True,
                )

            if use_ocr and read_pages > 0:
                ocr_attempted = True
                if not tesseract_on_path():
                    ocr_note = "Tesseract not on PATH — install for scanned PDFs/images"
                else:
                    ocr_chunks = list(chunks)
                    for i in range(read_pages):
                        ocr_text = _page_text_ocr(doc.load_page(i))
                        if ocr_text:
                            ocr_chunks.append(ocr_text)
                    joined = "\n".join(ocr_chunks).strip()
                    if _is_substantive(joined, path):
                        return BookSample(
                            joined[:max_chars],
                            "ocr",
                            read_pages,
                            True,
                            ocr_attempted=True,
                        )
                    ocr_note = "OCR ran but still too little text on first pages"

            return BookSample(
                joined[:max_chars] if joined else "",
                "empty",
                pages_attempted,
                False,
                ocr_attempted=ocr_attempted,
                ocr_note=ocr_note,
            )
    except Exception as exc:
        return BookSample(
            "",
            "empty",
            pages_attempted,
            False,
            ocr_attempted=ocr_attempted,
            ocr_note=str(exc)[:200],
        )


def _decode_bytes(raw: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16", "utf-16-le", "cp1252", "latin-1"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("utf-8", errors="replace")


def _decode_item_content(item) -> str:
    raw = item.get_content()
    if not raw:
        return ""
    if isinstance(raw, str):
        return raw
    return _decode_bytes(raw)


def _epub_ebooklib_sample(path: Path, max_chars: int, max_sections: int) -> BookSample:
    try:
        import ebooklib
        from bs4 import BeautifulSoup
        from ebooklib import epub
    except ImportError:
        return BookSample("", "empty", 0, False, ocr_note="ebooklib missing")

    try:
        book = epub.read_epub(str(path), options={"ignore_ncx": True})
    except Exception as exc:
        return BookSample("", "empty", 0, False, ocr_note=f"EPUB read failed: {exc}")

    parts: list[str] = []
    for key in ("title", "TITL"):
        value = book.get_metadata("DC", key)
        if value and value[0][0]:
            parts.append(str(value[0][0]).strip())
    for key in ("description", "DESC"):
        value = book.get_metadata("DC", key)
        if value and value[0][0]:
            parts.append(str(value[0][0]).strip())
    for value in book.get_metadata("DC", "subject") or []:
        if value and value[0]:
            parts.append(str(value[0]).strip())

    sections = 0

    def add_html(html: str) -> None:
        nonlocal sections
        if not html.strip():
            return
        try:
            soup = BeautifulSoup(html, "html.parser")
            text = soup.get_text(separator="\n", strip=True)
        except Exception:
            text = re.sub(r"<[^>]+>", " ", html)
            text = re.sub(r"\s+", " ", text).strip()
        if text:
            parts.append(text)
            sections += 1

    def add_item(item) -> None:
        if item is None:
            return
        name = (item.get_name() or "").lower()
        if name.endswith((".css", ".jpg", ".jpeg", ".png", ".gif", ".svg", ".ncx")):
            return
        if "nav" in name and "chapter" not in name:
            return
        add_html(_decode_item_content(item))

    seen_ids: set[str] = set()
    try:
        for item_id, _linear in book.spine:
            item = book.get_item_with_id(item_id)
            if item is None:
                continue
            seen_ids.add(item_id)
            if item.get_type() == ebooklib.ITEM_DOCUMENT:
                add_item(item)
            if sections >= max_sections or sum(len(p) for p in parts) > max_chars:
                break
    except Exception:
        pass

    if sum(len(p) for p in parts) < _MIN_SUBSTANTIVE_CHARS:
        for item in book.get_items():
            iid = item.get_id() or item.get_name() or ""
            if iid in seen_ids:
                continue
            if item.get_type() != ebooklib.ITEM_DOCUMENT:
                continue
            add_item(item)
            if sections >= max_sections * 3 or sum(len(p) for p in parts) > max_chars:
                break

    text = "\n\n".join(parts)[:max_chars]
    substantive = _is_substantive(text, path)
    return BookSample(
        text,
        "epub" if substantive else "empty",
        sections,
        substantive,
        ocr_note="" if substantive else "EPUB HTML sections had little extractable text",
    )


def _html_sample(path: Path, max_pages: int, max_chars: int) -> BookSample:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return BookSample("", "empty", 0, False, ocr_note=str(exc))
    text = _decode_bytes(raw)
    try:
        from bs4 import BeautifulSoup

        text = BeautifulSoup(text, "html.parser").get_text(separator="\n", strip=True)
    except Exception:
        text = re.sub(r"<[^>]+>", " ", text)
        text = re.sub(r"\s+", " ", text).strip()
    per_page = 3500
    limit = min(max_chars, max(1, max_pages) * per_page)
    chunk = text[:limit].strip()
    substantive = _is_substantive(chunk, path)
    return BookSample(
        chunk,
        "html" if substantive else "empty",
        max_pages,
        substantive,
    )


def _plain_text_sample(path: Path, max_pages: int, max_chars: int) -> BookSample:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return BookSample("", "empty", 0, False, ocr_note=str(exc))

    text = _decode_bytes(raw)
    per_page = 3500
    limit = min(max_chars, max(1, max_pages) * per_page)
    chunk = text[:limit].strip()
    substantive = _is_substantive(chunk, path)
    return BookSample(
        chunk,
        "plain-text" if substantive else "empty",
        max_pages,
        substantive,
    )


def _docx_sample(path: Path, max_chars: int) -> BookSample:
    try:
        with zipfile.ZipFile(path) as zf:
            if "word/document.xml" not in zf.namelist():
                return BookSample("", "empty", 0, False, ocr_note="Invalid DOCX")
            root = ET.fromstring(zf.read("word/document.xml"))
    except Exception as exc:
        return BookSample("", "empty", 0, False, ocr_note=f"DOCX read failed: {exc}")

    parts: list[str] = []
    for node in root.iter(f"{_WDOC_NS}t"):
        if node.text:
            parts.append(node.text)
        if node.tail:
            parts.append(node.tail)
    text = re.sub(r"\s+", " ", " ".join(parts)).strip()[:max_chars]
    substantive = _is_substantive(text, path)
    return BookSample(
        text,
        "docx" if substantive else "empty",
        1,
        substantive,
    )


def _rtf_sample(path: Path, max_chars: int) -> BookSample:
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return BookSample("", "empty", 0, False, ocr_note=str(exc))
    text = _decode_bytes(raw)
    text = re.sub(r"\\[a-z]+\d* ?", " ", text)
    text = re.sub(r"[{}\\]", " ", text)
    text = re.sub(r"\s+", " ", text).strip()[:max_chars]
    substantive = _is_substantive(text, path)
    return BookSample(
        text,
        "plain-text" if substantive else "empty",
        1,
        substantive,
    )


def pick_sample_file_for_bundle(bundle_dir: Path) -> Path | None:
    files = sorted(
        (p for p in bundle_dir.iterdir() if is_book_file(p)),
        key=lambda p: p.name.lower(),
    )
    if not files:
        return None
    for f in files:
        if looks_like_first_chapter(f.name):
            return f
    return files[0]


def looks_like_first_chapter(name: str) -> bool:
    lower = name.lower()
    return (
        "chapter 1" in lower
        or "chapter_1" in lower
        or "ch01" in lower
        or "ch_01" in lower
        or lower.startswith("01 ")
        or lower.startswith("1.")
        or Path(name).stem in {"1", "01", "001"}
    )


def is_weak_filename_title(title: str) -> bool:
    """Mostly numeric / no real words — rely on excerpt instead of title-only."""
    norm = re.sub(r"[_\.\-]+", " ", title).strip().lower()
    words = re.findall(r"[a-z]{3,}", norm)
    if len(words) >= 2:
        return False
    digits = sum(c.isdigit() for c in norm)
    if digits > 0 and digits >= len(norm.replace(" ", "")) * 0.4 and len(words) <= 1:
        return True
    return len(words) == 0
