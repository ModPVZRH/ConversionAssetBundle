#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""一键把 BepInEx 项目适配为 PC/Android 双端，并清理控制台编码设置。

把原本分散的两步操作合并为一个脚本：
  1. 删除会令 Android(IL2CPP) 崩溃的 `Console.OutputEncoding = ...;` 行；
  2. 为 AssetBundle 加载代码追加 `.android` 平台后缀（按 `Application.platform` 判断），
     并把 `.android` 资源嵌入各插件 `.csproj`。

所有操作幂等，可重复执行。默认根目录为仓库下的 BepInEx 文件夹。

用法：
    python scripts/apply_android_support.py [--root <BepInEx目录>] [--dry-run]
"""

from __future__ import annotations

import argparse
import os
import re
import sys
from pathlib import Path

ANDROID_SUFFIX = ".android"

# 匹配各种写法的控制台编码设置行（含 System.Console / Encoding.UTF8 全限定形式）
CONSOLE_ENC_RE = re.compile(
    r"^\s*(System\.)?Console\.OutputEncoding\s*=\s*(System\.Text\.)?Encoding\.UTF8\s*;\s*$"
)

# 匹配 CustomCore.GetAssetBundle(..., "bundleName") 调用里的完整字符串字面量（含引号）
GET_AB_NAME_RE = re.compile(r'CustomCore\.GetAssetBundle\([^;]*?,\s*("[^"]*")')

# 匹配本地 helper 里的 resourceName 赋值：string resourceName = <expr>;
RESOURCE_NAME_RE = re.compile(r'(\bstring\s+resourceName\s*=\s*)([^;]+?)\s*;')

# csproj 里的资源项
EMBED_RE = re.compile(r'(?P<indent>\s*)<EmbeddedResource Include="(?P<name>[^"]+)"\s*/>')
NONE_RE = re.compile(r'(?P<indent>\s*)<None Remove="(?P<name>[^"]+)"\s*/>')

ANDROID_PLATFORM_EXPR = 'Application.platform == RuntimePlatform.Android ? ".android" : ""'


def read_text(path: Path) -> tuple[str, bool, str]:
    """返回 (text, had_bom, newline)，保留 BOM 与换行风格。"""
    raw = path.read_bytes()
    bom = raw.startswith(b"\xef\xbb\xbf")
    body = raw[3:] if bom else raw
    text = body.decode("utf-8", errors="surrogateescape")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, bom, newline


def write_text(path: Path, text: str, bom: bool) -> None:
    raw = text.encode("utf-8", errors="surrogateescape")
    if bom:
        raw = b"\xef\xbb\xbf" + raw
    path.write_bytes(raw)


def iter_cs_files(root: Path):
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "bin", "obj", ".vs")]
        for fn in filenames:
            if fn.endswith(".cs"):
                yield Path(dirpath) / fn


def collect_android_names(root: Path) -> set[str]:
    """扫描所有 .android 文件，得到有 Android 产物的 bundle 基础名集合。"""
    names: set[str] = set()
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "bin", "obj", ".vs")]
        for fn in filenames:
            if fn.endswith(ANDROID_SUFFIX) and not fn.endswith(ANDROID_SUFFIX + ".manifest"):
                names.add(fn[: -len(ANDROID_SUFFIX)])
    return names


def remove_console_encoding(lines: list[str]) -> tuple[list[str], int]:
    """删除控制台编码设置行，返回 (新行列表, 删除数)。"""
    kept = [line for line in lines if not CONSOLE_ENC_RE.match(line)]
    return kept, len(lines) - len(kept)


def adapt_get_asset_bundle(line: str, cs_dir: Path) -> tuple[str, bool]:
    """为 CustomCore.GetAssetBundle(..., "name") 追加 .android 平台后缀。

    仅当同名 .android 产物与源文件在同一项目目录时才追加（嵌入式资源按项目目录放置）。
    """
    if "RuntimePlatform.Android" in line:
        return line, False
    m = GET_AB_NAME_RE.search(line)
    if not m:
        return line, False
    quoted = m.group(1)  # 含引号的完整字面量，例如 "salmon"
    name = quoted[1:-1]
    if not name:
        return line, False
    if not (cs_dir / f"{name}{ANDROID_SUFFIX}").is_file():
        return line, False
    replacement = quoted + ' + (' + ANDROID_PLATFORM_EXPR + ')'
    return line[: m.start(1)] + replacement + line[m.end(1) :], True


def adapt_resource_name(line: str) -> tuple[str, bool]:
    """把 string resourceName = <expr>; 包成平台三元表达式。"""
    if "RuntimePlatform.Android" in line:
        return line, False
    m = RESOURCE_NAME_RE.search(line)
    if not m:
        return line, False
    expr = m.group(2).strip()
    if not expr:
        return line, False
    replacement = (
        m.group(1)
        + "Application.platform == RuntimePlatform.Android ? "
        + expr
        + ' + ".android" : '
        + expr
        + ";"
    )
    return line[: m.start()] + replacement + line[m.end() :], True


def ensure_using_unityengine(lines: list[str]) -> bool:
    """若文件用到 RuntimePlatform.Android 却缺 using UnityEngine;，则补上。"""
    joined = "\n".join(lines)
    if "RuntimePlatform.Android" not in joined or "using UnityEngine;" in joined:
        return False
    insert_at = 0
    for i, line in enumerate(lines):
        if line.lstrip().startswith("using ") and ";" in line:
            insert_at = i + 1
    lines.insert(insert_at, "using UnityEngine;")
    return True


def process_cs_file(path: Path, dry_run: bool) -> list[str]:
    """处理单个 .cs 文件，返回变更描述列表。"""
    text, bom, nl = read_text(path)
    lines = text.split(nl)

    changed = False
    changes: list[str] = []
    cs_dir = path.parent

    # 1) 删控制台编码
    new_lines, removed = remove_console_encoding(lines)
    if removed:
        lines = new_lines
        changed = True
        changes.append(f"- {removed} 行 Console.OutputEncoding")

    # 2) 适配 AB 加载（CustomCore.GetAssetBundle 或本地 resourceName）
    has_manifest_stream = any("GetManifestResourceStream" in l for l in lines)
    dir_has_android = any(
        (cs_dir / f).is_file()
        for f in os.listdir(cs_dir)
        if f.endswith(ANDROID_SUFFIX) and not f.endswith(ANDROID_SUFFIX + ".manifest")
    )
    for i, line in enumerate(lines):
        new_line, ok = adapt_get_asset_bundle(line, cs_dir)
        if ok:
            lines[i] = new_line
            changed = True
            changes.append(f"+ CustomCore.GetAssetBundle(.android) 于第 {i + 1} 行")
            continue
        if has_manifest_stream and dir_has_android:
            new_line, ok = adapt_resource_name(line)
            if ok:
                lines[i] = new_line
                changed = True
                changes.append(f"+ resourceName(.android) 于第 {i + 1} 行")

    # 3) 补 using UnityEngine;
    if ensure_using_unityengine(lines):
        changed = True
        changes.append("+ using UnityEngine;")

    if not changed:
        return []

    if not dry_run:
        write_text(path, nl.join(lines), bom)
    return changes


def update_csproj(csproj: Path, dry_run: bool) -> list[str]:
    """把 .android 资源嵌入 csproj，返回变更描述列表。"""
    android_files = [
        f
        for f in os.listdir(csproj.parent)
        if f.endswith(ANDROID_SUFFIX) and not f.endswith(ANDROID_SUFFIX + ".manifest")
    ]
    if not android_files:
        return []

    text, bom, nl = read_text(csproj)
    changes: list[str] = []
    embedded_names = {m.group("name") for m in EMBED_RE.finditer(text)}
    none_names = {m.group("name") for m in NONE_RE.finditer(text)}

    def indentation_for(regex, name):
        for m in regex.finditer(text):
            if m.group("name") == name:
                return m.group("indent")
        return "    "

    for af in sorted(android_files):
        pc_name = af[: -len(ANDROID_SUFFIX)]

        if af not in embedded_names and pc_name in embedded_names:
            indent = indentation_for(EMBED_RE, pc_name)
            new_line = f'{indent}<EmbeddedResource Include="{af}" />{nl}'
            anchor = f'<EmbeddedResource Include="{pc_name}" />'
            idx = text.find(anchor)
            if idx != -1:
                end = text.find("\n", idx) + 1
                text = text[:end] + new_line + text[end:]
                embedded_names.add(af)
                changes.append(f"+ EmbeddedResource {af}")

        if af not in none_names and pc_name in none_names:
            indent = indentation_for(NONE_RE, pc_name)
            new_line = f'{indent}<None Remove="{af}" />{nl}'
            anchor = f'<None Remove="{pc_name}" />'
            idx = text.find(anchor)
            if idx != -1:
                end = text.find("\n", idx) + 1
                text = text[:end] + new_line + text[end:]
                none_names.add(af)
                changes.append(f"+ None Remove {af}")

    if changes and not dry_run:
        write_text(csproj, text, bom)
    return changes


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", default=None, help="BepInEx 目录（默认仓库下 BepInEx）")
    parser.add_argument("--dry-run", action="store_true", help="只报告不写入")
    args = parser.parse_args()

    scripts_dir = Path(__file__).resolve().parent
    root = Path(args.root).expanduser() if args.root else scripts_dir.parent.parent / "BepInEx"
    try:
        root = root.resolve()
    except OSError:
        root = Path(args.root).absolute()
    if not root.is_dir():
        print(f"error: BepInEx 目录不存在: {root}", file=sys.stderr)
        return 2

    android_names = collect_android_names(root)
    print(f"发现 {len(android_names)} 个 .android 产物。")
    if not android_names:
        print("警告：未发现任何 .android 文件，AB 加载适配将被跳过。"
              "请先运行转换脚本（convert.ps1）生成 .android 产物。")

    total_cs = 0
    total_csproj = 0
    changed_any = False

    for path in sorted(iter_cs_files(root)):
        changes = process_cs_file(path, args.dry_run)
        if changes:
            total_cs += 1
            changed_any = True
            if args.dry_run:
                print(f"[dry-run] {os.path.relpath(path, root)}:")
            else:
                print(f"{os.path.relpath(path, root)}:")
            for c in changes:
                print(f"    {c}")

    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in (".git", "bin", "obj", ".vs")]
        for fn in filenames:
            if not fn.endswith(".csproj"):
                continue
            csproj = Path(dirpath) / fn
            changes = update_csproj(csproj, args.dry_run)
            if changes:
                total_csproj += 1
                changed_any = True
                print(f"{os.path.relpath(csproj, root)}:")
                for c in changes:
                    print(f"    {c}")

    verb = "将修改" if args.dry_run else "已修改"
    print()
    print(f"{verb} {total_cs} 个 .cs 文件、{total_csproj} 个 .csproj 文件。")
    if not changed_any:
        print("没有需要修改的文件（可能已全部适配完成）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
