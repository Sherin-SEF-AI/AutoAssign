"""Writes the OpenAPI document used to generate the typed web client."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from app.config import Settings
from app.main import create_app

PLACEHOLDER_ENV = {
    "database_url": "postgresql+asyncpg://openapi@localhost/openapi",
    "redis_url": "redis://localhost:6379/0",
    "jwt_secret": "openapi-export-only-not-a-secret-0000",
    "admin_email": "openapi@example.invalid",
    "admin_password": "openapi-export",
}


def main() -> None:
    settings = Settings(**PLACEHOLDER_ENV)  # type: ignore[arg-type]
    doc = create_app(settings).openapi()
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("../web/src/api/openapi.json")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(doc, indent=2, sort_keys=True) + "\n")
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
