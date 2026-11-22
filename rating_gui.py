#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rating_gui.py — RatingTool 的图形界面。

功能：
- 选择源目录 / 输出目录（输出留空 = 源目录）；
- 预览：按评分等级分组，显示每个等级有多少张、以及文件名（只读 XMP，不加载缩略图）；
- 执行：复制或移动，分类规则与命令行版完全一致。

界面基于 tkinter（Python 标准库），逻辑全部复用 rating_sort.py。
"""

from __future__ import annotations

import os
import queue
import sys
import threading
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import rating_sort as rs  # noqa: E402

PREVIEW_LIMIT = 1000  # 每个等级最多列出的文件名数


class RatingApp(tk.Tk):
    def __init__(self) -> None:
        super().__init__()
        self.title("RatingClassifyTool4Canon By Nori&Xi")
        self.geometry("900x680")
        self.minsize(720, 520)

        self.msg_q: queue.Queue = queue.Queue()
        self.busy = False
        self.last_buckets: dict[str, list[Path]] = {}

        self.src_var = tk.StringVar()
        self.out_var = tk.StringVar()
        self.recursive_var = tk.BooleanVar(value=False)
        self.copy_var = tk.BooleanVar(value=True)
        self.status_var = tk.StringVar(value="就绪：先选源目录，然后点「预览评分」。")

        self._build_ui()
        self.after(100, self._poll)

        self.exiftool = rs.default_exiftool()
        if not rs.exiftool_available(self.exiftool):
            self.status_var.set("⚠ 没找到 ExifTool：检查 exiftool/ 目录或 PATH。")
        elif rs.bundled_exiftool():
            self.status_var.set("就绪（使用自带的 ExifTool）：先选源目录，然后点「预览评分」。")

    # ---------- UI ----------

    def _build_ui(self) -> None:
        pad = {"padx": 8, "pady": 4}

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="源目录：").pack(side="left")
        ttk.Entry(row, textvariable=self.src_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="浏览…", command=self.choose_source).pack(side="left")

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Label(row, text="输出目录：").pack(side="left")
        ttk.Entry(row, textvariable=self.out_var).pack(side="left", fill="x", expand=True, padx=4)
        ttk.Button(row, text="浏览…", command=self.choose_output).pack(side="left")
        ttk.Label(row, text="（留空 = 源目录）", foreground="#888").pack(side="left")

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        ttk.Radiobutton(row, text="复制（保留原文件）", variable=self.copy_var, value=True).pack(side="left")
        ttk.Radiobutton(row, text="移动", variable=self.copy_var, value=False).pack(side="left", padx=(10, 0))
        ttk.Checkbutton(row, text="递归扫描子目录", variable=self.recursive_var).pack(side="left", padx=(20, 0))

        row = ttk.Frame(self)
        row.pack(fill="x", **pad)
        self.btn_preview = ttk.Button(row, text="预览评分", command=self.do_preview)
        self.btn_preview.pack(side="left")
        self.btn_apply = ttk.Button(row, text="执行整理", command=self.do_apply)
        self.btn_apply.pack(side="left", padx=6)
        self.btn_clear = ttk.Button(row, text="清空结果", command=self.clear_result)
        self.btn_clear.pack(side="left")

        self.progress = ttk.Progressbar(self, mode="determinate", maximum=100)
        self.progress.pack(fill="x", padx=8)
        ttk.Label(self, textvariable=self.status_var, anchor="w").pack(fill="x", padx=10, pady=2)

        frame = ttk.Frame(self)
        frame.pack(fill="both", expand=True, padx=8, pady=(0, 8))
        self.tree = ttk.Treeview(frame, show="tree", selectmode="none")
        self.tree.column("#0", width=800, anchor="w")
        vsb = ttk.Scrollbar(frame, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        vsb.pack(side="right", fill="y")
        self.tree.bind("<Configure>", lambda e: self.tree.column("#0", width=max(200, e.width - 4)))

    # ---------- 输入 ----------

    def choose_source(self) -> None:
        d = filedialog.askdirectory(title="选择照片目录")
        if d:
            self.src_var.set(d)

    def choose_output(self) -> None:
        d = filedialog.askdirectory(title="选择输出目录（可留空）")
        if d:
            self.out_var.set(d)

    def _read_inputs(self):
        src = self.src_var.get().strip()
        if not src:
            messagebox.showwarning("还没选目录", "先选一个源目录吧。")
            return None, None, None
        source = Path(src).expanduser().resolve()
        if not source.is_dir():
            messagebox.showerror("目录不存在", str(source))
            return None, None, None
        out = self.out_var.get().strip()
        output = Path(out).expanduser().resolve() if out else source
        return source, output, self.recursive_var.get()

    # ---------- 预览 ----------

    def do_preview(self) -> None:
        if self.busy:
            return
        source, output, recursive = self._read_inputs()
        if source is None:
            return
        self._set_busy(True, "扫描中…")
        self.tree.delete(*self.tree.get_children())
        threading.Thread(
            target=self._scan_worker, args=(source, output, recursive), daemon=True
        ).start()

    def _scan_worker(self, source: Path, output: Path, recursive: bool) -> None:
        try:
            excluded = rs.compute_excluded_dirs(source, output, recursive)
            files = rs.find_files(source, recursive, excluded)
            if not files:
                self.msg_q.put(("info", "这个目录里没有 JPG / CR3 文件。"))
                return
            self.msg_q.put(("status", f"读取 {len(files)} 个文件的 XMP 评分…"))
            ratings = rs.read_ratings(
                files, self.exiftool,
                lambda d, t: self.msg_q.put(("progress", d, t)),
            )
            buckets = rs.plan_buckets(files, ratings)
            self.msg_q.put(("scan_done", files, buckets, source))
        except SystemExit as exc:
            self.msg_q.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.msg_q.put(("error", f"{type(exc).__name__}: {exc}"))

    # ---------- 执行 ----------

    def do_apply(self) -> None:
        if self.busy:
            return
        source, output, recursive = self._read_inputs()
        if source is None:
            return
        copy_mode = bool(self.copy_var.get())
        verb = "复制" if copy_mode else "移动"
        where = "源目录（原地整理）" if output == source else str(output)
        if not messagebox.askyesno(
            "确认执行",
            f"{verb}到：{where}\n\n源目录：{source}\n递归子目录：{'是' if recursive else '否'}\n\n继续吗？",
        ):
            return
        self._set_busy(True, f"扫描中…（{verb}模式）")
        self.tree.delete(*self.tree.get_children())
        threading.Thread(
            target=self._apply_worker,
            args=(source, output, recursive, copy_mode),
            daemon=True,
        ).start()

    def _apply_worker(self, source: Path, output: Path, recursive: bool, copy_mode: bool) -> None:
        try:
            excluded = rs.compute_excluded_dirs(source, output, recursive)
            files = rs.find_files(source, recursive, excluded)
            if not files:
                self.msg_q.put(("info", "没有找到 JPG / CR3 文件。"))
                return
            self.msg_q.put(("status", f"读取 {len(files)} 个文件的 XMP 评分…"))
            ratings = rs.read_ratings(
                files, self.exiftool,
                lambda d, t: self.msg_q.put(("progress", d, t)),
            )
            buckets = rs.plan_buckets(files, ratings)
            verb = "复制" if copy_mode else "移动"
            self.msg_q.put(("status", f"开始{verb}…"))
            copied, moved, failed, errors = rs.apply_buckets(
                buckets, output, copy_mode,
                lambda d, t: self.msg_q.put(("progress", d, t)),
            )
            self.msg_q.put(
                ("apply_done", copied, moved, failed, errors, buckets, copy_mode, len(files))
            )
        except SystemExit as exc:
            self.msg_q.put(("error", str(exc)))
        except Exception as exc:  # noqa: BLE001
            self.msg_q.put(("error", f"{type(exc).__name__}: {exc}"))

    # ---------- 消息轮询 ----------

    def _poll(self) -> None:
        try:
            while True:
                self._handle(self.msg_q.get_nowait())
        except queue.Empty:
            pass
        self.after(100, self._poll)

    def _handle(self, msg: tuple) -> None:
        kind = msg[0]
        if kind == "status":
            self.status_var.set(msg[1])
        elif kind == "progress":
            done, total = msg[1], msg[2]
            if total:
                self.progress.configure(mode="determinate", maximum=total, value=done)
        elif kind == "scan_done":
            _, files, buckets, source = msg
            self._set_busy(False)
            self._show_preview(files, buckets, source)
        elif kind == "apply_done":
            _, copied, moved, failed, errors, buckets, copy_mode, total = msg
            self._set_busy(False)
            self._show_apply_result(copied, moved, failed, errors, buckets, copy_mode, total)
        elif kind == "error":
            self._set_busy(False)
            self.status_var.set("出错了")
            messagebox.showerror("出错了", msg[1])
        elif kind == "info":
            self._set_busy(False)
            self.status_var.set(msg[1])
            messagebox.showinfo("提示", msg[1])

    # ---------- 结果展示 ----------

    def _show_preview(self, files: list[Path], buckets: dict[str, list[Path]], source: Path) -> None:
        self.last_buckets = buckets
        self.tree.delete(*self.tree.get_children())
        parts: list[str] = []
        for folder in rs.FOLDER_ORDER:
            items = buckets.get(folder, [])
            if not items:
                continue
            parts.append(f"{folder} {len(items)} 张")
            node = self.tree.insert(
                "", "end", text=f"{folder}（{len(items)} 张）", open=len(items) <= 30
            )
            for p in items[:PREVIEW_LIMIT]:
                try:
                    rel = p.relative_to(source)
                except ValueError:
                    rel = p
                self.tree.insert(node, "end", text=str(rel))
            if len(items) > PREVIEW_LIMIT:
                self.tree.insert(node, "end", text=f"… 还有 {len(items) - PREVIEW_LIMIT} 个未列出")
        self.status_var.set(f"预览完成：共 {len(files)} 个文件 · " + " · ".join(parts))

    def _show_apply_result(
        self,
        copied: int,
        moved: int,
        failed: int,
        errors: list[tuple[Path, Path, str]],
        buckets: dict[str, list[Path]],
        copy_mode: bool,
        total: int,
    ) -> None:
        self.tree.delete(*self.tree.get_children())
        for folder in rs.FOLDER_ORDER:
            items = buckets.get(folder, [])
            if not items:
                continue
            node = self.tree.insert("", "end", text=f"{folder}（{len(items)} 张）", open=False)
            for p in items[:PREVIEW_LIMIT]:
                self.tree.insert(node, "end", text=p.name)

        verb = "复制" if copy_mode else "移动"
        done = copied if copy_mode else moved
        text = f"完成：{verb} {done} 个，失败 {failed} 个（共扫描 {total} 个）。"
        if errors:
            text += "\n\n失败详情（前 10 条）：\n\n" + "\n".join(
                f"{s} -> {d}：{e}" for s, d, e in errors[:10]
            )
        self.status_var.set(text.replace("\n", " "))
        messagebox.showinfo("执行结果", text)

    def clear_result(self) -> None:
        self.tree.delete(*self.tree.get_children())
        self.last_buckets = {}
        self.status_var.set("已清空")

    # ---------- 状态 ----------

    def _set_busy(self, busy: bool, text: str | None = None) -> None:
        self.busy = busy
        state = "disabled" if busy else "normal"
        for btn in (self.btn_preview, self.btn_apply, self.btn_clear):
            btn.configure(state=state)
        if text is not None:
            self.status_var.set(text)
        if not busy:
            self.progress.configure(mode="determinate", value=0)


def main() -> None:
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    RatingApp().mainloop()


if __name__ == "__main__":
    main()
