from __future__ import annotations

import json
import os
import subprocess
import threading
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from tkinter import filedialog, messagebox, ttk

from .categorize_runner import CategorizeConfig, run_categorize
from .job_callbacks import JobCallbacks
from .paths import effective_output_root

SETTINGS_FILE = Path.home() / ".library-manager-gui.json"


class LibrarySortApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("Auto Library Manager — Smart Categorize")
        self.minsize(820, 620)
        self.geometry("960x720")

        self._log_queue: Queue[str] = Queue()
        self._cancel_event = threading.Event()
        self._worker: threading.Thread | None = None
        self._settings = self._load_settings()

        self._build_ui()
        self._apply_settings()
        self.after(100, self._poll_log_queue)

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}
        root_frame = ttk.Frame(self, padding=10)
        root_frame.pack(fill=tk.BOTH, expand=True)

        paths = ttk.LabelFrame(root_frame, text="Folders", padding=8)
        paths.pack(fill=tk.X, **pad)

        self.library_var = tk.StringVar()
        ttk.Label(paths, text="Input folder").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.library_var, width=72).grid(
            row=0, column=1, sticky=tk.EW, padx=4
        )
        ttk.Button(paths, text="Browse…", command=self._browse_library).grid(row=0, column=2)

        self.output_var = tk.StringVar()
        ttk.Label(paths, text="Output folder").grid(row=1, column=0, sticky=tk.W)
        ttk.Entry(paths, textvariable=self.output_var, width=72).grid(
            row=1, column=1, sticky=tk.EW, padx=4
        )
        ttk.Button(paths, text="Browse…", command=self._browse_output).grid(row=1, column=2)
        ttk.Label(
            paths,
            text=(
                "Books are read from the input folder. Sorted books go under "
                "# categories in the output folder (blank = same as input)."
            ),
            foreground="gray",
            wraplength=720,
            justify=tk.LEFT,
        ).grid(row=2, column=0, columnspan=3, sticky=tk.W)

        self.flatten_to_root_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            paths,
            text="Pull books from all nested subfolders up to input root before sorting",
            variable=self.flatten_to_root_var,
        ).grid(row=3, column=0, columnspan=3, sticky=tk.W, pady=(6, 0))
        paths.columnconfigure(1, weight=1)

        llm = ttk.LabelFrame(root_frame, text="Local LLM (offline)", padding=8)
        llm.pack(fill=tk.X, **pad)

        self.llm_url_var = tk.StringVar(value="http://127.0.0.1:1234")
        ttk.Label(llm, text="URL").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(llm, textvariable=self.llm_url_var, width=40).grid(
            row=0, column=1, sticky=tk.W, padx=4
        )
        self.model_var = tk.StringVar()
        ttk.Label(llm, text="Model (optional)").grid(row=0, column=2, sticky=tk.W, padx=(12, 0))
        ttk.Entry(llm, textvariable=self.model_var, width=24).grid(row=0, column=3, sticky=tk.W)

        self.concurrency_var = tk.IntVar(value=4)
        ttk.Label(llm, text="LLM concurrency").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Spinbox(llm, from_=1, to=16, textvariable=self.concurrency_var, width=6).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )
        ttk.Label(
            llm,
            text="No cloud APIs — only HTTP to the URL above (your machine).",
            foreground="gray",
        ).grid(row=2, column=0, columnspan=4, sticky=tk.W, pady=(6, 0))

        opts = ttk.LabelFrame(root_frame, text="Smart categorize", padding=8)
        opts.pack(fill=tk.X, **pad)

        self.cat_from_folder_var = tk.StringVar()
        ttk.Label(opts, text="Only from folder").grid(row=0, column=0, sticky=tk.W)
        ttk.Entry(opts, textvariable=self.cat_from_folder_var, width=50).grid(
            row=0, column=1, sticky=tk.EW, padx=4
        )

        self.cat_pages_var = tk.IntVar(value=5)
        ttk.Label(opts, text="First pages to read").grid(row=1, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Spinbox(opts, from_=1, to=30, textvariable=self.cat_pages_var, width=6).grid(
            row=1, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )

        self.cat_limit_var = tk.IntVar(value=0)
        ttk.Label(opts, text="Limit (0=all)").grid(row=2, column=0, sticky=tk.W, pady=(6, 0))
        ttk.Spinbox(opts, from_=0, to=100000, textvariable=self.cat_limit_var, width=8).grid(
            row=2, column=1, sticky=tk.W, padx=4, pady=(6, 0)
        )

        self.cat_dry_run_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(opts, text="Dry run", variable=self.cat_dry_run_var).grid(
            row=3, column=0, columnspan=2, sticky=tk.W, pady=(6, 0)
        )

        self.cat_include_manual_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(
            opts, text="Include # MANUAL SORT NEEDED", variable=self.cat_include_manual_var
        ).grid(row=4, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        self.cat_save_registry_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="Save new AI paths to ai-categories.json in the output folder",
            variable=self.cat_save_registry_var,
        ).grid(row=5, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        self.cat_use_ocr_var = tk.BooleanVar(value=True)
        ttk.Checkbutton(
            opts,
            text="OCR scanned PDFs (local Tesseract on PATH)",
            variable=self.cat_use_ocr_var,
        ).grid(row=6, column=0, columnspan=2, sticky=tk.W, pady=(6, 0))

        ttk.Label(
            opts,
            text=(
                "AI chooses # category paths. Pipeline: extract pages → one local LLM call "
                "(title + content) → manual queue if unsure."
            ),
            justify=tk.LEFT,
            wraplength=720,
        ).grid(row=7, column=0, columnspan=2, sticky=tk.W, pady=(8, 0))
        opts.columnconfigure(1, weight=1)

        actions = ttk.Frame(root_frame)
        actions.pack(fill=tk.X, **pad)

        self.start_btn = ttk.Button(actions, text="Start categorize", command=self._start_sort)
        self.start_btn.pack(side=tk.LEFT)
        ttk.Button(actions, text="Dry run", command=self._start_dry_run).pack(side=tk.LEFT, padx=6)
        self.stop_btn = ttk.Button(actions, text="Stop", command=self._stop_job, state=tk.DISABLED)
        self.stop_btn.pack(side=tk.LEFT, padx=6)
        ttk.Button(actions, text="Open input folder", command=self._open_library).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(actions, text="Open output folder", command=self._open_output).pack(
            side=tk.LEFT, padx=6
        )
        ttk.Button(actions, text="Clear log", command=self._clear_log).pack(side=tk.LEFT)

        self.status_var = tk.StringVar(value="Ready")
        ttk.Label(actions, textvariable=self.status_var).pack(side=tk.RIGHT)

        self.progress = ttk.Progressbar(root_frame, mode="determinate")
        self.progress.pack(fill=tk.X, **pad)

        log_frame = ttk.LabelFrame(root_frame, text="Activity", padding=4)
        log_frame.pack(fill=tk.BOTH, expand=True, **pad)

        self.log_text = tk.Text(log_frame, height=16, wrap=tk.WORD, state=tk.DISABLED)
        scroll = ttk.Scrollbar(log_frame, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=scroll.set)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _apply_settings(self) -> None:
        s = self._settings
        if s.get("library_root"):
            self.library_var.set(s["library_root"])
        if s.get("output_root"):
            self.output_var.set(s["output_root"])
        if s.get("llm_url"):
            self.llm_url_var.set(s["llm_url"])
        if s.get("model"):
            self.model_var.set(s["model"])
        self.concurrency_var.set(int(s.get("concurrency", 4)))
        self.cat_pages_var.set(int(s.get("cat_pages", s.get("pages", 5))))
        self.cat_limit_var.set(int(s.get("cat_limit", s.get("limit", 0))))
        self.cat_from_folder_var.set(s.get("cat_from_folder", s.get("from_folder", "")))
        self.cat_save_registry_var.set(bool(s.get("cat_save_registry", True)))
        self.cat_use_ocr_var.set(bool(s.get("cat_use_ocr", True)))
        self.flatten_to_root_var.set(bool(s.get("flatten_to_root", True)))
        self.cat_dry_run_var.set(bool(s.get("cat_dry_run", s.get("dry_run", False))))

    def _save_settings(self) -> None:
        data = {
            "library_root": self.library_var.get().strip(),
            "output_root": self.output_var.get().strip(),
            "llm_url": self.llm_url_var.get().strip(),
            "model": self.model_var.get().strip(),
            "concurrency": int(self.concurrency_var.get()),
            "cat_pages": int(self.cat_pages_var.get()),
            "cat_limit": int(self.cat_limit_var.get()),
            "cat_from_folder": self.cat_from_folder_var.get().strip(),
            "cat_save_registry": bool(self.cat_save_registry_var.get()),
            "cat_use_ocr": bool(self.cat_use_ocr_var.get()),
            "flatten_to_root": bool(self.flatten_to_root_var.get()),
            "cat_dry_run": bool(self.cat_dry_run_var.get()),
        }
        try:
            SETTINGS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
        except OSError:
            pass

    @staticmethod
    def _load_settings() -> dict:
        if not SETTINGS_FILE.is_file():
            return {}
        try:
            return json.loads(SETTINGS_FILE.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}

    def _browse_library(self) -> None:
        path = filedialog.askdirectory(title="Select input folder to scan")
        if path:
            self.library_var.set(path)

    def _browse_output(self) -> None:
        path = filedialog.askdirectory(title="Select output folder for sorted books")
        if path:
            self.output_var.set(path)

    def _open_library(self) -> None:
        path = self.library_var.get().strip()
        if not path or not Path(path).is_dir():
            messagebox.showwarning("Library", "Set a valid input folder first.")
            return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.run(["xdg-open", path], check=False)

    def _open_output(self) -> None:
        library = self.library_var.get().strip()
        out = self.output_var.get().strip()
        if not library or not Path(library).is_dir():
            messagebox.showwarning("Library", "Set a valid input folder first.")
            return
        path = str(effective_output_root(Path(library), Path(out) if out else None))
        if not Path(path).is_dir():
            try:
                Path(path).mkdir(parents=True, exist_ok=True)
            except OSError:
                messagebox.showwarning(
                    "Output",
                    f"Output folder does not exist and could not be created:\n{path}",
                )
                return
        if os.name == "nt":
            os.startfile(path)  # noqa: S606
        else:
            subprocess.run(["xdg-open", path], check=False)

    def _append_log(self, line: str) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, line + "\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _clear_log(self) -> None:
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _poll_log_queue(self) -> None:
        try:
            while True:
                line = self._log_queue.get_nowait()
                self._append_log(line)
        except Empty:
            pass
        self.after(100, self._poll_log_queue)

    def _set_running(self, running: bool) -> None:
        self.start_btn.configure(state=tk.DISABLED if running else tk.NORMAL)
        self.stop_btn.configure(state=tk.NORMAL if running else tk.DISABLED)

    def _start_dry_run(self) -> None:
        self.cat_dry_run_var.set(True)
        self._start_sort()

    def _start_sort(self) -> None:
        if self._worker and self._worker.is_alive():
            return

        library = self.library_var.get().strip()
        if not library or not Path(library).is_dir():
            messagebox.showerror("Library", "Choose a valid input folder.")
            return

        self._save_settings()
        self._cancel_event.clear()
        self._set_running(True)
        self.progress["value"] = 0
        self.progress["maximum"] = 100
        self.status_var.set("Running…")

        model = self.model_var.get().strip()
        out = self.output_var.get().strip()
        output_path = Path(out) if out else None

        def log(msg: str) -> None:
            self._log_queue.put(msg)

        def progress(done: int, total: int) -> None:
            if total > 0:
                self.after(0, lambda: self._update_progress(done, total))

        callbacks = JobCallbacks(
            on_log=log,
            on_progress=progress,
            should_cancel=self._cancel_event.is_set,
        )

        from_folder = self.cat_from_folder_var.get().strip()
        config = CategorizeConfig(
            library_root=Path(library),
            output_root=output_path,
            llm_url=self.llm_url_var.get().strip() or "http://127.0.0.1:1234",
            model=model or None,
            dry_run=bool(self.cat_dry_run_var.get()),
            pages=int(self.cat_pages_var.get()),
            limit=int(self.cat_limit_var.get()),
            from_folders=[from_folder] if from_folder else [],
            include_manual=bool(self.cat_include_manual_var.get()),
            concurrency=int(self.concurrency_var.get()),
            save_registry=bool(self.cat_save_registry_var.get()),
            use_ocr=bool(self.cat_use_ocr_var.get()),
            flatten_to_root=bool(self.flatten_to_root_var.get()),
        )

        def work() -> None:
            try:
                run_categorize(config, callbacks)
            finally:
                self.after(0, self._on_sort_finished)

        self._worker = threading.Thread(target=work, daemon=True)
        self._worker.start()
        self._append_log("——— Smart categorize started ———")

    def _update_progress(self, done: int, total: int) -> None:
        self.progress["maximum"] = total
        self.progress["value"] = done
        self.status_var.set(f"Processing {done} / {total}")

    def _stop_job(self) -> None:
        self._cancel_event.set()
        self.status_var.set("Stopping…")
        self._log_queue.put("Cancel requested…")

    def _on_sort_finished(self) -> None:
        self._set_running(False)
        self.status_var.set("Ready")
        self._append_log("——— Job ended ———")


def main() -> int:
    app = LibrarySortApp()
    app.mainloop()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
