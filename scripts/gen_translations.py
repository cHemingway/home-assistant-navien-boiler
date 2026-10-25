"""Write translations/en.json from strings.json, resolving Home Assistant's `[%key:...%]` references.

Core integrations get this done by Home Assistant's build scripts; custom integrations load en.json as is.

    python scripts/gen_translations.py          # write
    python scripts/gen_translations.py --check  # fail if en.json is out of date
"""

from __future__ import annotations

import json
from pathlib import Path
import re
import sys

import homeassistant

ROOT = Path(__file__).resolve().parent.parent
INTEGRATION = ROOT / "custom_components" / "navien_smartplus"


def build() -> str:
    common = json.loads((Path(homeassistant.__file__).parent / "strings.json").read_text())

    def resolve(match: re.Match[str]) -> str:
        node = common
        for part in match.group(1).split("::"):
            node = node[part]
        return node

    return re.sub(r"\[%key:([^%]+)%\]", resolve, (INTEGRATION / "strings.json").read_text())


def main() -> None:
    target = INTEGRATION / "translations" / "en.json"
    content = build()
    if "--check" in sys.argv:
        if not target.exists() or target.read_text() != content:
            sys.exit("translations/en.json is out of date; run scripts/gen_translations.py")
        return
    target.parent.mkdir(exist_ok=True)
    target.write_text(content)


if __name__ == "__main__":
    main()
