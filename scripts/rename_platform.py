"""Rename the platform package and brand in one pass.

Usage:
    python scripts/rename_platform.py NewName [--dry-run]

Rules that keep this safe (see ADR 0005):
- The package directory ``src/<old>`` is moved to ``src/<new_lower>``.
- Occurrences of the old name are replaced in three casings: ``novera`` (package, env
  prefix, module paths), ``Novera`` (brand), ``NOVERA`` (env vars).
- Only text files tracked in the repo are touched; ``.venv``, ``data`` and ``.git`` are
  skipped. Binary files are skipped.
- Run the tests afterwards. Then update ``settings.platform_name`` default if the brand
  differs from the package casing.
"""
from __future__ import annotations

import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKIP_DIRS = {
    ".git", ".venv", "data", "runs", "__pycache__", ".pytest_cache", ".ruff_cache", ".mypy_cache",
}
TEXT_SUFFIXES = {
    ".py", ".md", ".toml", ".yml", ".yaml", ".txt", ".cfg", ".ini", ".json", ".example", "",
}


def current_package_name() -> str:
    pkgs = [p for p in (ROOT / "src").iterdir() if p.is_dir() and (p / "__init__.py").exists()]
    if len(pkgs) != 1:
        raise SystemExit(f"expected exactly one package under src/, found {[p.name for p in pkgs]}")
    return pkgs[0].name


def iter_text_files() -> list[Path]:
    out: list[Path] = []
    for p in ROOT.rglob("*"):
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        if p.is_file() and p.suffix in TEXT_SUFFIXES:
            out.append(p)
    return out


def main() -> None:
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    dry = "--dry-run" in sys.argv
    if len(args) != 1 or not re.fullmatch(r"[A-Za-z][A-Za-z0-9]*", args[0]):
        raise SystemExit(__doc__)
    new = args[0]
    old = current_package_name()
    pairs = [(old, new.lower()), (old.capitalize(), new), (old.upper(), new.upper())]
    if old == new.lower():
        raise SystemExit("new name equals the current package name")

    changed = 0
    for path in iter_text_files():
        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        new_text = text
        for a, b in pairs:
            new_text = new_text.replace(a, b)
        if new_text != text:
            changed += 1
            print(f"rewrite {path.relative_to(ROOT)}")
            if not dry:
                path.write_text(new_text, encoding="utf-8")

    src_old, src_new = ROOT / "src" / old, ROOT / "src" / new.lower()
    print(f"move {src_old.relative_to(ROOT)} -> {src_new.relative_to(ROOT)}")
    if not dry:
        shutil.move(str(src_old), str(src_new))
    verb = "would rewrite" if dry else "rewrote"
    print(f"{verb} {changed} files. Now run: uv sync && uv run pytest")


if __name__ == "__main__":
    main()
