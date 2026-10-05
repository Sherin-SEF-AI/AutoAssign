"""Limit and cursor pagination."""

from __future__ import annotations

import base64
import json
from collections.abc import Sequence
from typing import Annotated

from fastapi import Query
from pydantic import BaseModel

from app.core.errors import Unprocessable


class Page[T](BaseModel):
    items: list[T]
    next_cursor: str | None = None
    total: int | None = None


class PageParams(BaseModel):
    limit: int
    offset: int


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"o": offset}).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    if not cursor:
        return 0
    try:
        data = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        offset = int(data["o"])
    except (ValueError, KeyError, TypeError) as exc:
        raise Unprocessable("invalid cursor", code="invalid_cursor") from exc
    if offset < 0:
        raise Unprocessable("invalid cursor", code="invalid_cursor")
    return offset


def page_params(
    limit: Annotated[int, Query(ge=1, le=1000)] = 200, cursor: Annotated[str | None, Query()] = None
) -> PageParams:
    return PageParams(limit=limit, offset=decode_cursor(cursor))


def paginate[T](items: Sequence[T], params: PageParams) -> Page[T]:
    window = list(items[params.offset : params.offset + params.limit])
    nxt = params.offset + params.limit
    return Page[T](
        items=window, next_cursor=encode_cursor(nxt) if nxt < len(items) else None, total=len(items)
    )
