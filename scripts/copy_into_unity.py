#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Copy AssetRipper output into work/unity-project for Android AssetBundle rebuild."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
from pathlib import Path
from typing import Any

IGNORE_DIR_NAMES = {"library", "temp", "logs", "obj"}


def repo_root() -> Path:
    return Path(__file__).resolve().parent.parent


def resolve_config_path(repo: Path, raw: str) -> Path:
    given = Path(raw)
    candidates = []
    if given.is_absolute():
        candidates.append(given)
    else:
        candidates.append((Path.cwd() / given).resolve())
        candidates.append((repo / given).resolve())
    for path in candidates:
        if path.is_file():
            return path
    return candidates[0]


def resolve_from_repo(repo: Path, raw: str | None, default: str) -> Path:
    value = raw if raw else default
    path = Path(value)
    if not path.is_absolute():
        path = repo / path
    return path.resolve()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8-sig") as handle:
        return json.load(handle)


def ignore_copy(_directory: str, contents: list[str]) -> list[str]:
    return [name for name in contents if name.lower() in IGNORE_DIR_NAMES]


def rmtree(path: Path) -> None:
    if not path.exists():
        return

    def onerror(func, item, _exc_info):
        try:
            os.chmod(item, 0o700)
            func(item)
        except OSError:
            raise

    if path.is_file() or path.is_symlink():
        path.unlink()
        return
    shutil.rmtree(path, onerror=onerror)


def copytree_replace(src: Path, dst: Path) -> None:
    rmtree(dst)
    shutil.copytree(src, dst, ignore=ignore_copy)


def find_ripped_assets(ripped_root: Path) -> Path | None:
    exported = ripped_root / "ExportedProject" / "Assets"
    if exported.is_dir():
        return exported
    direct = ripped_root / "Assets"
    if direct.is_dir():
        return direct
    return None


def overlay_editor_scripts(template_dir: Path, unity_project: Path) -> None:
    src_editor = template_dir / "Assets" / "Editor"
    dst_editor = unity_project / "Assets" / "Editor"
    dst_editor.mkdir(parents=True, exist_ok=True)
    if not src_editor.is_dir():
        print(f"warning: template Editor folder missing: {src_editor}", file=sys.stderr)
        return
    for cs_file in sorted(src_editor.glob("*.cs")):
        shutil.copy2(cs_file, dst_editor / cs_file.name)


def flatten_mapping(mapping: dict[str, Any]) -> dict[str, Any]:
    items: list[dict[str, str]] = []
    bundles = mapping.get("bundles") or {}
    if isinstance(bundles, dict):
        for bundle_name, bundle in bundles.items():
            if not isinstance(bundle, dict):
                continue
            assets = bundle.get("assets") or []
            if not isinstance(assets, list):
                continue
            for asset in assets:
                if not isinstance(asset, dict):
                    continue
                items.append(
                    {
                        "bundle": str(bundle_name),
                        "name": str(asset.get("name") or ""),
                        "type": str(asset.get("type") or ""),
                        "path": str(asset.get("path") or ""),
                    }
                )
    return {"items": items}


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def copy_if_missing(src: Path, dst: Path) -> None:
    if dst.exists() or not src.is_file():
        return
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)


def write_mapping_files(mapping_src: Path, editor_dir: Path) -> None:
    editor_dir.mkdir(parents=True, exist_ok=True)
    if mapping_src.is_file():
        shutil.copy2(mapping_src, editor_dir / "ab_mapping.json")
        mapping = load_json(mapping_src)
        if not isinstance(mapping, dict):
            mapping = {}
        flat = flatten_mapping(mapping)
        write_json(editor_dir / "ab_mapping_flat.json", flat)
        write_json(mapping_src.parent / "ab_mapping_flat.json", flat)
        return
    print(f"warning: mapping.json not found at {mapping_src}", file=sys.stderr)
    write_json(editor_dir / "ab_mapping_flat.json", {"items": []})


def main() -> int:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

    parser = argparse.ArgumentParser(
        description="Copy ripped assets into work/unity-project for AbRebuild."
    )
    parser.add_argument("--config", required=True, help="Path to config.json")
    args = parser.parse_args()

    repo = repo_root()
    config_path = resolve_config_path(repo, args.config)
    if not config_path.is_file():
        print(f"config not found: {config_path}", file=sys.stderr)
        return 1

    cfg = load_json(config_path)
    if not isinstance(cfg, dict):
        print(f"config is not an object: {config_path}", file=sys.stderr)
        return 1

    work_dir = resolve_from_repo(repo, cfg.get("workDir"), "work")
    template_dir = resolve_from_repo(repo, cfg.get("unityTemplateDir"), "unity-template")
    mode = str(cfg.get("unityProjectMode") or "template").strip().lower()

    if not template_dir.is_dir():
        print(f"unity template not found: {template_dir}", file=sys.stderr)
        return 1

    ripped_root = work_dir / "ripped"
    ripped_assets = find_ripped_assets(ripped_root)
    if ripped_assets is None:
        print(
            "ripped Assets not found. Expected "
            f"{ripped_root / 'ExportedProject' / 'Assets'} or {ripped_root / 'Assets'}",
            file=sys.stderr,
        )
        return 2

    unity_project = work_dir / "unity-project"
    mapping_src = work_dir / "inventory" / "mapping.json"

    if mode == "ripped":
        copy_root = ripped_assets.parent
        print(f"mode=ripped copy {copy_root} -> {unity_project}")
        copytree_replace(copy_root, unity_project)
    else:
        print(f"mode=template copy {template_dir} -> {unity_project}")
        copytree_replace(template_dir, unity_project)
        ripped_dst = unity_project / "Assets" / "Ripped"
        rmtree(ripped_dst)
        shutil.copytree(ripped_assets, ripped_dst, ignore=ignore_copy)
        print(f"copied ripped Assets -> {ripped_dst}")

    overlay_editor_scripts(template_dir, unity_project)
    editor_dir = unity_project / "Assets" / "Editor"
    write_mapping_files(mapping_src, editor_dir)

    copy_if_missing(
        template_dir / "ProjectSettings" / "ProjectVersion.txt",
        unity_project / "ProjectSettings" / "ProjectVersion.txt",
    )
    copy_if_missing(
        template_dir / "Packages" / "manifest.json",
        unity_project / "Packages" / "manifest.json",
    )

    print(f"unity project ready: {unity_project}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
