#!/usr/bin/env python3
"""Replace Presto 'Powered by' branding in compiled statentry GWT *.cache.js files."""
from __future__ import annotations

import sys
from pathlib import Path

ROOTDir = Path(__file__).resolve().parents[1]
STATENTRY_DIR = ROOTDir / "app" / "static" / "gamedaystats" / "statsentry" / "statentry"

# File embeds escaped slashes as two backslashes before '/' in JS string literals.
REPLACEMENTS: tuple[tuple[str, str], ...] = (
    (
        "Powered by <\\\\/span> PrestoSports <\\\\/a>",
        "Powered by <\\\\/span> Gameday Stats <\\\\/a>",
    ),
    ("alt='Powered by PrestoSports'", "alt='Powered by Gameday Stats'"),
)


def _patch_file(path: Path) -> bool:
    text = path.read_text(encoding="utf-8", errors="replace")
    orig = text
    for old, new in REPLACEMENTS:
        if old in text:
            text = text.replace(old, new)
    if text == orig:
        return False
    path.write_text(text, encoding="utf-8")
    return True


def main() -> int:
    if len(sys.argv) > 1:
        paths = [Path(a).expanduser().resolve() for a in sys.argv[1:]]
        for p in paths:
            if not p.is_file():
                print(f"Missing {p}", flush=True)
                return 1
    else:
        paths = sorted(STATENTRY_DIR.glob("*.cache.js"))
        if not paths:
            print(f"No *.cache.js under {STATENTRY_DIR}", flush=True)
            return 1

    changed_any = False
    for path in paths:
        if _patch_file(path):
            print(f"Branding applied: {path}", flush=True)
            changed_any = True
        else:
            print(f"(no Presto footer to replace) {path}", flush=True)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
