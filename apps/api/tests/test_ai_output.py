"""AI output validation.

Model output is untrusted input. These tests exercise the boundary that treats
it that way - not the model itself, which is not what needs guarding.
"""
from __future__ import annotations

import json

import pytest

from recover.agent import fallback
from recover.agent.llm import diagnose
from recover.agent.schemas import (
    AIDecision,
    AIValidationError,
    parse_ai_decision,
    sanitize_text,
)
from recover.enums import (
    EventType,
    FailureReason,
    PolicyDecision,
    RecoveryAction,
)
from recover.policy import evaluate
from tests.conftest import make_context

VALID = {
    "case_id": "case_test",
    "root_cause": "Transient gateway error at the issuing bank.",
    "recoverability": "high",
    "recommended_action": "RETRY_PAYMENT",
    "reason": "The customer has paid eight times before and the failure is transient.",
    "confidence": 0.82,
    "requires_approval": False,
}


def test_a_valid_decision_parses():
    decision = parse_ai_decision(json.dumps(VALID), expected_case_id="case_test")
    assert decision.recommended_action == RecoveryAction.RETRY_PAYMENT
    assert decision.confidence == 0.82


def test_fenced_json_is_recovered():
    """Models wrap JSON in code fences despite instructions. Tolerate it."""
    raw = "```json\n" + json.dumps(VALID) + "\n```"
    assert parse_ai_decision(raw, expected_case_id="case_test").recoverability == "high"


@pytest.mark.parametrize("mutation,description", [
    ({"recommended_action": "DELETE_ALL_ORDERS"}, "action outside the vocabulary"),
    ({"recommended_action": "refund_everything"}, "invented action"),
    ({"recoverability": "extremely high"}, "invalid enum"),
    ({"confidence": 1.5}, "confidence above 1"),
    ({"confidence": -0.2}, "negative confidence"),
    ({"confidence": "high"}, "confidence not a number"),
    ({"reason": ""}, "empty reason"),
    ({"root_cause": ""}, "empty root cause"),
])
def test_invalid_fields_are_rejected(mutation, description):
    payload = {**VALID, **mutation}
    with pytest.raises(AIValidationError):
        parse_ai_decision(json.dumps(payload), expected_case_id="case_test")


def test_missing_required_fields_are_rejected():
    payload = {k: v for k, v in VALID.items() if k != "recommended_action"}
    with pytest.raises(AIValidationError):
        parse_ai_decision(json.dumps(payload), expected_case_id="case_test")


def test_extra_fields_are_rejected():
    """A model cannot smuggle in a field the schema does not define - such as
    an amount, or an instruction."""
    payload = {**VALID, "recovered_amount_paise": 999_999, "override_policy": True}
    with pytest.raises(AIValidationError):
        parse_ai_decision(json.dumps(payload), expected_case_id="case_test")


def test_a_decision_for_another_case_is_rejected():
    """Without this, a confused model's answer could be applied to the wrong
    customer's money."""
    payload = {**VALID, "case_id": "case_somebody_else"}
    with pytest.raises(AIValidationError) as excinfo:
        parse_ai_decision(json.dumps(payload), expected_case_id="case_test")
    assert "case_somebody_else" in str(excinfo.value)


def test_non_json_output_is_rejected():
    with pytest.raises(AIValidationError):
        parse_ai_decision("I think we should retry this one.",
                          expected_case_id="case_test")


def test_a_json_array_is_rejected():
    with pytest.raises(AIValidationError):
        parse_ai_decision("[1, 2, 3]", expected_case_id="case_test")


def test_prose_is_sanitised_before_it_reaches_the_ui():
    dirty = "Retry\x00 this\x1b[31m   now\n\n\nplease"
    clean = sanitize_text(dirty, 100)
    assert "\x00" not in clean
    assert "\x1b" not in clean
    assert clean == "Retry this[31m now please"


def test_chatty_prose_is_truncated_for_display():
    """A model that overruns the display budget still produces a usable
    decision - the prose is trimmed, not the verdict thrown away."""
    payload = {**VALID, "reason": "x" * 900}
    decision = parse_ai_decision(json.dumps(payload), expected_case_id="case_test")
    assert len(decision.reason) <= 601
    assert decision.recommended_action == RecoveryAction.RETRY_PAYMENT


