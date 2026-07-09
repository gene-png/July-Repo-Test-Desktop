"""Real Postgres contention for the E-3 advisory lock (integration).

The unit suite runs SQLite, where assessment_advisory_lock is a deliberate
no-op, so genuine two-transaction contention was an honest coverage gap
(recorded in Sprint 2's evidence). This module closes it: it runs only when
a PostgreSQL DATABASE_URL is reachable (the local docker stack or the CI
e2e/restore-drill service) and is skipped otherwise.

Run: DATABASE_URL=postgresql+psycopg://shield:shield@localhost:5432/shield \\
       pytest -m integration apps/api/tests/integration
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi import HTTPException
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

pytestmark = pytest.mark.integration


def _pg_engine():
    url = os.environ.get("DATABASE_URL", "")
    if not url.startswith("postgresql"):
        pytest.skip("integration test requires a PostgreSQL DATABASE_URL")
    engine = create_engine(url, future=True, pool_pre_ping=True)
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
    except Exception as exc:  # noqa: BLE001 - availability probe
        pytest.skip(f"PostgreSQL not reachable: {exc}")
    return engine


def test_second_transaction_loses_and_gets_409() -> None:
    from app.db.session import assessment_advisory_lock

    engine = _pg_engine()
    subject = uuid.uuid4()

    winner = Session(bind=engine, future=True)
    loser = Session(bind=engine, future=True)
    try:
        # Winner takes the lock inside an open transaction.
        assessment_advisory_lock(winner, subject)

        # A second, genuinely concurrent transaction must lose with the typed 409.
        with pytest.raises(HTTPException) as excinfo:
            assessment_advisory_lock(loser, subject)
        assert excinfo.value.status_code == 409
        assert "already in progress" in str(excinfo.value.detail)
    finally:
        winner.rollback()
        loser.rollback()
        winner.close()
        loser.close()
    engine.dispose()


def test_lock_releases_at_transaction_end() -> None:
    from app.db.session import assessment_advisory_lock

    engine = _pg_engine()
    subject = uuid.uuid4()

    first = Session(bind=engine, future=True)
    try:
        assessment_advisory_lock(first, subject)
        first.commit()  # xact lock releases here

        second = Session(bind=engine, future=True)
        try:
            # No exception: the key is free again after the winner's commit.
            assessment_advisory_lock(second, subject)
        finally:
            second.rollback()
            second.close()
    finally:
        first.rollback()
        first.close()
    engine.dispose()


def test_distinct_subjects_do_not_contend() -> None:
    from app.db.session import assessment_advisory_lock

    engine = _pg_engine()
    a = Session(bind=engine, future=True)
    b = Session(bind=engine, future=True)
    try:
        assessment_advisory_lock(a, uuid.uuid4())
        # A different assessment locks independently: no 409.
        assessment_advisory_lock(b, uuid.uuid4())
    finally:
        a.rollback()
        b.rollback()
        a.close()
        b.close()
    engine.dispose()
