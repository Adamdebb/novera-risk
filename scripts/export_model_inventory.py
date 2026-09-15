"""Write the model inventory table into docs/methodology/MV-001-model-inventory.md.

The inventory is reference data read from code (``novera.pricing.catalogue``); the record
keeps a rendered copy between two markers so a reader of the docs sees the same table as the
API. The test suite fails when the committed table is stale, so run this after changing the
catalogue:

    uv run python scripts/export_model_inventory.py          # rewrite the table
    uv run python scripts/export_model_inventory.py --check  # exit 1 if it is stale
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "methodology" / "MV-001-model-inventory.md"
START, END = "<!-- inventory:start -->", "<!-- inventory:end -->"


def render() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from novera.pricing.catalogue import render_model_inventory

    return render_model_inventory()


def _split(text: str) -> tuple[str, str, str]:
    a, b = text.index(START) + len(START), text.index(END)
    return text[:a], text[a:b], text[b:]


def updated() -> str:
    head, _, tail = _split(TARGET.read_text(encoding="utf-8"))
    return f"{head}\n{render()}{tail}"


def is_current() -> bool:
    return TARGET.exists() and TARGET.read_text(encoding="utf-8") == updated()


def main(argv: list[str]) -> int:
    if "--check" in argv:
        if is_current():
            print(f"{TARGET.relative_to(ROOT)} is current")
            return 0
        print(f"{TARGET.relative_to(ROOT)} is stale; run scripts/export_model_inventory.py")
        return 1
    TARGET.write_text(updated(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
