#!/usr/bin/env python3
"""Generate an android-aware mapping from inventory mapping.json.

The input `mapping.json` is produced by the `inventory` stage and lists every
PC AssetBundle under `inputDir`. After the `build` stage, each converted
bundle is written back next to its PC source with a `.android` suffix.

This script filters the inventory down to the bundles that actually have a
matching `.android` artifact on disk and groups them by project directory, so
the result is a source of truth for which plugin projects need android AB
support (embedding the `.android` file and loading it on Android).
"""

import json
import os
import sys
from datetime import datetime, timezone

ANDROID_SUFFIX = ".android"


def find_csproj(project_dir: str) -> str | None:
    """Return the single .csproj inside `project_dir`, if any."""
    if not os.path.isdir(project_dir):
        return None
    csprojs = [f for f in os.listdir(project_dir) if f.endswith(".csproj")]
    if len(csprojs) == 1:
        return csprojs[0]
    return None


def main() -> int:
    here = os.path.dirname(os.path.abspath(__file__))
    root = os.path.dirname(here)  # ConversionAssetBundle/
    src = os.path.join(root, "work", "inventory", "mapping.json")
    dst = os.path.join(root, "work", "inventory", "mapping_android.json")

    with open(src, encoding="utf-8") as f:
        mapping = json.load(f)

    input_dir = mapping.get("inputDir", "")
    bundles = mapping.get("bundles", {})

    included = []       # bundle entries that have an android artifact
    excluded = []       # bundle entries without one
    by_dir: dict[str, list[dict]] = {}

    for name, info in bundles.items():
        file_rel = info["file"]  # relative to inputDir, forward slashes
        pc_path = os.path.join(input_dir, file_rel.replace("/", os.sep))
        android_rel = file_rel + ANDROID_SUFFIX
        android_path = pc_path + ANDROID_SUFFIX

        entry = {
            "name": name,
            "file": file_rel,
            "androidFile": android_rel,
            "size": info.get("size"),
            "unityVersion": info.get("unityVersion"),
        }
        if os.path.isfile(android_path):
            included.append(entry)
            project_dir = os.path.dirname(file_rel).replace("/", os.sep)
            by_dir.setdefault(project_dir, []).append(entry)
        else:
            excluded.append({"name": name, "file": file_rel})

    projects = []
    for project_dir, entries in sorted(by_dir.items()):
        csproj_name = find_csproj(os.path.join(input_dir, project_dir))
        projects.append(
            {
                "directory": project_dir.replace(os.sep, "/"),
                "csproj": csproj_name,
                "bundles": entries,
            }
        )

    result = {
        "generatedAt": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "source": "work/inventory/mapping.json",
        "inputDir": input_dir,
        "androidSuffix": ANDROID_SUFFIX,
        "summary": {
            "totalBundles": len(bundles),
            "bundlesWithAndroid": len(included),
            "bundlesWithoutAndroid": len(excluded),
            "projectDirectories": len(projects),
        },
        "excludedBundles": excluded,
        "projects": projects,
    }

    with open(dst, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
        f.write("\n")

    print(f"Wrote {dst}")
    print(
        f"  {len(included)} bundles with android / {len(bundles)} total "
        f"across {len(projects)} project directories"
    )
    print(f"  excluded ({len(excluded)}): "
          + ", ".join(e["name"] for e in excluded))
    return 0


if __name__ == "__main__":
    sys.exit(main())
