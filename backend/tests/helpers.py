"""Shared test helpers."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def fixture(*parts: str) -> Any:
    return json.loads(FIXTURES.joinpath(*parts).read_text())


async def no_sleep(_: float) -> None:
    return None
