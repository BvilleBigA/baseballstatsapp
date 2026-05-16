#!/usr/bin/env python3
"""
Patch a packaged PrestoSync / Gameday LiveStats desktop app to use your Gameday Stats API.

Windows (typical layouts):
  Current-user installer (Electron / Squirrel-style):
    %LOCALAPPDATA%\\Programs\\prestosports-prestosync\\resources\\app.asar
    python scripts/patch_gameday_sync.py --default-windows-install
  Or explicitly:
    python scripts/patch_gameday_sync.py --app-dir "%LOCALAPPDATA%\\Programs\\prestosports-prestosync"

macOS (.app bundle):
  python scripts/patch_gameday_sync.py /path/to/PrestoSync.app

Requires: Node.js on PATH + npx (uses @electron/asar to extract/repack app.asar).

After patching, the Electron app may fail integrity checks if the build used ASAR
integrity fuses. If the app will not start, use an unsigned build from Presto or
contact your vendor; on macOS the script strips AsarIntegrity keys from Info.plist.

Build a Windows .exe (one-file console):
  pip install pyinstaller
  pyinstaller --onefile --name GamedaySyncPatch scripts/patch_gameday_sync.py
  (artifact: dist/GamedaySyncPatch.exe)
"""

from __future__ import annotations

import argparse
import os
import plistlib
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

OLD_DEFAULT = "https://gameday-api.prestosports.com/api"
NEW_DEFAULT = "https://stats.bvillebiga.com/api"

TEXT_SUFFIXES = {".js", ".json", ".html", ".htm", ".map", ".css"}


def _npx_argv(*parts: str) -> list[str]:
    """Build argv to run npx. On Windows, npx is a .cmd shim and must run via cmd /c."""
    if sys.platform == "win32":
        return ["cmd", "/c", "npx", *parts]
    return ["npx", *parts]


def _ensure_npx_works() -> None:
    """Fail fast with a clear message if Node/npx is missing."""
    cmd = _npx_argv("--version")
    r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if r.returncode != 0:
        out = (r.stderr or r.stdout or "").strip()
        print(
            "Node.js does not appear to be installed or npx is not on PATH.\n"
            "Install from https://nodejs.org/ then open a new terminal and retry.\n"
            f"Tried: {' '.join(cmd)!r} (exit {r.returncode})\n"
            f"{out}",
            file=sys.stderr,
        )
        raise SystemExit(1)


def _run(cmd: list[str], **kw) -> None:
    print("+", " ".join(cmd))
    subprocess.run(cmd, check=True, **kw)


def _replace_in_tree(root: Path, old: str, new: str) -> int:
    count = 0
    for path in root.rglob("*"):
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if old not in text:
            continue
        path.write_text(text.replace(old, new), encoding="utf-8")
        count += 1
    return count


def _find_asar_mac(app: Path) -> Path | None:
    p = app / "Contents" / "Resources" / "app.asar"
    return p if p.is_file() else None


def _find_asar_windows(app_dir: Path) -> Path | None:
    candidates = [
        app_dir / "resources" / "app.asar",
        app_dir / "Resources" / "app.asar",
        app_dir / "app.asar",
    ]
    for p in candidates:
        if p.is_file():
            return p
    return None


def _find_plist_mac(app: Path) -> Path | None:
    p = app / "Contents" / "Info.plist"
    return p if p.is_file() else None


def _strip_asar_integrity_plist(plist_path: Path) -> None:
    with plist_path.open("rb") as f:
        pl = plistlib.load(f)
    changed = False
    for key in ("AsarIntegrity", "ElectronAsarIntegrity"):
        if key in pl:
            del pl[key]
            changed = True
            print(f"Removed Info.plist key: {key}")
    if not changed:
        print("(No AsarIntegrity / ElectronAsarIntegrity keys in Info.plist — OK)")
        return
    with plist_path.open("wb") as f:
        plistlib.dump(pl, f, fmt=plistlib.FMT_XML)
    print(f"Updated {plist_path}")


