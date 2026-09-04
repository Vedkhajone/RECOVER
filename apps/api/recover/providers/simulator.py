"""Local payment simulator.

Used when RAZORPAY_KEY_ID / RAZORPAY_KEY_SECRET are absent, so a clean clone
runs end to end with no account setup.

It is a *simulator*, not a mock: it emits the same webhook envelope shape
Razorpay documents, signs it with HMAC-SHA256 over the raw body exactly as
Razorpay does, and pushes it through the same verification and idempotency
pipeline. The parts of RECOVER that matter - signature checking, duplicate
suppression, state transitions, recovery accounting - are genuinely exercised
on this path. What is *not* exercised is Razorpay's own API surface.

The UI always states which provider is live. Nothing here is ever presented as
a real Razorpay transaction.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import time

from ..config import get_settings
from .base import (
    PaymentProvider,
    ProviderError,
    ProviderOrder,
    ProviderPayment,
    ProviderPaymentLink,
)

#: Deterministic local secret. Not sensitive - it protects nothing real - but it
#: keeps the signature path honest rather than short-circuited.
SIMULATED_SECRET = "recover_simulated_webhook_secret"


class SimulatedPaymentProvider(PaymentProvider):
    name = "simulator"
    mode = "simulated"

    def __init__(self) -> None:
        self._payments: dict[str, ProviderPayment] = {}
        self._orders: dict[str, ProviderOrder] = {}
        self._counter = 0

    def _next_id(self, prefix: str) -> str:
        self._counter += 1
        return f"{prefix}_sim{self._counter:06d}{secrets.token_hex(3)}"

    @property
    def public_key_id(self) -> str | None:
        return None

    def create_order(self, *, amount_paise: int, currency: str, receipt: str,
                     notes: dict | None = None) -> ProviderOrder:
        order = ProviderOrder(id=self._next_id("order"), amount_paise=int(amount_paise),
                              currency=currency, status="created", receipt=receipt[:40])
        self._orders[order.id] = order
        return order

    def create_payment_link(self, *, amount_paise: int, currency: str, description: str,
                            reference_id: str, customer_name: str, customer_email: str,
                            customer_contact: str, callback_url: str,
                            notes: dict | None = None) -> ProviderPaymentLink:
        link_id = self._next_id("plink")
        return ProviderPaymentLink(
            id=link_id,
            # Points at RECOVER's own hosted recovery page rather than rzp.io.
            short_url=callback_url,
            amount_paise=int(amount_paise), status="created", reference_id=reference_id[:40],
        )

    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        payment = self._payments.get(payment_id)
        if payment is None:
            raise ProviderError(f"simulated payment {payment_id} not found")
        return payment

    def record_payment(self, *, order_id: str | None, amount_paise: int, succeeded: bool,
                       method: str = "card", failure: tuple[str, str, str] | None = None
                       ) -> ProviderPayment:
        """Create a simulated payment outcome. Called by the customer recovery page."""
        code, description, reason = failure or ("", "", "")
        payment = ProviderPayment(
            id=self._next_id("pay"), order_id=order_id, amount_paise=int(amount_paise),
            currency="INR", status="captured" if succeeded else "failed", method=method,
            error_code=code or None, error_description=description or None,
            error_reason=reason or None,
        )
        self._payments[payment.id] = payment
        return payment

    # -- verification ------------------------------------------------------
    def _sign(self, body: bytes) -> str:
        return hmac.new(SIMULATED_SECRET.encode(), body, hashlib.sha256).hexdigest()

    def verify_checkout_signature(self, *, order_id: str, payment_id: str,
                                  signature: str) -> bool:
        expected = self._sign(f"{order_id}|{payment_id}".encode())
        return hmac.compare_digest(expected, signature or "")

    def sign_checkout(self, *, order_id: str, payment_id: str) -> str:
        return self._sign(f"{order_id}|{payment_id}".encode())

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool:
        return hmac.compare_digest(self._sign(body), signature or "")

    def build_webhook(self, *, event: str, payment: ProviderPayment) -> tuple[bytes, str, str]:
        """Build a signed webhook delivery in Razorpay's documented envelope shape.

        Returns (raw_body, signature, event_id) so the caller can feed it through
        the real /webhooks/razorpay endpoint.
        """
        envelope = {
            "entity": "event",
            "account_id": "acc_simulated",
            "event": event,
            "contains": ["payment"],
            "payload": {
                "payment": {
                    "entity": {
                        "id": payment.id,
                        "entity": "payment",
                        "amount": payment.amount_paise,
                        "currency": payment.currency,
                        "status": payment.status,
                        "order_id": payment.order_id,
                        "method": payment.method,
                        "error_code": payment.error_code,
                        "error_description": payment.error_description,
                        "error_reason": payment.error_reason,
                    }
                }
            },
            "created_at": int(time.time()),
        }
        body = json.dumps(envelope, separators=(",", ":")).encode()
        return body, self._sign(body), f"evt_sim_{payment.id}"


_provider: PaymentProvider | None = None


def get_payment_provider() -> PaymentProvider:
    """Process-wide provider. Razorpay Test Mode when configured, else the simulator."""
    global _provider
    if _provider is None:
        settings = get_settings()
        if settings.razorpay_enabled:
            from .razorpay_provider import RazorpayPaymentProvider

            _provider = RazorpayPaymentProvider()
        else:
            _provider = SimulatedPaymentProvider()
    return _provider


def reset_provider() -> None:
    """Test hook."""
    global _provider
    _provider = None
