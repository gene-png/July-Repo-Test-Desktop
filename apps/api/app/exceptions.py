"""Global exception handler.

AI Prompt §4.4 + Master Spec §6.3: NEVER expose a stack trace to a client.
The user-facing 500 response carries only the correlation ID. Internal
diagnostics go to the structured log under the matching correlation ID so an
operator can join them.
"""

from __future__ import annotations

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException

from app.logging import get_logger

logger = get_logger(__name__)


def _correlation_id_from(request: Request) -> str:
    return getattr(request.state, "correlation_id", "unknown")


async def _handle_http_exception(request: Request, exc: HTTPException) -> JSONResponse:
    # Preserve response headers set on the exception (e.g. Retry-After on a 429
    # from the rate limiter). Starlette's HTTPException carries `.headers`.
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": {
                "code": exc.status_code,
                "message": exc.detail,
                "correlation_id": _correlation_id_from(request),
            }
        },
        headers=getattr(exc, "headers", None),
    )


async def _handle_validation_error(request: Request, exc: RequestValidationError) -> JSONResponse:
    return JSONResponse(
        status_code=422,
        content={
            "error": {
                "code": 422,
                "message": "Request validation failed.",
                "details": exc.errors(),
                "correlation_id": _correlation_id_from(request),
            }
        },
    )


async def _handle_llm_timeout(request: Request, exc: Exception) -> JSONResponse:
    # Task S2-A E-1: a whole-call AI deadline expired. Nothing was applied, so
    # this is a clean 504 rather than a 500.
    return JSONResponse(
        status_code=504,
        content={
            "error": {
                "code": 504,
                "message": "the AI call timed out; nothing was changed",
                "correlation_id": _correlation_id_from(request),
            }
        },
    )


async def _handle_unexpected(request: Request, exc: Exception) -> JSONResponse:
    cid = _correlation_id_from(request)
    logger.exception(
        "unhandled_exception",
        path=request.url.path,
        method=request.method,
        correlation_id=cid,
    )
    return JSONResponse(
        status_code=500,
        content={
            "error": {
                "code": 500,
                "message": "An internal error occurred. Please contact support.",
                "correlation_id": cid,
            }
        },
    )


def register_exception_handlers(app: FastAPI) -> None:
    from app.ai.llm import LLMTimeoutError

    app.add_exception_handler(HTTPException, _handle_http_exception)
    app.add_exception_handler(RequestValidationError, _handle_validation_error)
    app.add_exception_handler(LLMTimeoutError, _handle_llm_timeout)
    app.add_exception_handler(Exception, _handle_unexpected)
