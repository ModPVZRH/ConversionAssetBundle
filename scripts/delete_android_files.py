#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""删除指定目录下所有后缀为 .android 与 .android.manifest 的文件。

用法：
    python delete_android_files.py [--root <目录>] [--dry-run]

默认递归扫描整个目录；跳过 .git/.vs/bin/obj 构建目录（可用 --include-build 取消）。
删除动作前建议先用 --dry-run 预览。
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

ANDROID_SUFFIX = ".android"
MANIFEST_SUFFIX = ".android.manifest"
SKIP_DIRS = {".git", ".vs", "bin", "obj"}


def iter_target_files(root: Path, skip: set[str]):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in skip]
        for fn in filenames:
            if fn.endswith(ANDROID_SUFFIX) or fn.endswith(MANIFEST_SUFFIX):
                yield Path(dirpath) / fn


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=".", help="要扫描的根目录（默认当前目录）")
    parser.add_argument("--dry-run", action="store_true", help="只列出将删除的文件，不真正删除")
    parser.add_argument("--include-build", action="store_true",
                        help="同时扫描 bin/obj/.vs 构建目录（默认跳过）")
    args = parser.parse_args()

    root = Path(args.root).expanduser()
    try:
        root = root.resolve()
    except OSError:
        root = Path(args.root).absolute()
    if not root.is_dir():
        print(f"error: 目录不存在: {root}", file=sys.stderr)
        return 2

    skip = set() if args.include_build else SKIP_DIRS

    deleted = 0
    for path in sorted(iter_target_files(root, skip)):
        rel = os.path.relpath(str(path), str(root))
        if args.dry_run:
            print(f"[dry-run] {rel}")
        else:
            try:
                path.unlink()
            except OSError as exc:
                print(f"error: 删除失败 {rel}: {exc}", file=sys.stderr)
                continue
            print(f"删除 {rel}")
        deleted += 1

    verb = "将删除" if args.dry_run else "已删除"
    print(f"\n{verb} {deleted} 个文件。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
