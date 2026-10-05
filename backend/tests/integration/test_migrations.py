from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

pytestmark = pytest.mark.integration
ROOT = Path(__file__).resolve().parents[2]


def _alembic(*args: str, url: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "alembic", *args],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": url},
        check=True,
        capture_output=True,
        text=True,
    )


def test_migrations_up_down_up(settings, migrated_db) -> None:  # type: ignore[no-untyped-def]
    _alembic("downgrade", "base", url=settings.database_url)
    _alembic("upgrade", "head", url=settings.database_url)
    out = _alembic("current", url=settings.database_url)
    assert "(head)" in out.stdout
