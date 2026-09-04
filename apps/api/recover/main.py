"""RECOVER API.

A prototype built for the Razorpay Buildathon using Razorpay Test Mode. Not an
official Razorpay product.
"""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from .config import get_settings
from .db import init_db
from .executor import PolicyViolation
from .routers import (
    audit_routes,
    cases,
    dashboard,
    demo,
    evaluation,
    ingest,
    policy,
    recovery,
    webhook_routes,
)
from .state_machine import InvalidStateTransition

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s %(message)s",
)
log = logging.getLogger("recover")

settings = get_settings()


@asynccontextmanager
async def lifespan(_: FastAPI):
    init_db()
    if settings.razorpay_enabled and not settings.is_test_mode_key:
        # Refuse to start rather than risk touching live money.
        raise RuntimeError(
            "RAZORPAY_KEY_ID is not a test key (expected an rzp_test_ prefix). "
            "RECOVER only runs against Razorpay Test Mode."
        )
    log.info("RECOVER started | razorpay=%s | ai=%s | db=%s",
             settings.razorpay_enabled, settings.ai_enabled,
             "postgres" if settings.database_url.startswith("postgres") else "sqlite")
    yield


app = FastAPI(
    lifespan=lifespan,
    title="RECOVER",
    description=(
        "AI that finds slipping revenue, recovers what it can, and knows when to stop. "
        "A prototype built for the Razorpay Buildathon using Razorpay Test Mode."
    ),
    version="0.1.0",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list,
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.exception_handler(PolicyViolation)
async def policy_violation_handler(request: Request, exc: PolicyViolation):
    """A policy refusal is a 403 with the reason, never a 500.

    The merchant should always be able to see *why* something did not happen.
    """
    log.warning("policy violation on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=403,
                        content={"error": "policy_violation", "detail": str(exc)})


@app.exception_handler(InvalidStateTransition)
async def invalid_transition_handler(request: Request, exc: InvalidStateTransition):
    log.warning("invalid state transition on %s: %s", request.url.path, exc)
    return JSONResponse(status_code=409,
                        content={"error": "invalid_state_transition", "detail": str(exc)})


@app.get("/api/health")
def health() -> dict:
    return {"status": "ok", "service": "recover"}


app.include_router(dashboard.router)
app.include_router(cases.router)
app.include_router(policy.router)
app.include_router(recovery.router)
app.include_router(webhook_routes.router)
app.include_router(audit_routes.router)
app.include_router(evaluation.router)
app.include_router(demo.router)
app.include_router(ingest.router)
