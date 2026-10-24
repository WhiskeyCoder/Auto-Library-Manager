from __future__ import annotations

import shutil
import subprocess
import zipfile
from collections.abc import Callable
from pathlib import Path

from .categories import ARCHIVE_EXTENSIONS, BOOK_EXTENSIONS, is_archive_file, is_book_file
from .mover import _prune_empty_ancestors, move_to_manual, unique_destination
from .scanner import SortItem

_EXTRACT_DIR_NAME = ".library-extract"
_BOOK_SUFFIXES = BOOK_EXTENSIONS - ARCHIVE_EXTENSIONS


def peek_archive_book_count(archive: Path) -> int:
    """Dry-run: count likely book paths inside an archive without extracting."""
    suffix = archive.suffix.lower()
    names: list[str] = []
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                names = zf.namelist()
        elif suffix == ".rar":
            try:
                import rarfile

                with rarfile.RarFile(archive) as rf:
                    names = [i.filename for i in rf.infolist()]
            except Exception:
                return 0
        else:
            return 0
    except Exception:
        return 0
    return sum(1 for n in names if Path(n).suffix.lower() in _BOOK_SUFFIXES)


def extract_archive(archive: Path, dest: Path) -> tuple[bool, str]:
    dest.mkdir(parents=True, exist_ok=True)
    suffix = archive.suffix.lower()
    try:
        if suffix == ".zip":
            with zipfile.ZipFile(archive) as zf:
                zf.extractall(dest)
            return True, ""
        if suffix == ".rar":
            return _extract_rar(archive, dest)
        if suffix == ".7z":
            return _extract_7z(archive, dest)
        return False, f"Unsupported archive type: {suffix}"
    except Exception as exc:
        return False, str(exc)


def _extract_rar(archive: Path, dest: Path) -> tuple[bool, str]:
    unrar = shutil.which("unrar") or shutil.which("UnRAR")
    if unrar:
        try:
            subprocess.run(
                [unrar, "x", "-o+", str(archive), str(dest)],
                check=True,
                capture_output=True,
                text=True,
            )
            return True, ""
        except subprocess.CalledProcessError as exc:
            return False, (exc.stderr or exc.stdout or str(exc))[:300]

    try:
        import rarfile

        with rarfile.RarFile(archive) as rf:
            rf.extractall(dest)
        return True, ""
    except ImportError:
        return False, "RAR needs unrar on PATH or pip install rarfile"
    except Exception as exc:
        return False, str(exc)[:300]


def _extract_7z(archive: Path, dest: Path) -> tuple[bool, str]:
    seven = shutil.which("7z") or shutil.which("7za")
    if not seven:
        return False, "7z not on PATH (install 7-Zip for .7z archives)"
    try:
        subprocess.run(
            [seven, "x", str(archive), f"-o{dest}", "-y"],
            check=True,
            capture_output=True,
            text=True,
        )
        return True, ""
    except subprocess.CalledProcessError as exc:
        return False, (exc.stderr or exc.stdout or str(exc))[:300]


def collect_books_under(root: Path) -> list[Path]:
    """All book files under root, expanding nested archives in place."""
    found: list[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if is_archive_file(path):
            nested_dir = path.parent / f"_nested_{path.stem}"
            ok, _err = extract_archive(path, nested_dir)
            if ok:
                try:
                    path.unlink()
                except OSError:
                    pass
                found.extend(collect_books_under(nested_dir))
            continue
        if is_book_file(path):
            found.append(path.resolve())
    return found


def workspace_for_archive(archive: Path, library_root: Path) -> Path:
    base = library_root.resolve() / _EXTRACT_DIR_NAME
    safe = archive.stem.replace("..", "_")[:120]
    return base / safe


def process_archive_items(
    item: SortItem,
    scan_root: Path,
    output_root: Path,
    *,
    dry_run: bool,
    flatten_to_root: bool,
    on_log: Callable[[str], None],
    process_book: Callable[[SortItem], None],
) -> None:
    archive = item.path.resolve()
    scan_root = scan_root.resolve()
    output_root = output_root.resolve()

    if dry_run:
        n = peek_archive_book_count(archive)
        on_log(f"  archive → dry run: would extract ~{n} book file(s) from {archive.name}")
        return

    work = workspace_for_archive(archive, scan_root)
    if work.exists():
        shutil.rmtree(work, ignore_errors=True)

    ok, err = extract_archive(archive, work)
    if not ok:
        on_log(f"  archive → extract failed: {err}")
        move_to_manual(archive, output_root, dry_run=False, prune_root=scan_root)
        return

    books = collect_books_under(work)
    if not books:
        on_log("  archive → no book files found inside")
        shutil.rmtree(work, ignore_errors=True)
        move_to_manual(archive, output_root, dry_run=False, prune_root=scan_root)
        return

    on_log(f"  archive → extracted {len(books)} book(s) from {archive.name}")
    for book_path in books:
        dest = unique_destination(work, book_path.name)
        if book_path != dest:
            shutil.move(str(book_path), str(dest))
            book_path = dest
        sub = SortItem(
            path=book_path,
            display_title=book_path.stem,
            relative_path=book_path.relative_to(scan_root).as_posix(),
            is_bundle=False,
            source_archive=archive.stem,
            skip_hoist=True,
        )
        on_log(f"  archive / book → {sub.relative_path}")
        process_book(sub)

    try:
        archive.unlink()
    except OSError as exc:
        on_log(f"  archive → could not delete {archive.name}: {exc}")

    shutil.rmtree(work, ignore_errors=True)
    _prune_empty_ancestors(archive.parent, scan_root)
