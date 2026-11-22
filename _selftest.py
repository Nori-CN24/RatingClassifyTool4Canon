#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_selftest.py — RatingTool 自测：后端函数 + GUI 冒烟测试。

运行：python _selftest.py
测试会自己在 %TEMP% 下建临时目录，结束后删除。
"""

from __future__ import annotations

import base64
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

TOOL_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL_DIR))
import rating_sort as rs  # noqa: E402

EXIFTOOL = rs.default_exiftool()

# 1x1 灰度 JPEG，ExifTool 可以往里面写 XMP
JPEG_1PX = base64.b64decode(
    "/9j/4AAQSkZJRgABAQAAAQABAAD/2wBDAAgGBgcGBQgHBwcJCQgKDBQNDAsLDBkSEw8UHRofHh0aHBwgJC4nICIsIxwcKDcpLDAxNDQ0Hyc5PTgyPC4zNDL/wAALCAABAAEBAREA/8QAFAABAAAAAAAAAAAAAAAAAAAACf/EABQQAQAAAAAAAAAAAAAAAAAAAAD/2gAIAQEAAD8AKp//2Q=="
)

results: list[tuple[str, bool]] = []


def check(label: str, cond: object, detail: str = "") -> None:
    ok = bool(cond)
    results.append((label, ok))
    tail = f"  [{detail}]" if (detail and not ok) else ""
    print(("PASS " if ok else "FAIL ") + label + tail)


def write_rating(path: Path, rating: int) -> None:
    subprocess.run(
        [EXIFTOOL, "-q", "-q", "-overwrite_original", f"-XMP:Rating={rating}", str(path)],
        check=True,
    )


def make_tree(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    for r in range(6):
        p = root / f"img{r}.jpg"
        p.write_bytes(JPEG_1PX)
        write_rating(p, r)
    (root / "notes.txt").write_text("not an image", encoding="utf-8")
    sub = root / "sub"
    sub.mkdir()
    p = sub / "deep.jpg"
    p.write_bytes(JPEG_1PX)
    write_rating(p, 5)


def wait_until(pred, timeout: float = 30.0, step: float = 0.05) -> bool:
    end = time.time() + timeout
    while time.time() < end:
        if pred():
            return True
        time.sleep(step)
    return False


def main() -> int:
    work_root = TOOL_DIR / "_selftest_work"
    work_root.mkdir(parents=True, exist_ok=True)
    # 不用 tempfile.mkdtemp：它建出来的目录 DACL 只给当前用户（mode 0o700），
    # 沙箱进程在里面再建子目录会 PermissionError。手动建普通目录。
    tmp = work_root / f"rating_selftest_{os.getpid()}_{int(time.time())}"
    tmp.mkdir(parents=True, exist_ok=True)
    src = tmp / "in"
    make_tree(src)
    print(f"临时目录：{tmp}\n")

    try:
        # ---------- ExifTool 定位 ----------
        bundled = rs.bundled_exiftool()
        check("随包 ExifTool 存在", bundled is not None, str(bundled))
        check("默认优先用随包 ExifTool", rs.default_exiftool() == bundled, rs.default_exiftool())
        check("随包 ExifTool 可用", rs.exiftool_available(rs.default_exiftool()), rs.default_exiftool())

        # ---------- 后端 ----------
        files = rs.find_files(src, False, [])
        check("非递归找到 6 个 jpg", len(files) == 6, str(len(files)))

        files_r = rs.find_files(src, True, [])
        check("递归找到 7 个 jpg", len(files_r) == 7, str(len(files_r)))

        ex = rs.compute_excluded_dirs(src, src, True)
        expected = {src / n for n in rs.FOLDER_ORDER}
        check("原地整理排除全部 6 个分类目录", set(ex) == expected, str(ex))

        ex2 = rs.compute_excluded_dirs(src, tmp / "elsewhere", True)
        check("外部输出目录不额外排除", ex2 == [], str(ex2))

        ex3 = rs.compute_excluded_dirs(src, src / "out", True)
        check("输出在源内时排除整个输出目录", ex3 == [src / "out"], str(ex3))

        ex4 = rs.compute_excluded_dirs(src, tmp / "elsewhere", False)
        check("非递归时不排除任何目录", ex4 == [], str(ex4))

        ratings = rs.read_ratings(files, EXIFTOOL)
        check("读到 6 个评分", len(ratings) == 6, str(len(ratings)))
        got = {r for r in ratings.values()}
        check("评分覆盖 0-5", got == set(range(6)), str(got))

        buckets = rs.plan_buckets(files, ratings)
        check("plan_buckets 顺序", list(buckets) == rs.FOLDER_ORDER, str(list(buckets)))
        check(
            "每个等级 1 张",
            all(len(v) == 1 for v in buckets.values()),
            str({k: len(v) for k, v in buckets.items()}),
        )

        out = tmp / "out"
        copied, moved, failed, errors = rs.apply_buckets(buckets, out, True)
        check("复制 6 个", copied == 6 and failed == 0, f"copied={copied} failed={failed} {errors[:2]}")
        check(
            "输出 6 个分类目录齐全",
            {p.name for p in out.iterdir()} == set(rs.FOLDER_ORDER),
            str(sorted(p.name for p in out.iterdir())),
        )
        check("复制模式源文件仍在", all(p.exists() for p in files))

        moved_out = tmp / "moved"
        _copied2, moved2, failed2, errors2 = rs.apply_buckets(buckets, moved_out, False)
        check("移动 6 个", moved2 == 6 and failed2 == 0, f"moved={moved2} failed={failed2} {errors2[:2]}")
        check(
            "移动后源目录只剩 notes.txt 和 sub",
            sorted(p.name for p in src.iterdir()) == ["notes.txt", "sub"],
            str(sorted(p.name for p in src.iterdir())),
        )

        # ---------- GUI 冒烟 ----------
        gui_src = tmp / "gui_in"
        make_tree(gui_src)
        from tkinter import messagebox

        def _noop(*_a, **_k):
            return None

        messagebox.askyesno = lambda *_a, **_k: True
        messagebox.showinfo = _noop
        messagebox.showwarning = _noop
        messagebox.showerror = _noop

        import rating_gui

        app = rating_gui.RatingApp()
        app.withdraw()
        app.src_var.set(str(gui_src))
        app.out_var.set(str(tmp / "gui_out"))
        app.copy_var.set(True)
        app.do_preview()
        ok = wait_until(lambda: (app.update() or True) and bool(app.tree.get_children()) and not app.busy)
        check("GUI 预览完成", ok, app.status_var.get())
        roots = app.tree.get_children()
        check("GUI 预览 6 个等级节点", len(roots) == 6, str(len(roots)))
        labels = [app.tree.item(n, "text") for n in roots]
        check("GUI 节点文案含张数", all("张" in t for t in labels), str(labels))

        app.do_apply()
        ok2 = wait_until(lambda: (app.update() or True) and app.status_var.get().startswith("完成"))
        check("GUI 执行完成", ok2, app.status_var.get())
        gui_out = tmp / "gui_out"
        check(
            "GUI 输出 6 个分类目录",
            gui_out.is_dir() and {p.name for p in gui_out.iterdir()} == set(rs.FOLDER_ORDER),
            str(sorted(p.name for p in gui_out.iterdir())) if gui_out.is_dir() else "missing",
        )

        app.destroy()
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    failed_n = sum(1 for _, ok in results if not ok)
    print(f"\n{len(results) - failed_n}/{len(results)} 通过")
    return 1 if failed_n else 0


if __name__ == "__main__":
    sys.exit(main())
