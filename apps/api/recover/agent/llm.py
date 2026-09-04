"""Anthropic-backed recovery agent.

The model's job is the genuinely ambiguous part of the work: reading a failure
code plus a customer's history plus what has already been tried, and judging
what happened and whether chasing this money is worth it.

Everything it is *not* trusted with is enforced structurally rather than by
instruction:

  * it can only call read tools (recover/agent/tools.py);
  * it can only answer through `submit_recovery_decision`, whose schema has no
    field for an amount, a state, or a policy override;
  * whatever it returns is re-validated by policy.evaluate() afterwards.

So the prompt below is guidance, not a control. If the model ignores every word
of it, the worst outcome is a bad proposal that the policy engine rejects.
"""
from __future__ import annotations

import json
import time
from typing import Any

from ..config import get_settings
from ..context import RecoveryContext
from . import fallback
from .schemas import AIDecision, AIDecisionEnvelope, AIValidationError, parse_ai_decision
from .tools import ToolContext, anthropic_tool_schemas, dispatch

MAX_TOOL_ROUNDS = 6
MAX_TOKENS = 1400

SYSTEM_PROMPT = """\
You are the diagnosis and triage engine inside RECOVER, a revenue-recovery \
system for an Indian payments merchant. A payment, checkout, subscription or \
invoice has put revenue at risk, and a case has been opened.

Your job, and only this:
  1. Work out what actually happened, from the evidence.
  2. Judge how recoverable the money is.
  3. Choose the single best recovery action that merchant policy permits.
  4. Explain the choice in two or three plain sentences a support lead could read.

How to work:
  - Call the read tools before deciding. At minimum look at the failure \
context, the customer's history, and the deterministic recovery score.
  - `calculate_recovery_score` returns the authoritative recovery probability \
and expected value. Use those numbers. Do not compute your own and do not \
contradict them.
  - `propose_recovery_action` tells you what merchant policy already thinks of \
each action. Choosing something marked BLOCK wastes the case; it will be \
rejected and the case will fall back to a safe default.
  - Finish by calling `submit_recovery_decision` exactly once.

Judgement you are expected to exercise:
  - Not every failure is worth chasing. A low-value case with a low recovery \
probability and a customer who has already been contacted twice should be \
STOPped. Stopping is a correct answer, not a failure.
  - Distinguish failures that a retry can fix (transient bank/network errors) \
from failures that it cannot (expired card, revoked mandate, deliberate \
cancellation). Retrying the second kind irritates a customer for nothing.
  - A reliable customer with a long success history deserves more benefit of \
the doubt than a new account with two failures.
  - If the amount needs merchant approval, still recommend the action you \
believe is right and set requires_approval to true.

Constraints that are enforced whatever you say, so do not attempt them:
  - You cannot move money, capture a payment, mark anything recovered, change \
an order's state, or alter merchant policy.
  - You cannot invent transaction data. If a field is not in the evidence, it \
is not known.
  - You cannot claim revenue was recovered. Recovery is recorded only from a \
verified payment event.
"""

SUBMIT_TOOL = {
    "name": "submit_recovery_decision",
    "description": (
        "Submit your final structured decision for this case. Call exactly once, "
        "after you have gathered evidence."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "case_id": {"type": "string", "description": "The case id you were given."},
            "root_cause": {
                "type": "string",
                "description": "One or two sentences on what actually went wrong.",
            },
            "recoverability": {"type": "string", "enum": ["high", "medium", "low"]},
            "recommended_action": {
                "type": "string",
                "enum": ["WAIT", "RETRY_PAYMENT", "SEND_PAYMENT_LINK", "SEND_REMINDER",
                         "OFFER_ALLOWED_ALTERNATIVE", "ESCALATE_TO_MERCHANT", "STOP"],
            },
            "reason": {
                "type": "string",
                "description": "Two or three plain sentences justifying the action.",
            },
            "confidence": {"type": "number", "minimum": 0, "maximum": 1},
            "requires_approval": {"type": "boolean"},
            "evidence_used": {
                "type": "array",
                "items": {"type": "string"},
                "description": "Names of the context fields you relied on.",
            },
        },
        "required": ["case_id", "root_cause", "recoverability", "recommended_action",
                     "reason", "confidence", "requires_approval"],
    },
}