def _patch_asar(asar: Path, old_u: str, new_u: str, plist_path: Path | None) -> int:
    if old_u == new_u:
        print("Old and new URL are the same; nothing to do.")
        return 0

    _ensure_npx_works()

    backup = asar.with_suffix(f".asar.bak-{Path(__file__).stem}")
    if not backup.exists():
        shutil.copy2(asar, backup)
        print(f"Backup: {backup}")

    with tempfile.TemporaryDirectory(prefix="gameday-sync-patch-") as tmp:
        tdir = Path(tmp)
        extracted = tdir / "extracted"
        packed = tdir / "app.asar.new"
        _run(
            _npx_argv(
                "--yes",
                "@electron/asar",
                "extract",
                str(asar),
                str(extracted),
            )
        )
        n = _replace_in_tree(extracted, old_u, new_u)
        if n == 0:
            print(
                f"Error: did not find {old_u!r} in any bundled text files. "
                "Wrong app version or URL already patched?",
                file=sys.stderr,
            )
            return 1
        print(f"Replaced URL in {n} file(s).")
        _run(
            _npx_argv(
                "--yes",
                "@electron/asar",
                "pack",
                str(extracted),
                str(packed),
            )
        )
        shutil.copy2(packed, asar)
        print(f"Wrote {asar}")

    if plist_path and plist_path.is_file():
        _strip_asar_integrity_plist(plist_path)
    else:
        print("(No Info.plist — Windows or non-bundle; if the app will not start after patch,")
        print(" the build may enforce ASAR integrity on the .exe; try an unsigned/desktop build.)")

    print()
    print("Done. Restart the desktop app.")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(
        description="Patch PrestoSync / Gameday LiveStats desktop app API URL → Gameday Stats"
    )
    ap.add_argument(
        "app",
        type=Path,
        nargs="?",
        default=None,
        help="Path to PrestoSync.app (macOS bundle)",
    )
    ap.add_argument(
        "--app-dir",
        type=Path,
        default=None,
        help="Install folder containing resources/app.asar (Windows/Linux)",
    )
    ap.add_argument(
        "--asar",
        type=Path,
        default=None,
        help="Direct path to app.asar",
    )
    ap.add_argument(
        "--default-windows-install",
        action="store_true",
        help=(
            r"Use %LOCALAPPDATA%\Programs\prestosports-prestosync "
            "(default folder for the PrestoSync Windows app)"
        ),
    )
    ap.add_argument(
        "--url",
        default=NEW_DEFAULT,
        help=f"New API base URL (default: {NEW_DEFAULT})",
    )
    ap.add_argument(
        "--old-url",
        default=OLD_DEFAULT,
        help=f"String to replace (default: {OLD_DEFAULT})",
    )
    args = ap.parse_args()

    if args.default_windows_install:
        if args.asar or args.app_dir or args.app:
            print(
                "Error: use --default-windows-install alone (no --asar / --app-dir / bundle path)",
                file=sys.stderr,
            )
            return 1
        local = os.environ.get("LOCALAPPDATA", "")
        if not local:
            print("Error: LOCALAPPDATA is not set", file=sys.stderr)
            return 1
        args.app_dir = Path(local) / "Programs" / "prestosports-prestosync"

    old_u: str = args.old_url
    new_u: str = args.url.rstrip("/")

    asar: Path | None = None
    plist_path: Path | None = None

    if args.asar:
        asar = args.asar.expanduser().resolve()
        if not asar.is_file():
            print(f"Error: not a file: {asar}", file=sys.stderr)
            return 1
    elif args.app_dir:
        d = args.app_dir.expanduser().resolve()
        if not d.is_dir():
            print(f"Error: not a directory: {d}", file=sys.stderr)
            return 1
        asar = _find_asar_windows(d)
        if not asar:
            print(
                f"Error: could not find app.asar under {d} "
                "(tried resources/app.asar, Resources/app.asar, app.asar)",
                file=sys.stderr,
            )
            return 1
    elif args.app:
        app = args.app.expanduser().resolve()
        if not app.is_dir():
            print(f"Error: not a directory: {app}", file=sys.stderr)
            return 1
        if app.suffix.lower() == ".app" or (app / "Contents").is_dir():
            asar = _find_asar_mac(app)
            plist_path = _find_plist_mac(app)
            if not asar:
                print(f"Error: missing app.asar in bundle {app}", file=sys.stderr)
                return 1
        else:
            print(
                "Error: provide PrestoSync.app, or use --app-dir / --asar",
                file=sys.stderr,
            )
            return 1
    else:
        ap.print_help()
        print(
            "\nError: specify PrestoSync.app, --app-dir, --asar, or --default-windows-install",
            file=sys.stderr,
        )
        return 1

    return _patch_asar(asar, old_u, new_u, plist_path)


if __name__ == "__main__":
    raise SystemExit(main())
