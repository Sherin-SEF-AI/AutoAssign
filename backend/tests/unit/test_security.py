from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from app.api.pagination import PageParams, decode_cursor, encode_cursor, paginate
from app.api.security import check_service_key, decode_token, hash_password, issue_token, verify_password
from app.core.errors import Unauthorized, Unprocessable
from app.db.models import AppUser


def test_password_roundtrip() -> None:
    h = hash_password("s3cret-pass")
    assert verify_password("s3cret-pass", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "not-a-hash")


def test_token_roundtrip_and_expiry(settings) -> None:  # type: ignore[no-untyped-def]
    user = AppUser(email="ops@x.y", password_hash="h", role="ops")
    token, _ = issue_token(settings, user)
    principal = decode_token(settings, token)
    assert principal.role == "ops" and principal.subject == "ops@x.y" and not principal.is_admin
    old, _ = issue_token(settings, user, now=datetime.now(UTC) - timedelta(hours=settings.jwt_ttl_hours + 1))
    with pytest.raises(Unauthorized) as exc:
        decode_token(settings, old)
    assert exc.value.code == "token_expired"
    with pytest.raises(Unauthorized):
        decode_token(settings, token + "x")


def test_service_key(settings) -> None:  # type: ignore[no-untyped-def]
    assert check_service_key(settings, settings.service_api_keys[0]).kind == "service"
    with pytest.raises(Unauthorized):
        check_service_key(settings, "nope")
    with pytest.raises(Unauthorized):
        check_service_key(settings, None)


def test_cursor_pagination() -> None:
    items = list(range(25))
    page = paginate(items, PageParams(limit=10, offset=0))
    assert page.items == list(range(10)) and page.total == 25
    nxt = decode_cursor(page.next_cursor)
    assert nxt == 10
    last = paginate(items, PageParams(limit=10, offset=20))
    assert last.items == [20, 21, 22, 23, 24] and last.next_cursor is None
    assert decode_cursor(encode_cursor(7)) == 7
    with pytest.raises(Unprocessable):
        decode_cursor("!!!")