def _case_brief(ctx: RecoveryContext) -> str:
    return (
        f"Case {ctx.case_id} has been opened.\n\n"
        f"Initial evidence (you may look up more with the tools):\n"
        f"{json.dumps(ctx.evidence(), indent=2, default=str)}\n\n"
        f"Diagnose this case and submit your decision."
    )


class LLMUnavailable(Exception):
    """The model path could not produce a validated decision."""


def _run_llm(ctx: RecoveryContext, tc: ToolContext) -> tuple[AIDecision, list[str], str]:
    from anthropic import Anthropic

    settings = get_settings()
    client = Anthropic(api_key=settings.anthropic_api_key, timeout=settings.ai_timeout_seconds)
    tools = [*anthropic_tool_schemas(), SUBMIT_TOOL]
    messages: list[dict[str, Any]] = [{"role": "user", "content": _case_brief(ctx)}]

    for round_index in range(MAX_TOOL_ROUNDS):
        # On the final round force the answer, so a model that keeps browsing
        # tools still terminates with a decision rather than timing out.
        force_answer = round_index == MAX_TOOL_ROUNDS - 1
        response = client.messages.create(
            model=settings.anthropic_model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=tools,
            tool_choice=({"type": "tool", "name": "submit_recovery_decision"}
                         if force_answer else {"type": "auto"}),
            messages=messages,
        )

        tool_uses = [b for b in response.content if getattr(b, "type", None) == "tool_use"]
        if not tool_uses:
            # Model replied in prose. Try to salvage JSON from the text; if that
            # fails the caller degrades to the fallback.
            text = "".join(getattr(b, "text", "") for b in response.content)
            return parse_ai_decision(text, expected_case_id=ctx.case_id), tc.calls, "llm"

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in tool_uses:
            if block.name == "submit_recovery_decision":
                decision = parse_ai_decision(dict(block.input), expected_case_id=ctx.case_id)
                return decision, tc.calls, "llm"
            payload = dispatch(tc, block.name, dict(block.input or {}))
            results.append({
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": json.dumps(payload, default=str),
            })
        messages.append({"role": "user", "content": results})

    raise LLMUnavailable("model did not submit a decision within the tool-call budget")


def diagnose(ctx: RecoveryContext, *, db=None, force_fallback: bool = False) -> AIDecisionEnvelope:
    """Produce a decision for `ctx`, using the model when it is available.

    Never raises. A model outage, a malformed response or a schema violation all
    degrade to the deterministic fallback, and the envelope says so.
    """
    settings = get_settings()
    tc = ToolContext(ctx=ctx, db=db)
    started = time.perf_counter()

    if force_fallback or not settings.ai_enabled:
        decision = fallback.decide(ctx)
        return AIDecisionEnvelope(
            decision=decision,
            path="fallback-heuristic",
            model=None,
            tool_calls=[],
            latency_ms=int((time.perf_counter() - started) * 1000),
            degraded=False,
        )

    validation_error: str | None = None
    try:
        decision, calls, path = _run_llm(ctx, tc)
        return AIDecisionEnvelope(
            decision=decision,
            path=path,
            model=settings.anthropic_model,
            tool_calls=calls,
            latency_ms=int((time.perf_counter() - started) * 1000),
        )
    except AIValidationError as exc:
        validation_error = f"schema validation failed: {exc}"
    except LLMUnavailable as exc:
        validation_error = str(exc)
    except Exception as exc:  # network, auth, rate limit, SDK change
        validation_error = f"{type(exc).__name__}: {exc}"

    decision = fallback.decide(ctx)
    return AIDecisionEnvelope(
        decision=decision,
        path="fallback-heuristic",
        model=None,
        tool_calls=tc.calls,
        latency_ms=int((time.perf_counter() - started) * 1000),
        validation_error=validation_error,
        degraded=True,
    )
