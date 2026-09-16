#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import UnityPy
except ImportError:
    UnityPy = None  # type: ignore[misc, assignment]


BUNDLE_EXTS = {"", ".ab", ".assetbundle", ".unity3d", ".bundle", ".assets"}
# 排除已转换的 Android 产物（后缀 .android），避免下次 inventory 把它们当成新源包重复转换
SKIP_EXTS = {".json", ".txt", ".meta", ".xml", ".cs", ".py", ".md", ".zip", ".7z", ".exe", ".dll", ".android"}
KNOWN_MAGICS = (b"UnityFS", b"UnityWeb", b"UnityRaw")
KNOWN_MAGIC_STRS = {"UnityFS", "UnityWeb", "UnityRaw"}
BUNDLE_FILE_EXTS = (".ab", ".assetbundle", ".unity3d", ".bundle", ".assets")
PLACEHOLDER_VERSIONS = {"", "0.0.0", "5.x.x", "4.x.x", "3.x.x", "2.x.x", "1.x.x"}


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def _unquote(value: str) -> str:
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        return value[1:-1]
    return value


def _read_cstring(data: bytes, offset: int) -> tuple[str, int]:
    end = data.find(b"\x00", offset)
    if end < 0:
        return "", len(data)
    try:
        text = data[offset:end].decode("utf-8", errors="replace")
    except Exception:
        text = ""
    return text, end + 1


def _starts_with_magic(path: Path) -> bool:
    try:
        with path.open("rb") as handle:
            sig = handle.read(8)
    except OSError:
        return False
    return any(sig.startswith(magic) for magic in KNOWN_MAGICS)


def probe_unity_header(path: Path) -> tuple[str, str, bool]:
    """Return (magic, unityVersion, encryptedOrUnknown).

    UnityFS/UnityWeb/UnityRaw header (big-endian):
      signature     : null-terminated magic
      formatVersion : u32 (skipped)
      unityVersion  : null-terminated generator string, e.g. "5.x.x"
      unityRevision : null-terminated engine version, e.g. "2021.3.33f1"
    """
    try:
        with path.open("rb") as handle:
            data = handle.read(512)
    except OSError:
        return "", "", True
    if not any(data.startswith(magic) for magic in KNOWN_MAGICS):
        return "", "", True
    magic, offset = _read_cstring(data, 0)
    if magic not in KNOWN_MAGIC_STRS:
        return "", "", True
    if offset + 4 > len(data):
        return magic, "", False
    offset += 4
    generator, offset = _read_cstring(data, offset)
    revision, _ = _read_cstring(data, offset)
    version = revision.strip() if revision.strip() not in PLACEHOLDER_VERSIONS else ""
    if not version:
        version = generator.strip() if generator.strip() not in PLACEHOLDER_VERSIONS else generator.strip()
    return magic, version, False


def bundle_name_from_filename(path: Path) -> str:
    if path.suffix:
        return path.stem
    return path.name


def dependency_bundle_name(item: str) -> str:
    item = _unquote(item).replace("\\", "/")
    if not item or item in ("{}", "[]"):
        return ""
    name = item.rsplit("/", 1)[-1]
    lower = name.lower()
    for ext in BUNDLE_FILE_EXTS:
        if lower.endswith(ext):
            return name[: -len(ext)]
    return name


def _manifest_item(stripped: str) -> str:
    if stripped.startswith("-"):
        return _unquote(stripped[1:].strip())
    if ":" in stripped:
        _key, _, value = stripped.partition(":")
        return _unquote(value.strip())
    return ""