def test_pathologically_long_prose_is_rejected_outright():
    """Beyond twice the display budget we stop being generous. Output that
    long is a malfunctioning model, and the fallback should answer instead."""
    payload = {**VALID, "reason": "x" * 5000}
    with pytest.raises(AIValidationError):
        parse_ai_decision(json.dumps(payload), expected_case_id="case_test")


def test_a_decision_is_immutable():
    """Nothing downstream can quietly edit what the model said."""
    decision = parse_ai_decision(json.dumps(VALID), expected_case_id="case_test")
    with pytest.raises(Exception):
        decision.recommended_action = RecoveryAction.STOP  # type: ignore[misc]


# --------------------------------------------------------------------------
# The critical property: a valid decision is not an executable one
# --------------------------------------------------------------------------
def test_valid_json_alone_does_not_authorise_an_action():
    """Schema-valid output proposing a forbidden action is still refused."""
    ctx = make_context(order_cancelled=True)
    decision = parse_ai_decision(json.dumps(VALID), expected_case_id="case_test")
    assert decision.recommended_action == RecoveryAction.RETRY_PAYMENT
    verdict = evaluate(ctx, decision.recommended_action)
    assert verdict.decision == PolicyDecision.BLOCK
    assert verdict.code == "ORDER_CANCELLED"


def test_the_model_cannot_grant_itself_approval():
    """requires_approval=False does not make an over-limit action executable."""
    payload = {**VALID, "requires_approval": False}
    decision = parse_ai_decision(json.dumps(payload), expected_case_id="case_test")
    ctx = make_context(amount_paise=2_000_000)
    verdict = evaluate(ctx, decision.recommended_action)
    assert verdict.decision == PolicyDecision.REQUIRE_APPROVAL


# --------------------------------------------------------------------------
# Fallback path
# --------------------------------------------------------------------------
def test_fallback_produces_a_schema_valid_decision():
    decision = fallback.decide(make_context())
    assert isinstance(decision, AIDecision)
    assert decision.recommended_action == RecoveryAction.RETRY_PAYMENT


def test_fallback_stops_when_every_action_is_refused():
    ctx = make_context(retry_count=2, contacts_last_24h=2,
                       minutes_since_last_attempt=200)
    assert fallback.decide(ctx).recommended_action == RecoveryAction.STOP


def test_fallback_never_proposes_a_retry_on_a_dead_instrument():
    ctx = make_context(failure_reason=FailureReason.CARD_EXPIRED)
    assert fallback.decide(ctx).recommended_action != RecoveryAction.RETRY_PAYMENT


def test_fallback_escalates_a_state_mismatch():
    ctx = make_context(event_type=EventType.STATE_MISMATCH)
    assert fallback.decide(ctx).recommended_action == RecoveryAction.ESCALATE_TO_MERCHANT


def test_diagnose_without_an_api_key_uses_the_labelled_fallback():
    """With no key configured, the envelope must say so rather than implying a
    model was involved."""
    envelope = diagnose(make_context())
    assert envelope.path == "fallback-heuristic"
    assert envelope.model is None
    assert envelope.degraded is False


def test_diagnose_is_deterministic_on_the_fallback_path():
    ctx = make_context()
    actions = {diagnose(ctx, force_fallback=True).decision.recommended_action
               for _ in range(10)}
    assert len(actions) == 1


def test_diagnose_never_raises_when_the_model_path_explodes(monkeypatch):
    """A model outage must degrade the product, not break it."""
    from recover.agent import llm as llm_module

    monkeypatch.setattr(llm_module.get_settings(), "anthropic_api_key", "sk-test-fake",
                        raising=False)

    def explode(*args, **kwargs):
        raise RuntimeError("connection reset by peer")

    monkeypatch.setattr(llm_module, "_run_llm", explode)
    envelope = llm_module.diagnose(make_context())
    assert envelope.degraded is True
    assert envelope.path == "fallback-heuristic"
    assert "connection reset" in (envelope.validation_error or "")
    assert envelope.decision.recommended_action in set(RecoveryAction)
