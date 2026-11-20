"""The provider catalogue: the relay's copy of the runtime's `providers.json`.

The facts come from the catalogue `providers.json` next to this file, a copy of the
runtime's `nanomuse/llm/providers.json`, written by `node scripts/providers-json.mjs`
and checked by CI, never edited here. The catalogue lists local endpoints only; the
own-key guidance that used to order cloud providers by region went away with the
allowance gate, which no longer refuses anyone.
"""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Any

CATALOGUE = Path(__file__).with_name("providers.json")

# What each capability lets the app do, in the order the cards list them.
CAPABILITIES = ("chat", "vision", "image", "video")


@lru_cache(maxsize=1)
def catalogue() -> dict[str, Any]:
    """The catalogue as shipped (`{version, updated, providers[]}`); read once."""
    return json.loads(CATALOGUE.read_text(encoding="utf-8"))


def provider(pid: str) -> dict[str, Any] | None:
    for p in catalogue()["providers"]:
        if p["id"] == pid:
            return p
    return None
