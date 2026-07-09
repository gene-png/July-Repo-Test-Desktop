"""SQLAlchemy engine + session factory.

Sync engine for v1 (FastAPI runs handlers in a worker thread when they're
defined `def`). Switching to async is a v1.x candidate but not load-bearing
for v1.
"""

from __future__ import annotations

import uuid
from collections.abc import Generator

from fastapi import HTTPException, status
from sqlalchemy import create_engine, text
from sqlalchemy.engine import Engine
from sqlalchemy.orm import Session, sessionmaker

from app.config import get_settings


def _build_engine() -> Engine:
    settings = get_settings()
    return create_engine(
        settings.database_url,
        pool_pre_ping=True,
        future=True,
    )


engine: Engine = _build_engine()
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False, future=True)


def get_db() -> Generator[Session, None, None]:
    """FastAPI dependency yielding a request-scoped DB session."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _advisory_lock_key(subject_id: uuid.UUID) -> int:
    """A stable signed 64-bit key from a UUID for pg advisory locks.

    Postgres advisory-lock functions take a bigint. We fold the first 8 bytes of
    the UUID into a signed 64-bit integer so the same assessment/client always
    maps to the same lock key.
    """
    return int.from_bytes(subject_id.bytes[:8], "big", signed=True)


def assessment_advisory_lock(db: Session, subject_id: uuid.UUID) -> None:
    """Acquire a transaction-scoped advisory lock for an AI run (Task S2-A E-3).

    Serializes concurrent run-ai / generate calls for the same assessment (or,
    for the Risk Register, the same client). On PostgreSQL this uses
    ``pg_try_advisory_xact_lock``; when the lock is already held by another
    in-flight transaction we raise 409 rather than block. On SQLite (the test
    suite) there is no advisory-lock facility, so this is a deliberate no-op —
    real contention behavior can only be verified on Postgres.
    """
    bind = db.get_bind()
    if bind.dialect.name != "postgresql":
        return
    key = _advisory_lock_key(subject_id)
    acquired = db.execute(text("SELECT pg_try_advisory_xact_lock(:key)"), {"key": key}).scalar()
    if not acquired:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a run is already in progress",
        )
