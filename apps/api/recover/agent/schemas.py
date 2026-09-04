"""Strict schema for AI output.

Everything the model returns is untrusted input. It is parsed here, and a parse
failure is a *normal, expected* outcome that the caller must handle - not an
exception that crashes a case.

Note what this schema deliberately cannot express: an amount, an order state, a
recovered figure, a policy override. The model has no vocabulary for those, so
it cannot assert them even if it tries.
"""
from __future__ import annotations

import json
import re
import unicodedata

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..enums import Recoverability, RecoveryAction

MAX_REASON_CHARS = 600
MAX_ROOT_CAUSE_CHARS = 300

_CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def sanitize_text(value: str, limit: int) -> str:
    """Model prose is rendered in the merchant UI, so it is scrubbed first.

    Strips control characters, normalises unicode, collapses whitespace and
    hard-truncates. This prevents a model (or anything that influenced it) from
    smuggling markup or terminal escapes into an operator's screen.
    """
    value = unicodedata.normalize("NFKC", value)
    value = _CONTROL_CHARS.sub("", value)
    value = re.sub(r"\s+", " ", value).strip()
    if len(value) > limit:
        value = value[: limit - 1].rstrip() + "…"
    return value


class AIDecision(BaseModel):
    """The one structure the agent is allowed to emit."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    case_id: str = Field(min_length=1, max_length=64)
    root_cause: str = Field(min_length=1, max_length=MAX_ROOT_CAUSE_CHARS * 2)
    recoverability: Recoverability
    recommended_action: RecoveryAction
    reason: str = Field(min_length=1, max_length=MAX_REASON_CHARS * 2)
    confidence: float = Field(ge=0.0, le=1.0)
    requires_approval: bool
    #: Which context fields the model says it relied on. Advisory, shown in the
    #: decision panel so an operator can sanity-check the reasoning.
    evidence_used: list[str] = Field(default_factory=list, max_length=12)

    @field_validator("root_cause")
    @classmethod
    def _clean_root_cause(cls, v: str) -> str:
        return sanitize_text(v, MAX_ROOT_CAUSE_CHARS)

    @field_validator("reason")
    @classmethod
    def _clean_reason(cls, v: str) -> str:
        return sanitize_text(v, MAX_REASON_CHARS)

    @field_validator("evidence_used")
    @classmethod
    def _clean_evidence(cls, v: list[str]) -> list[str]:
        return [sanitize_text(str(item), 80) for item in v if str(item).strip()][:12]


class AIDecisionEnvelope(BaseModel):
    """AIDecision plus provenance. Persisted so the UI can be honest about
    whether a real model produced this or the deterministic fallback did."""

    decision: AIDecision
    path: str            # "llm" | "fallback-heuristic"
    model: str | None = None
    tool_calls: list[str] = Field(default_factory=list)
    latency_ms: int = 0
    validation_error: str | None = None
    #: True when the LLM was attempted and failed, and the fallback answered.
    degraded: bool = False


class AIValidationError(Exception):
    """Raised when model output cannot be coerced into AIDecision."""


def parse_ai_decision(raw: str | dict, *, expected_case_id: str) -> AIDecision:
    """Parse and validate model output, pinning the case id.

    Pinning matters: without it a model could return a decision addressed to a
    different case, and a careless caller would apply it to the wrong money.
    """
    if isinstance(raw, str):
        text = raw.strip()
        # Models sometimes wrap JSON in a fenced block despite instructions.
        if text.startswith("```"):
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
        try:
            payload = json.loads(text)
        except json.JSONDecodeError as exc:
            raise AIValidationError(f"model output was not valid JSON: {exc}") from exc
    else:
        payload = raw

    if not isinstance(payload, dict):
        raise AIValidationError("model output was not a JSON object")

    payload = dict(payload)
    returned_id = payload.get("case_id")
    if returned_id not in (None, expected_case_id):
        raise AIValidationError(
            f"model returned a decision for case {returned_id!r}, expected {expected_case_id!r}"
        )
    payload["case_id"] = expected_case_id

    try:
        return AIDecision.model_validate(payload)
    except Exception as exc:  # pydantic.ValidationError and anything else
        raise AIValidationError(str(exc)) from exc
