#!/usr/bin/env python3
"""Embed `.android` AssetBundles into their plugin `.csproj` files.

Each plugin project embeds its PC AssetBundle as an `EmbeddedResource` (with an
optional `<None Remove=... />` to keep the implicit glob from also treating it
as `None`). The converted Android bundles sit next to the PC ones with an
`.android` suffix; this script adds matching `EmbeddedResource` / `None Remove`
entries for them so the Android bundle is compiled into the plugin DLL and can
be loaded via `Application.platform` at runtime.

Usage:
    python scripts/embed_android_resources.py [--dry-run] [--input-dir <dir>]
"""

import argparse
import os
import re
import sys

ANDROID_SUFFIX = ".android"
EMBED_RE = re.compile(r'(?P<indent>\s*)<EmbeddedResource Include="(?P<name>[^"]+)"\s*/>')
NONE_RE = re.compile(r'(?P<indent>\s*)<None Remove="(?P<name>[^"]+)"\s*/>')


def read_text(path: str) -> tuple[str, bool, str]:
    """Return (text, had_bom, newline) preserving BOM + line-ending info."""
    with open(path, "rb") as f:
        raw = f.read()
    bom = raw.startswith(b"\xef\xbb\xbf")
    text = raw.decode("utf-8-sig")
    newline = "\r\n" if "\r\n" in text else "\n"
    return text, bom, newline


def write_text(path: str, text: str, bom: bool) -> None:
    with open(path, "wb") as f:
        if bom:
            f.write(b"\xef\xbb\xbf")
        f.write(text.encode("utf-8"))


def update_csproj(csproj: str, android_files: list[str]) -> list[str]:
    """Insert entries for each android file; return human-readable changes."""
    changes = []
    text, bom, nl = read_text(csproj)
    lines = text.splitlines(keepends=True)  # preserves original newlines

    embedded_names = set(m.group("name") for m in EMBED_RE.finditer(text))
    none_names = set(m.group("name") for m in NONE_RE.finditer(text))

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
            # insert after the PC EmbeddedResource line (searching raw text)
            anchor = f'<EmbeddedResource Include="{pc_name}" />'
            idx = text.find(anchor)
            if idx != -1:
                end = text.find("\n", idx) + 1
                text = text[:end] + new_line + text[end:]
                embedded_names.add(af)
                changes.append(f"  + EmbeddedResource {af}")

        if af not in none_names and pc_name in none_names:
            indent = indentation_for(NONE_RE, pc_name)
            new_line = f'{indent}<None Remove="{af}" />{nl}'
            anchor = f'<None Remove="{pc_name}" />'
            idx = text.find(anchor)
            if idx != -1:
                end = text.find("\n", idx) + 1
                text = text[:end] + new_line + text[end:]
                none_names.add(af)
                changes.append(f"  + None Remove {af}")

    return changes, text, bom


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--input-dir", default=None)
    args = parser.parse_args()

    input_dir = args.input_dir or os.path.normpath(
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "..", "BepInEx")
    )

    total_edited = 0
    total_added = 0
    for root, dirs, files in os.walk(input_dir):
        dirs[:] = [d for d in dirs if d not in (".git", "bin", "obj", ".vs")]
        for fn in files:
            if not fn.endswith(".csproj"):
                continue
            csproj = os.path.join(root, fn)
            android_files = [
                f for f in os.listdir(root)
                if f.endswith(ANDROID_SUFFIX) and not f.endswith(ANDROID_SUFFIX + ".manifest")
            ]
            if not android_files:
                continue
            changes, text, bom = update_csproj(csproj, android_files)
            if changes:
                total_edited += 1
                total_added += len(changes)
                rel = os.path.relpath(csproj, input_dir)
                print(f"{'[dry-run] ' if args.dry_run else ''}{rel}:")
                for c in changes:
                    print(c)
                if not args.dry_run:
                    write_text(csproj, text, bom)

    print()
    verb = "would update" if args.dry_run else "updated"
    print(f"{verb} {total_edited} csproj file(s), {total_added} entry(ies).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
