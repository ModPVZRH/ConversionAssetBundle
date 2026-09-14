#!/usr/bin/env python3
from __future__ import annotations

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path


def _ensure_utf8_stdio() -> None:
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            try:
                reconfigure(encoding="utf-8", errors="replace")
            except Exception:
                pass


def pick_free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def make_opener() -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(urllib.request.ProxyHandler({}))


def wait_http(opener: urllib.request.OpenerDirector, port: int, proc: subprocess.Popen, timeout: float) -> None:
    url = f"http://127.0.0.1:{port}/"
    deadline = time.time() + timeout
    last_error = ""
    while time.time() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"AssetRipper exited before the web API was ready (code {proc.returncode})")
        try:
            with opener.open(url, timeout=2) as response:
                if 200 <= int(response.status) < 400:
                    return
        except Exception as exc:
            last_error = str(exc)
        time.sleep(0.25)
    raise TimeoutError(f"AssetRipper web API did not start on port {port}: {last_error}")


def post_form(
    opener: urllib.request.OpenerDirector,
    port: int,
    path: str,
    fields: dict[str, str],
    timeout: float | None,
) -> None:
    url = f"http://127.0.0.1:{port}{path}"
    body = urllib.parse.urlencode(fields).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    print(f"POST {path} Path={fields.get('Path', '')}", flush=True)
    try:
        with opener.open(request, timeout=timeout) as response:
            status = int(response.status)
            if status >= 400:
                raise RuntimeError(f"{path} failed HTTP {status}")
    except urllib.error.HTTPError as exc:
        if exc.code in (301, 302, 303, 307, 308):
            return
        raise RuntimeError(f"{path} failed HTTP {exc.code}: {exc.reason}") from exc


def pump_output(stream, prefix: str) -> None:
    try:
        for line in iter(stream.readline, ""):
            if not line:
                break
            sys.stdout.write(f"{prefix}{line}")
            sys.stdout.flush()
    except Exception:
        pass


def find_exported_assets(output: Path) -> Path | None:
    for candidate in (output / "ExportedProject" / "Assets", output / "Assets"):
        if candidate.is_dir():
            return candidate
    return None


def terminate(proc: subprocess.Popen) -> None:
    if proc.poll() is not None:
        return
    try:
        proc.terminate()
        proc.wait(timeout=8)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def main(argv: list[str] | None = None) -> int:
    _ensure_utf8_stdio()
    parser = argparse.ArgumentParser(
        description="Drive AssetRipper.GUI.Free via its headless web API (LoadFolder + Export/UnityProject)."
    )
    parser.add_argument("--ripper", required=True, help="Path to AssetRipper.GUI.Free.exe")
    parser.add_argument("--input", required=True, help="Folder of PC AssetBundles")
    parser.add_argument("--output", required=True, help="Export root (expects ExportedProject/Assets)")
    parser.add_argument("--port", type=int, default=0, help="Web API port; 0 = pick a free port")
    parser.add_argument("--ready-timeout", type=float, default=60, help="Seconds to wait for the web API")
    parser.add_argument("--http-timeout", type=float, default=0, help="Seconds per HTTP call; 0 = no limit")
    parser.add_argument("ripper_args", nargs="*", help="Extra args forwarded to AssetRipper")
    args = parser.parse_args(argv)

    ripper = Path(args.ripper).expanduser()
    input_dir = Path(args.input).expanduser()
    output_dir = Path(args.output).expanduser()
    try:
        ripper = ripper.resolve()
        input_dir = input_dir.resolve()
        output_dir = output_dir.resolve()
    except OSError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not ripper.is_file():
        print(f"error: AssetRipper not found: {ripper}", file=sys.stderr)
        return 2
    if not input_dir.is_dir():
        print(f"error: input directory not found: {input_dir}", file=sys.stderr)
        return 2

    if output_dir.exists():
        shutil.rmtree(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    port = args.port if args.port > 0 else pick_free_port()
    log_path = output_dir.parent / "assetripper.log"
    http_timeout = None if args.http_timeout <= 0 else args.http_timeout

    launch_args = [str(ripper), "--headless", "--port", str(port), "--log-path", str(log_path)]
    extra = [str(item) for item in args.ripper_args if str(item) not in {"--headless", "true", "false", "--"}]
    launch_args.extend(extra)

    print(f"Starting AssetRipper on http://127.0.0.1:{port}/", flush=True)
    creationflags = 0
    if os.name == "nt":
        creationflags = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]

    proc = subprocess.Popen(
        launch_args,
        cwd=str(ripper.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        creationflags=creationflags,
    )
    assert proc.stdout is not None
    reader = threading.Thread(target=pump_output, args=(proc.stdout, ""), daemon=True)
    reader.start()

    opener = make_opener()
    try:
        wait_http(opener, port, proc, args.ready_timeout)
        post_form(opener, port, "/LoadFolder", {"Path": str(input_dir)}, http_timeout)
        post_form(opener, port, "/Export/UnityProject", {"Path": str(output_dir)}, http_timeout)
    finally:
        terminate(proc)

    assets = find_exported_assets(output_dir)
    if assets is None:
        print(
            f"error: ripped export has no Assets folder under {output_dir}",
            file=sys.stderr,
        )
        return 2
    print(f"Exported Unity project assets: {assets}", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
