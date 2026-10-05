"""RFC 7807 problem details with stable codes."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.core.errors import AppError
from app.core.logging import get_logger

log = get_logger("api.errors")
MEDIA = "application/problem+json"


class FieldError(BaseModel):
    field: str
    message: str


class Problem(BaseModel):
    """RFC 7807 problem details. code is stable and safe to branch on."""

    type: str
    title: str
    status: int
    code: str
    detail: str
    instance: str
    errors: list[FieldError] | None = None


def _resp(description: str) -> dict[str, Any]:
    return {"model": Problem, "description": description, "content": {MEDIA: {}}}


PROBLEM_RESPONSES: dict[int | str, dict[str, Any]] = {
    401: _resp("Missing or invalid credentials"),
    403: _resp("Role not allowed"),
    404: _resp("Not found"),
    409: _resp("Conflict with the current state"),
    422: _resp("Validation failed"),
    429: _resp("Rate limited"),
}


def problem(status: int, code: str, detail: str, request: Request, **extra: Any) -> JSONResponse:
    body: dict[str, Any] = {
        "type": f"https://dispatch.blurabbit.internal/problems/{code}",
        "title": code.replace("_", " "),
        "status": status,
        "code": code,
        "detail": detail,
        "instance": str(request.url.path),
    }
    body.update(extra)
    return JSONResponse(body, status_code=status, media_type=MEDIA)


def install(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError) -> JSONResponse:
        return problem(exc.status, exc.code, exc.detail, request, **exc.extra)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]}
            for e in exc.errors()
        ]
        return problem(422, "validation_error", "request validation failed", request, errors=errors)

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = {404: "not_found", 405: "method_not_allowed", 413: "payload_too_large"}.get(
            exc.status_code, "http_error"
        )
        return problem(exc.status_code, code, str(exc.detail), request)

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled_error", path=str(request.url.path), error=str(exc))
        return problem(500, "internal_error", "internal server error", request)
