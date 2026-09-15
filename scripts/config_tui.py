#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path
from typing import Any

try:
    from textual.app import App, ComposeResult
    from textual.binding import Binding
    from textual.containers import Horizontal, Vertical, VerticalScroll
    from textual.screen import ModalScreen
    from textual.widgets import Button, DirectoryTree, Footer, Header, Input, Label, Select, Static
except ImportError:
    print("缺少 textual。请先执行:  pip install textual", file=sys.stderr)
    sys.exit(2)


REPO = Path(__file__).resolve().parent.parent

TEXTURE_OPTS = [("ASTC", "ASTC"), ("ETC2", "ETC2"), ("DXT", "DXT")]
ASTC_OPTS = [
    ("4x4  最大画质", "4x4"),
    ("5x5", "5x5"),
    ("6x6  推荐画质", "6x6"),
    ("8x8  体积/画质折中", "8x8"),
    ("10x10", "10x10"),
    ("12x12  最小体积", "12x12"),
]
MAX_SIZE_OPTS = [
    ("不限制", "0"),
    ("512", "512"),
    ("1024", "1024"),
    ("2048", "2048"),
    ("4096", "4096"),
]
COMPRESSION_OPTS = [
    ("lzma  最小，加载较慢", "lzma"),
    ("lz4   较大，加载较快", "lz4"),
    ("none  不压缩", "none"),
]
MODE_OPTS = [
    ("ripped    用 AssetRipper 导出工程打包", "ripped"),
    ("template  灌进 unity-template", "template"),
]


def repo_path() -> Path:
    return REPO


def load_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    return data if isinstance(data, dict) else {}


def save_json(path: Path, data: dict[str, Any]) -> None:
    text = json.dumps(data, ensure_ascii=False, indent=2)
    path.write_text(text + "\n", encoding="utf-8")


def find_unity_editors() -> list[tuple[str, str]]:
    roots = [
        Path(r"C:\Program Files\Unity\Hub\Editor"),
        Path(r"D:\Program Files\Unity\Hub\Editor"),
        Path(r"C:\Program Files (x86)\Unity\Hub\Editor"),
        Path.home() / "Unity" / "Hub" / "Editor",
    ]
    found: list[tuple[str, str]] = []
    seen: set[str] = set()
    for root in roots:
        if not root.is_dir():
            continue
        for child in sorted(root.iterdir(), reverse=True):
            exe = child / "Editor" / "Unity.exe"
            if not exe.is_file():
                continue
            key = str(exe.resolve()).lower()
            if key in seen:
                continue
            seen.add(key)
            found.append((child.name, str(exe).replace("\\", "/")))
    return found


def detect_roots() -> list[Path]:
    """Roots usable as folder-picker starting points (drive roots / filesystem root)."""
    roots: list[Path] = []
    if os.name == "nt":
        for letter in "ABCDEFGHIJKLMNOPQRSTUVWXYZ":
            root = Path(f"{letter}:\\")
            try:
                if root.exists():
                    roots.append(root)
            except OSError:
                continue
    else:
        roots.append(Path("/"))
    return roots


def with_current(options: list[tuple[str, str]], current: str) -> list[tuple[str, str]]:
    values = {value for _label, value in options}
    if current and current not in values:
        return [(f"{current}  (当前)", current), *options]
    return options


def split_args(raw: str) -> list[str]:
    parts = [item for item in raw.replace(",", " ").split() if item]
    return parts


class FieldRow(Horizontal):
    def __init__(self, label: str, widget, *, hint: str = "", extra=None) -> None:
        super().__init__(classes="row")
        self._label = label
        self._widget = widget
        self._hint = hint
        self._extra = extra

    def compose(self) -> ComposeResult:
        yield Label(self._label, classes="field")
        yield self._widget
        if self._hint:
            yield Label(self._hint, classes="hint")
        if self._extra is not None:
            yield self._extra


class FolderOnlyTree(DirectoryTree):
    """A DirectoryTree that only lists directories (for picking a folder)."""

    def filter_paths(self, paths):
        return [path for path in paths if path.is_dir()]


