"""First-run bootstrap: create the schema, and seed demo data if the database
is empty.

Run as `python -m recover.bootstrap`. Docker Compose calls it before starting
uvicorn so a fresh `docker compose up` lands on a populated dashboard rather
than an empty one.

Idempotent: an existing database with cases in it is left completely alone.
"""
from __future__ import annotations

import logging
import sys

from sqlalchemy import func, select

from .db import init_db, session_scope
from .models import RecoveryCase

logging.basicConfig(level=logging.INFO, format="%(levelname)-8s %(message)s")
log = logging.getLogger("recover.bootstrap")


def main() -> int:
    init_db()
    log.info("schema ready")

    with session_scope() as db:
        existing = db.execute(select(func.count()).select_from(RecoveryCase)).scalar_one()
        if existing:
            log.info("database already has %s cases; leaving it untouched", existing)
            return 0

    from .seed import reset_and_seed

    result = reset_and_seed()
    log.info("seeded %s scenario cases and %s bulk cases for merchant %s",
             result["scenario_cases"], result["bulk_cases"], result["merchant_id"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
