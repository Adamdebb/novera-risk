"""Write the API schema to docs/api/openapi.json.

A client project generates its types from this file without running the server. The test
suite fails when the committed file is stale, so run this after changing the API:

    uv run python scripts/export_openapi.py          # rewrite the file
    uv run python scripts/export_openapi.py --check  # exit 1 if it is stale
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "docs" / "api" / "openapi.json"


def render() -> str:
    sys.path.insert(0, str(ROOT / "src"))
    from novera.api.app import app

    return json.dumps(app.openapi(), indent=2, sort_keys=True) + "\n"


def is_current() -> bool:
    return TARGET.exists() and TARGET.read_text(encoding="utf-8") == render()


def main(argv: list[str]) -> int:
    if "--check" in argv:
        if is_current():
            print(f"{TARGET.relative_to(ROOT)} is current")
            return 0
        print(f"{TARGET.relative_to(ROOT)} is stale; run scripts/export_openapi.py")
        return 1
    TARGET.parent.mkdir(parents=True, exist_ok=True)
    TARGET.write_text(render(), encoding="utf-8")
    print(f"wrote {TARGET.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