class FolderPicker(ModalScreen[Path | None]):
    """Modal dialog to pick a folder, used to choose the scan (input) directory."""

    CSS = """
    FolderPicker {
        align: center middle;
    }
    #picker {
        width: 92%;
        height: 90%;
        max-width: 130;
        border: round $accent;
        background: $surface;
        padding: 1 2;
    }
    #picker-title {
        height: 1;
        text-style: bold;
        color: $accent;
    }
    #picker-root-row {
        height: auto;
        margin: 1 0;
        align: left middle;
    }
    #picker-root-label {
        width: 10;
        color: $text-muted;
    }
    #picker-root {
        width: 1fr;
    }
    #tree {
        height: 1fr;
        border: round $primary;
    }
    #picker-status {
        height: 1;
        color: $text-muted;
        margin: 1 0;
    }
    #picker-actions {
        height: auto;
        align: right middle;
    }
    #picker-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS = [Binding("escape", "cancel", "取消", show=False)]

    def __init__(self, start: Path) -> None:
        super().__init__()
        self._start = start if start.is_dir() else repo_path()
        self.current: Path = self._start

    def _root_options(self) -> list[tuple[str, str]]:
        seen: set[str] = set()
        options: list[tuple[str, str]] = []
        for candidate in (self._start, repo_path(), Path.home(), *detect_roots()):
            if not candidate.is_dir():
                continue
            text = str(candidate)
            key = text.replace("\\", "/").lower()
            if key in seen:
                continue
            seen.add(key)
            options.append((text, text))
        return options

    def compose(self) -> ComposeResult:
        with Vertical(id="picker"):
            yield Static("选择扫描文件夹", id="picker-title")
            with Horizontal(id="picker-root-row"):
                yield Label("起始位置", id="picker-root-label")
                yield Select(self._root_options(), value=str(self._start), id="picker-root", allow_blank=False)
            yield FolderOnlyTree(self._start, id="tree")
            yield Static("", id="picker-status")
            with Horizontal(id="picker-actions"):
                yield Button("上级目录", id="up", variant="default")
                yield Button("取消", id="cancel", variant="default")
                yield Button("选择当前文件夹", id="confirm", variant="primary")

    def on_mount(self) -> None:
        tree = self.query_one("#tree", DirectoryTree)
        tree.root.expand()
        tree.focus()
        self._update_status(self.current)

    def on_directory_tree_directory_selected(self, event: DirectoryTree.DirectorySelected) -> None:
        self.current = event.path
        if not event.node.is_expanded:
            event.node.expand()
        self._update_status(event.path)

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "picker-root":
            return
        value = str(event.value or "")
        if value:
            self._reroot(Path(value))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "confirm":
            self.dismiss(self.current)
        elif event.button.id == "cancel":
            self.dismiss(None)
        elif event.button.id == "up":
            self._go_up()

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _reroot(self, path: Path) -> None:
        if not path.is_dir():
            return
        self.current = path
        self.query_one("#tree", DirectoryTree).path = path
        self._update_status(path)

    def _go_up(self) -> None:
        parent = self.current.parent if self.current else self._start
        if parent == self.current:
            return
        self._reroot(parent)

    def _update_status(self, path: Path) -> None:
        self.query_one("#picker-status", Static).update(f"当前: {path}")


class ConfigTui(App[None]):
    TITLE = "ConversionCulib 配置"
    CSS = """
    Screen {
        layout: vertical;
    }
    #form {
        padding: 1 2;
        height: 1fr;
    }
    .section {
        color: $accent;
        text-style: bold;
        margin: 1 0 0 0;
        height: 1;
    }
    .row {
        height: auto;
        margin: 0 0 1 0;
        align: left middle;
    }
    Label.field {
        width: 16;
        color: $text-muted;
        padding: 0 1 0 0;
    }
    Label.hint {
        width: auto;
        color: $text-muted;
        padding: 0 0 0 1;
    }
    Input, Select {
        width: 1fr;
    }
    #detected {
        height: auto;
        margin: 0 0 1 16;
    }
    #actions {
        height: auto;
        dock: bottom;
        padding: 0 2 1 2;
        align: left middle;
    }
    #status {
        height: 1;
        color: $text-muted;
        padding: 0 2;
    }
    Button {
        margin-right: 1;
    }
    """
    BINDINGS = [
        Binding("ctrl+s", "save", "保存", show=True),
        Binding("ctrl+r", "reload", "重载", show=True),
        Binding("escape", "quit", "退出", show=True),
    ]

    def __init__(self, config_path: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self._original: dict[str, Any] = {}
        self._editors = find_unity_editors()

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with VerticalScroll(id="form"):
            yield Static("路径", classes="section")
            yield FieldRow("Unity Editor", Input(placeholder="Unity.exe 绝对路径", id="unityEditor"))
            editor_opts = [("不自动填入", "")]
            editor_opts.extend((f"{ver}  —  {path}", path) for ver, path in self._editors)
            yield FieldRow(
                "检测到",
                Select(editor_opts, value="", id="unityDetected", allow_blank=False, prompt="选择已安装的 Editor"),
            )
            yield FieldRow("Unity 版本", Input(placeholder="2022.3.62f3c1", id="unityVersion"))
            yield FieldRow("AssetRipper", Input(placeholder="./tools/AssetRipper/AssetRipper.GUI.Free.exe", id="assetRipper"))
            yield FieldRow("Ripper 参数", Input(placeholder="额外参数，空格分隔", id="assetRipperArgs"))
            yield FieldRow("Python", Input(placeholder="python", id="python"))
            yield FieldRow("input", Input(id="inputDir"), extra=Button("浏览…", id="browseInput"))
            yield FieldRow("work", Input(id="workDir"))
            yield FieldRow("output", Input(id="outputDir"))
            yield FieldRow("template", Input(id="unityTemplateDir"))

            yield Static("打包", classes="section")
            yield FieldRow("贴图格式", Select(TEXTURE_OPTS, id="androidTexture", allow_blank=False))
            yield FieldRow("ASTC 块", Select(ASTC_OPTS, id="astcBlockSize", allow_blank=False))
            yield FieldRow("贴图上限", Select(MAX_SIZE_OPTS, id="maxTextureSize", allow_blank=False))
            yield FieldRow("AB 压缩", Select(COMPRESSION_OPTS, id="compression", allow_blank=False))
            yield FieldRow("工程模式", Select(MODE_OPTS, id="unityProjectMode", allow_blank=False))
        yield Static("", id="status")
        with Horizontal(id="actions"):
            yield Button("保存  Ctrl+S", id="save", variant="primary")
            yield Button("重新加载", id="reload")
            yield Button("退出", id="quit")
        yield Footer()

    def on_mount(self) -> None:
        self.reload_from_disk()

    def reload_from_disk(self) -> None:
        data = load_json(self.config_path)
        self._original = dict(data)
        self._fill(data)
        exists = "已加载" if self.config_path.is_file() else "文件不存在，保存时会新建"
        self._set_status(f"{exists}  {self.config_path}")

    def _fill(self, data: dict[str, Any]) -> None:
        def text(key: str, default: str = "") -> str:
            value = data.get(key, default)
            if value is None:
                return default
            return str(value)

        self.query_one("#unityEditor", Input).value = text("unityEditor")
        self.query_one("#unityVersion", Input).value = text("unityVersion")
        self.query_one("#assetRipper", Input).value = text("assetRipper", "./tools/AssetRipper/AssetRipper.GUI.Free.exe")
        args = data.get("assetRipperArgs") or []
        if isinstance(args, list):
            self.query_one("#assetRipperArgs", Input).value = " ".join(str(item) for item in args)
        else:
            self.query_one("#assetRipperArgs", Input).value = str(args)
        self.query_one("#python", Input).value = text("python", "python")
        self.query_one("#inputDir", Input).value = text("inputDir", "./input")
        self.query_one("#workDir", Input).value = text("workDir", "./work")
        self.query_one("#outputDir", Input).value = text("outputDir", "./output/android")
        self.query_one("#unityTemplateDir", Input).value = text("unityTemplateDir", "./unity-template")

        self._set_select("#androidTexture", TEXTURE_OPTS, text("androidTexture", "ASTC"))
        astc = text("astcBlockSize", "8x8")
        astc_opts = with_current(ASTC_OPTS, astc)
        astc_select = self.query_one("#astcBlockSize", Select)
        astc_select.set_options(astc_opts)
        self._set_select("#astcBlockSize", astc_opts, astc if astc else "8x8")

        max_size = text("maxTextureSize", "0")
        if max_size == "":
            max_size = "0"
        max_opts = with_current(MAX_SIZE_OPTS, max_size)
        max_select = self.query_one("#maxTextureSize", Select)
        max_select.set_options(max_opts)
        self._set_select("#maxTextureSize", max_opts, max_size)

        self._set_select("#compression", COMPRESSION_OPTS, text("compression", "lzma"))
        self._set_select("#unityProjectMode", MODE_OPTS, text("unityProjectMode", "ripped"))

        detected = self.query_one("#unityDetected", Select)
        current_editor = text("unityEditor").replace("\\", "/")
        match = next((path for _ver, path in self._editors if path.replace("\\", "/") == current_editor), "")
        try:
            detected.value = match
        except Exception:
            detected.value = ""

    def _set_select(self, selector: str, options: list[tuple[str, str]], value: str) -> None:
        widget = self.query_one(selector, Select)
        values = {item[1] for item in options}
        if value not in values:
            value = options[0][1] if options else Select.NULL
        try:
            widget.value = value
        except Exception:
            pass

    def collect(self) -> dict[str, Any]:
        merged = dict(self._original)
        merged["unityEditor"] = self.query_one("#unityEditor", Input).value.strip()
        merged["unityVersion"] = self.query_one("#unityVersion", Input).value.strip()
        merged["assetRipper"] = self.query_one("#assetRipper", Input).value.strip()
        merged["assetRipperArgs"] = split_args(self.query_one("#assetRipperArgs", Input).value)
        merged["python"] = self.query_one("#python", Input).value.strip() or "python"
        merged["inputDir"] = self.query_one("#inputDir", Input).value.strip() or "./input"
        merged["workDir"] = self.query_one("#workDir", Input).value.strip() or "./work"
        merged["outputDir"] = self.query_one("#outputDir", Input).value.strip() or "./output/android"
        merged["unityTemplateDir"] = self.query_one("#unityTemplateDir", Input).value.strip() or "./unity-template"
        merged["androidTexture"] = str(self.query_one("#androidTexture", Select).value or "ASTC")
        merged["astcBlockSize"] = str(self.query_one("#astcBlockSize", Select).value or "8x8")
        try:
            merged["maxTextureSize"] = int(str(self.query_one("#maxTextureSize", Select).value or "0"))
        except ValueError:
            merged["maxTextureSize"] = 0
        merged["compression"] = str(self.query_one("#compression", Select).value or "lzma")
        merged["unityProjectMode"] = str(self.query_one("#unityProjectMode", Select).value or "ripped")
        return merged

    def _resolve(self, raw: str) -> Path:
        path = Path(raw)
        if not path.is_absolute():
            path = repo_path() / path
        return path

    def _warnings(self, data: dict[str, Any]) -> list[str]:
        notes: list[str] = []
        editor = self._resolve(str(data.get("unityEditor") or ""))
        if not str(data.get("unityEditor") or "").strip():
            notes.append("未填写 Unity Editor")
        elif not editor.is_file():
            notes.append(f"Unity.exe 不存在: {editor}")
        ripper = self._resolve(str(data.get("assetRipper") or ""))
        if not ripper.is_file():
            notes.append(f"AssetRipper 不存在: {ripper}")
        astc = str(data.get("astcBlockSize") or "")
        if data.get("androidTexture") == "ASTC" and astc not in {v for _l, v in ASTC_OPTS}:
            notes.append(f"ASTC 块 {astc} 无效，打包时会按 8x8")
        return notes

    def _set_status(self, message: str) -> None:
        self.query_one("#status", Static).update(message)

    def action_save(self) -> None:
        data = self.collect()
        self.config_path.parent.mkdir(parents=True, exist_ok=True)
        save_json(self.config_path, data)
        self._original = dict(data)
        notes = self._warnings(data)
        extra = ("  |  " + "；".join(notes)) if notes else ""
        self._set_status(f"已保存 {self.config_path}{extra}")
        self.notify("已保存 config.json" + (("：" + notes[0]) if notes else ""), severity="warning" if notes else "information")

    def action_reload(self) -> None:
        self.reload_from_disk()
        self.notify("已从磁盘重新加载")

    def action_browse_input(self) -> None:
        raw = self.query_one("#inputDir", Input).value.strip()
        start = self._resolve(raw) if raw else repo_path()
        if not start.is_dir():
            start = repo_path()
        self.push_screen(FolderPicker(start), self._on_folder_picked)

    def _on_folder_picked(self, picked: Path | None) -> None:
        if picked is None:
            return
        self.query_one("#inputDir", Input).value = str(picked).replace("\\", "/")
        self.notify(f"已选择扫描文件夹: {picked}")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_save()
        elif event.button.id == "reload":
            self.action_reload()
        elif event.button.id == "browseInput":
            self.action_browse_input()
        elif event.button.id == "quit":
            self.exit()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id != "unityDetected":
            return
        path = str(event.value or "")
        if not path:
            return
        self.query_one("#unityEditor", Input).value = path.replace("\\", "/")
        version = Path(path).parent.parent.name
        if version:
            self.query_one("#unityVersion", Input).value = version


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="ConversionCulib config.json TUI")
    parser.add_argument("--config", default=str(REPO / "config.json"), help="config.json 路径")
    args = parser.parse_args(argv)
    config_path = Path(args.config).expanduser()
    if not config_path.is_absolute():
        config_path = (Path.cwd() / config_path).resolve()
    app = ConfigTui(config_path)
    app.run()
    return 0


if __name__ == "__main__":
    sys.exit(main())