def parse_unity_manifest(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {"assets": [], "dependencies": [], "unityVersion": ""}
    try:
        text = path.read_text(encoding="utf-8-sig", errors="replace")
    except OSError:
        return result

    section: str | None = None
    for raw in text.splitlines():
        if not raw.strip():
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        stripped = raw.strip()
        is_list_item = stripped.startswith("-")
        if indent == 0 and not is_list_item:
            key, sep, value = stripped.partition(":")
            if not sep:
                section = None
                continue
            key = key.strip()
            value = value.strip()
            if key == "UnityVersion":
                result["unityVersion"] = _unquote(value)
                section = None
                continue
            if key == "Assets":
                section = "assets"
                if value in ("{}", "[]"):
                    section = None
                elif value:
                    result["assets"].append(_unquote(value))
                    section = None
                continue
            if key == "Dependencies":
                section = "deps"
                if value in ("{}", "[]"):
                    section = None
                elif value:
                    dep = dependency_bundle_name(value)
                    if dep:
                        result["dependencies"].append(dep)
                    section = None
                continue
            section = None
            continue
        if section is None:
            continue
        item = _manifest_item(stripped)
        if not item or item in ("{}", "[]"):
            continue
        if section == "assets":
            result["assets"].append(item)
        else:
            dep = dependency_bundle_name(item)
            if dep:
                result["dependencies"].append(dep)
    return result


def _unique(seq: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for item in seq:
        if item and item not in seen:
            seen.add(item)
            out.append(item)
    return out


def _object_type_name(obj: Any) -> str:
    type_info = getattr(obj, "type", None)
    if type_info is None:
        return ""
    name = getattr(type_info, "name", None)
    if name:
        return str(name)
    return str(type_info)


def _object_name(obj: Any) -> str:
    peek = getattr(obj, "peek_name", None)
    if callable(peek):
        try:
            name = peek()
            return str(name) if name else ""
        except Exception:
            return ""
    read_typetree = getattr(obj, "read_typetree", None)
    if callable(read_typetree):
        try:
            tree = read_typetree()
            if isinstance(tree, dict):
                name = tree.get("m_Name") or tree.get("name") or ""
                if name:
                    return str(name)
        except Exception:
            pass
    return ""


def _container_items(container: Any) -> list[tuple[str, Any]]:
    items: list[tuple[str, Any]] = []
    if container is None:
        return items
    try:
        raw_items = container.items() if hasattr(container, "items") else container
        for entry in raw_items:
            if isinstance(entry, (list, tuple)) and len(entry) >= 2:
                items.append((str(entry[0]), entry[1]))
    except Exception:
        return []
    return items


def enumerate_with_unitypy(
    path: Path,
    fallback_version: str,
    known_unity: bool,
    warnings: list[str],
) -> list[dict[str, str]]:
    if UnityPy is None:
        return []
    if fallback_version:
        try:
            UnityPy.config.FALLBACK_UNITY_VERSION = fallback_version
        except Exception:
            pass
    try:
        env = UnityPy.load(str(path))
    except Exception as exc:
        if known_unity:
            warnings.append(f"UnityPy failed to load {path.name}: {exc}")
        return []

    path_map: dict[tuple[int, int], str] = {}
    try:
        for cpath, obj in _container_items(getattr(env, "container", None)):
            try:
                path_id = int(getattr(obj, "path_id", 0))
                file_id = id(getattr(obj, "assets_file", obj))
                path_map[(file_id, path_id)] = cpath
            except Exception:
                continue
    except Exception as exc:
        warnings.append(f"UnityPy container read failed for {path.name}: {exc}")

    try:
        objects = list(getattr(env, "objects", []))
    except Exception as exc:
        warnings.append(f"UnityPy object list failed for {path.name}: {exc}")
        objects = []

    assets: list[dict[str, str]] = []
    seen: set[tuple[str, str, str]] = set()
    covered_paths: set[str] = set()
    for obj in objects:
        try:
            type_name = _object_type_name(obj)
            if type_name == "AssetBundle":
                continue
            path_id = int(getattr(obj, "path_id", 0))
            file_id = id(getattr(obj, "assets_file", obj))
            asset_path = path_map.get((file_id, path_id), "")
            name = _object_name(obj)
            if not name:
                if not asset_path:
                    continue
                name = Path(asset_path.replace("\\", "/")).stem
            key = (name, type_name, asset_path)
            if key in seen:
                continue
            seen.add(key)
            if asset_path:
                covered_paths.add(asset_path)
            assets.append({"name": name, "type": type_name, "path": asset_path})
        except Exception:
            continue

    for cpath in path_map.values():
        if cpath in covered_paths:
            continue
        name = Path(cpath.replace("\\", "/")).stem
        key = (name, "", cpath)
        if key in seen:
            continue
        seen.add(key)
        assets.append({"name": name, "type": "", "path": cpath})
    return assets


def merge_manifest_assets(assets: list[dict[str, str]], manifest_assets: list[str]) -> list[dict[str, str]]:
    existing_paths = {item["path"] for item in assets if item.get("path")}
    existing_names = {item["name"] for item in assets if item.get("name")}
    for raw_path in manifest_assets:
        asset_path = raw_path.replace("\\", "/")
        name = Path(asset_path).stem
        if asset_path in existing_paths or (not asset_path.startswith("Assets/") and name in existing_names):
            continue
        assets.append({"name": name, "type": "", "path": asset_path})
        existing_paths.add(asset_path)
    assets.sort(key=lambda item: (item.get("path") or "", item.get("name") or "", item.get("type") or ""))
    return assets


def pick_unity_version(versions: list[str], warnings: list[str]) -> str:
    usable = [ver.strip() for ver in versions if ver and ver.strip() not in PLACEHOLDER_VERSIONS]
    if not usable:
        usable = [ver.strip() for ver in versions if ver and ver.strip()]
    if not usable:
        return ""
    counts = Counter(usable)
    version, _ = counts.most_common(1)[0]
    if len(counts) > 1:
        detail = ", ".join(f"{ver} ({count})" for ver, count in counts.most_common())
        warnings.append(f"Multiple Unity versions detected: {detail}. Using most common: {version}")
    return version


def iter_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for dirpath, _dirnames, filenames in os.walk(root):
        for filename in filenames:
            files.append(Path(dirpath) / filename)
    return files


def unique_bundle_key(name: str, used: set[str]) -> str:
    if name not in used:
        return name
    index = 2
    while f"{name}#{index}" in used:
        index += 1
    return f"{name}#{index}"


def inventory(input_dir: Path, out_dir: Path) -> int:
    warnings: list[str] = []
    if UnityPy is None:
        warnings.append(
            "UnityPy is not installed; asset object lists come from filenames and .manifest files only."
        )

    manifests: dict[str, dict[str, Any]] = {}
    candidates: list[Path] = []
    for file_path in iter_files(input_dir):
        ext = file_path.suffix.lower()
        if ext == ".manifest":
            if file_path.name.lower().endswith(".android.manifest"):
                continue
            sibling = os.path.normcase(os.path.abspath(str(file_path)[: -len(".manifest")]))
            manifests[sibling] = parse_unity_manifest(file_path)
            continue
        if file_path.name.startswith(".") or ext in SKIP_EXTS:
            continue
        try:
            if file_path.stat().st_size <= 0:
                continue
        except OSError:
            continue
        has_magic = _starts_with_magic(file_path)
        if ext == "":
            if has_magic:
                candidates.append(file_path)
            continue
        if ext in BUNDLE_EXTS or has_magic:
            candidates.append(file_path)

    if not candidates:
        print(f"error: no AssetBundle candidates under {input_dir}", file=sys.stderr)
        return 2

    bundles: dict[str, dict[str, Any]] = {}
    used_keys: set[str] = set()
    versions: list[str] = []

    for file_path in sorted(candidates, key=lambda item: item.as_posix().lower()):
        try:
            rel = file_path.resolve().relative_to(input_dir).as_posix()
        except ValueError:
            rel = os.path.relpath(str(file_path), str(input_dir)).replace("\\", "/")
        try:
            size = file_path.stat().st_size
        except OSError as exc:
            warnings.append(f"Could not stat {rel}: {exc}")
            size = 0
        _magic, header_version, encrypted = probe_unity_header(file_path)
        manifest = manifests.get(os.path.normcase(os.path.abspath(str(file_path))), {})
        unity_version = header_version or str(manifest.get("unityVersion") or "")
        if unity_version:
            versions.append(unity_version)

        base_name = bundle_name_from_filename(file_path)
        if not base_name:
            warnings.append(f"Skipping unnamed candidate: {rel}")
            continue
        key = unique_bundle_key(base_name, used_keys)
        if key != base_name:
            warnings.append(f"Duplicate bundle name '{base_name}' for {rel}; stored as '{key}'")
        used_keys.add(key)

        assets: list[dict[str, str]] = []
        if UnityPy is not None:
            assets = enumerate_with_unitypy(file_path, unity_version, not encrypted, warnings)
        assets = merge_manifest_assets(assets, list(manifest.get("assets") or []))
        dependencies = _unique(list(manifest.get("dependencies") or []))

        bundles[key] = {
            "file": rel,
            "size": size,
            "unityVersion": unity_version,
            "encryptedOrUnknown": encrypted,
            "assets": assets,
            "dependencies": dependencies,
        }
        if encrypted:
            warnings.append(f"Encrypted or unknown header: {rel}")

    unity_version = pick_unity_version(versions, warnings)
    mapping = {
        "unityVersion": unity_version,
        "generatedAt": datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z"),
        "inputDir": str(input_dir),
        "warnings": warnings,
        "bundles": dict(sorted(bundles.items(), key=lambda item: item[0])),
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    mapping_path = out_dir / "mapping.json"
    with mapping_path.open("w", encoding="utf-8", newline="\n") as handle:
        json.dump(mapping, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    flat_items: list[dict[str, str]] = []
    for bundle_name, bundle in bundles.items():
        for asset in bundle.get("assets") or []:
            flat_items.append(
                {
                    "bundle": bundle_name,
                    "name": str(asset.get("name") or ""),
                    "type": str(asset.get("type") or ""),
                    "path": str(asset.get("path") or ""),
                }
            )
    with (out_dir / "ab_mapping_flat.json").open("w", encoding="utf-8", newline="\n") as handle:
        json.dump({"items": flat_items}, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
    warning_lines = warnings if warnings else ["No warnings."]
    (out_dir / "warnings.txt").write_text("\n".join(warning_lines) + "\n", encoding="utf-8")

    asset_count = sum(len(bundle["assets"]) for bundle in bundles.values())
    print(
        f"{len(bundles)} bundles, {asset_count} assets, unityVersion={unity_version or '(unknown)'}"
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Stage 1: inventory PC AssetBundles into mapping.json"
    )
    parser.add_argument("--input", required=True, help="Root directory of PC AssetBundles")
    parser.add_argument("--out", required=True, help="Output directory for mapping.json and warnings.txt")
    args = parser.parse_args(argv)

    input_dir = Path(args.input).expanduser()
    out_dir = Path(args.out).expanduser()
    try:
        input_dir = input_dir.resolve()
    except OSError:
        print(f"error: input directory not found: {args.input}", file=sys.stderr)
        return 2
    if not input_dir.is_dir():
        print(f"error: input directory not found: {input_dir}", file=sys.stderr)
        return 2
    try:
        out_dir = out_dir.resolve()
    except OSError:
        out_dir = Path(args.out).expanduser().absolute()
    return inventory(input_dir, out_dir)


if __name__ == "__main__":
    sys.exit(main())
