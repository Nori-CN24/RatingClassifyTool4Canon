#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
rating_sort.py — 按 XMP 评分把 JPG / CR3 归类到不同子文件夹。

设计要点：
- 只让 ExifTool 提取 XMP:Rating 一个标签，不请求预览图，也不解码 RAW 像素；
- 批量调用 ExifTool（每批 500 个文件），把进程启动开销摊薄；
- 评分 1~5 归入「N星」，0 / 无评分归入「0-未评分」；
- 默认原地移动，支持 --copy / --dry-run。

本模块的函数同时被命令行（本文件）和 GUI（rating_gui.py）复用。
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Callable

IMAGE_EXTS = {".jpg", ".jpeg", ".cr3"}
BATCH_SIZE = 500
RATING_FOLDERS = {1: "1星", 2: "2星", 3: "3星", 4: "4星", 5: "5星"}
UNRATED_FOLDER = "0-未评分"
FOLDER_ORDER = [UNRATED_FOLDER, *RATING_FOLDERS.values()]


def _norm_key(p: str | Path) -> str:
    """把路径统一成可比较的 key（Windows 下大小写不敏感）。"""
    return os.path.normcase(os.path.normpath(str(p)))


def app_dir() -> Path:
    """程序所在目录：源码运行时是脚本目录，PyInstaller 打包后是 exe 所在目录。"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def bundled_exiftool() -> str | None:
    """程序同目录 `exiftool/` 里自带的 ExifTool 路径，没有就返回 None。"""
    exe = app_dir() / "exiftool" / ("exiftool.exe" if os.name == "nt" else "exiftool")
    return str(exe) if exe.is_file() else None


def default_exiftool() -> str:
    """优先用随包附带的 ExifTool，其次用 PATH 里的 exiftool。"""
    return bundled_exiftool() or "exiftool"


def exiftool_available(path: str) -> bool:
    """检查 ExifTool 路径能不能用（PATH 里的名字或具体文件都行）。"""
    if os.sep in path or "/" in path:
        return Path(path).is_file()
    return shutil.which(path) is not None


def find_files(source: Path, recursive: bool, excluded_dirs: list[Path]) -> list[Path]:
    iterator = source.rglob("*") if recursive else source.iterdir()
    files: list[Path] = []
    for p in iterator:
        if not p.is_file() or p.suffix.lower() not in IMAGE_EXTS:
            continue
        if any(p.is_relative_to(d) for d in excluded_dirs):
            continue
        files.append(p)
    return sorted(files, key=lambda x: str(x).lower())


def compute_excluded_dirs(source: Path, output: Path, recursive: bool) -> list[Path]:
    """递归扫描时要跳过的目录。

    - 原地整理（output == source）：跳过上次生成的分类目录；
    - 输出目录在源目录内部：跳过整个输出目录（否则会反复处理输出里的副本）；
    - 其他情况：不跳过任何目录。
    """
    if not recursive:
        return []
    if output == source:
        return [output / name for name in FOLDER_ORDER]
    if output.is_relative_to(source):
        return [output]
    return []


def read_ratings(
    files: list[Path],
    exiftool: str,
    on_progress: Callable[[int, int], None] | None = None,
) -> dict[Path, int | None]:
    ratings: dict[Path, int | None] = {}
    total = len(files)
    for start in range(0, total, BATCH_SIZE):
        batch = files[start:start + BATCH_SIZE]
        ratings.update(_read_batch(batch, exiftool))
        if on_progress is not None:
            on_progress(min(start + len(batch), total), total)
    return ratings


def _read_batch(batch: list[Path], exiftool: str) -> dict[Path, int | None]:
    fd, argfile = tempfile.mkstemp(prefix="ratingtool_", suffix=".txt")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as f:
            f.writelines(f"{p.resolve()}\n" for p in batch)

        cmd = [
            exiftool,
            "-j", "-n", "-q", "-q",
            "-fast",
            "-charset", "filename=utf8",
            "-XMP:Rating",
            "-@", argfile,
        ]
        try:
            proc = subprocess.run(
                cmd, capture_output=True, text=True,
                encoding="utf-8", errors="replace",
            )
        except FileNotFoundError:
            raise SystemExit(f"找不到 ExifTool：{exiftool}") from None
        except OSError as exc:
            raise SystemExit(f"启动 ExifTool 失败：{exc}") from None

        # 返回码 1 表示个别文件有警告，JSON 仍然有效
        if proc.returncode not in (0, 1):
            raise SystemExit(f"ExifTool 出错（{proc.returncode}）：{proc.stderr.strip()}")

        try:
            payload = json.loads(proc.stdout) if proc.stdout.strip() else []
        except json.JSONDecodeError as exc:
            raise SystemExit(f"无法解析 ExifTool 输出：{exc}") from None
    finally:
        try:
            os.unlink(argfile)
        except OSError:
            pass

    raw: dict[str, int | None] = {}
    for item in payload:
        src = item.get("SourceFile")
        if not src:
            continue
        value = item.get("Rating")
        if isinstance(value, bool):
            value = None
        elif isinstance(value, str):
            value = int(value) if value.lstrip("-").isdigit() else None
        raw[_norm_key(src)] = value

    return {p: raw.get(_norm_key(p.resolve())) for p in batch}


def target_folder(rating: int | None) -> str:
    return RATING_FOLDERS.get(rating, UNRATED_FOLDER)


def unique_path(dst: Path) -> Path:
    if not dst.exists():
        return dst
    i = 1
    while True:
        cand = dst.with_name(f"{dst.stem}_{i}{dst.suffix}")
        if not cand.exists():
            return cand
        i += 1


def plan_buckets(
    files: list[Path], ratings: dict[Path, int | None]
) -> dict[str, list[Path]]:
    """按评分把文件分组，保持 FOLDER_ORDER 的显示顺序。"""
    buckets: dict[str, list[Path]] = {name: [] for name in FOLDER_ORDER}
    for p in files:
        buckets[target_folder(ratings.get(p))].append(p)
    return {name: items for name, items in buckets.items() if items}


def apply_buckets(
    buckets: dict[str, list[Path]],
    output: Path,
    copy_mode: bool,
    on_progress: Callable[[int, int], None] | None = None,
) -> tuple[int, int, int, list[tuple[Path, Path, str]]]:
    """按分组把文件复制/移动到 output，返回 (复制数, 移动数, 失败数, 错误列表)。"""
    copied = moved = failed = 0
    errors: list[tuple[Path, Path, str]] = []
    total = sum(len(items) for items in buckets.values())
    done = 0
    for folder in FOLDER_ORDER:
        items = buckets.get(folder)
        if not items:
            continue
        dest_dir = output / folder
        dest_dir.mkdir(parents=True, exist_ok=True)
        for src in items:
            dst = unique_path(dest_dir / src.name)
            try:
                if copy_mode:
                    shutil.copy2(src, dst)
                    copied += 1
                else:
                    shutil.move(str(src), str(dst))
                    moved += 1
            except OSError as exc:
                failed += 1
                errors.append((src, dst, str(exc)))
            done += 1
            if on_progress is not None:
                on_progress(done, total)
    return copied, moved, failed, errors


def main() -> int:
    parser = argparse.ArgumentParser(
        description="按 XMP 评分把 JPG / CR3 分类到子文件夹（只读元数据，不解 RAW）",
    )
    parser.add_argument("source", help="源目录")
    parser.add_argument("-o", "--output", help="输出目录（默认：源目录本身）")
    parser.add_argument("--copy", action="store_true", help="复制而不是移动")
    parser.add_argument("--dry-run", action="store_true", help="只列出分类结果，不动文件")
    parser.add_argument("--recursive", action="store_true", help="递归扫描子目录")
    parser.add_argument("--exiftool", default=None, help="ExifTool 可执行文件路径（默认：自带的 exiftool/，否则 PATH）")
    args = parser.parse_args()
    exiftool = args.exiftool or default_exiftool()

    source = Path(args.source).expanduser().resolve()
    if not source.is_dir():
        print(f"源目录不存在：{source}", file=sys.stderr)
        return 2

    output = Path(args.output).expanduser().resolve() if args.output else source

    excluded_dirs = compute_excluded_dirs(source, output, args.recursive)
    files = find_files(source, args.recursive, excluded_dirs)
    if not files:
        print("没有找到 JPG / CR3 文件。")
        return 0

    print(f"找到 {len(files)} 个文件，读取 XMP 评分…")
    ratings = read_ratings(files, exiftool)

    buckets = plan_buckets(files, ratings)

    print("\n分类统计：")
    for folder in FOLDER_ORDER:
        if folder in buckets:
            print(f"  {folder}: {len(buckets[folder])}")

    if args.dry_run:
        print("\n[dry-run] 预览（不移动文件）：")
        for folder in FOLDER_ORDER:
            for p in buckets.get(folder, []):
                print(f"  {p}  ->  {output / folder / p.name}")
        return 0

    copied, moved, failed, errors = apply_buckets(buckets, output, args.copy)
    for src, dst, msg in errors:
        print(f"失败：{src} -> {dst}：{msg}", file=sys.stderr)

    done = copied if args.copy else moved
    print(f"\n完成：{'复制' if args.copy else '移动'} {done} 个，失败 {failed} 个。")
    return 1 if failed else 0


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass
    raise SystemExit(main())
