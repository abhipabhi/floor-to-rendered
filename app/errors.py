"""One error shape for the whole API."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class AppError(Exception):
    """An error worth showing the user, with a code the UI can branch on."""

    def __init__(self, code: str, message: str, status: int = 400, **details):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status
        self.details = details


def not_found(what: str) -> AppError:
    return AppError("not_found", f"{what} not found", 404)


async def handle(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status,
        content={
            "error": {"code": exc.code, "message": exc.message, "details": exc.details}
        },
    )
