"""Razorpay Test Mode adapter.

Implemented against the official Razorpay documentation and the official
razorpay-python SDK:

  * Orders            POST https://api.razorpay.com/v1/orders
                      amount is in the smallest currency sub-unit (paise),
                      receipt max 40 chars.
                      https://razorpay.com/docs/api/orders/create/
  * Payment Links     POST https://api.razorpay.com/v1/payment_links
                      returns `short_url`; callback_method must be "get".
                      https://razorpay.com/docs/api/payments/payment-links/create-standard/
  * Checkout signature  HMAC-SHA256 over "{order_id}|{payment_id}" keyed with
                      the API secret - razorpay.Utility.verify_payment_signature
  * Webhook signature  HMAC-SHA256 over the **raw request body** keyed with the
                      webhook secret, delivered in the `X-Razorpay-Signature`
                      header - razorpay.Utility.verify_webhook_signature
                      https://razorpay.com/docs/webhooks/validate-test/

Nothing here is invented; where the SDK provides a verification helper we call
the SDK rather than re-implementing the signature scheme.
"""
from __future__ import annotations

import logging

import razorpay
from razorpay.errors import SignatureVerificationError

from ..config import get_settings
from .base import (
    PaymentProvider,
    ProviderError,
    ProviderOrder,
    ProviderPayment,
    ProviderPaymentLink,
)

log = logging.getLogger(__name__)

#: Razorpay error codes -> our normalised failure taxonomy. Anything unmapped
#: falls through to DO_NOT_HONOUR, which is the conservative choice: it is not
#: auto-retried on a strong prior.
RAZORPAY_REASON_MAP = {
    "GATEWAY_ERROR": "BANK_TRANSIENT",
    "BAD_REQUEST_ERROR": "DO_NOT_HONOUR",
    "SERVER_ERROR": "BANK_TRANSIENT",
    "NETWORK_ERROR": "NETWORK_ERROR",
}

#: The `error_reason` / internal reason strings Razorpay returns on a failed
#: payment, mapped to the same taxonomy.
RAZORPAY_ERROR_REASON_MAP = {
    "payment_failed": "BANK_TRANSIENT",
    "insufficient_funds": "INSUFFICIENT_FUNDS",
    "card_expired": "CARD_EXPIRED",
    "incorrect_cvv": "INCORRECT_CVV",
    "payment_declined_by_bank": "DO_NOT_HONOUR",
    "authentication_failed": "AUTHENTICATION_FAILED",
    "payment_cancelled": "CUSTOMER_CANCELLED",
    "payment_risk_check_failed": "RISK_DECLINED",
    "invalid_mandate": "MANDATE_INACTIVE",
}


def normalise_failure(error_code: str | None, error_reason: str | None,
                      error_description: str | None = None) -> str:
    """Map a Razorpay failure onto our closed failure taxonomy."""
    if error_reason and error_reason in RAZORPAY_ERROR_REASON_MAP:
        return RAZORPAY_ERROR_REASON_MAP[error_reason]
    blob = " ".join(filter(None, [error_reason, error_description])).lower()
    for needle, mapped in (
        ("insufficient", "INSUFFICIENT_FUNDS"),
        ("expired", "CARD_EXPIRED"),
        ("cvv", "INCORRECT_CVV"),
        ("authentication", "AUTHENTICATION_FAILED"),
        ("cancel", "CUSTOMER_CANCELLED"),
        ("risk", "RISK_DECLINED"),
        ("mandate", "MANDATE_INACTIVE"),
        ("timeout", "BANK_TRANSIENT"),
    ):
        if needle in blob:
            return mapped
    if error_code and error_code in RAZORPAY_REASON_MAP:
        return RAZORPAY_REASON_MAP[error_code]
    return "DO_NOT_HONOUR"


class RazorpayPaymentProvider(PaymentProvider):
    name = "razorpay"
    mode = "test"

    def __init__(self) -> None:
        settings = get_settings()
        if not settings.razorpay_enabled:
            raise ProviderError("Razorpay credentials are not configured.")
        if not settings.is_test_mode_key:
            # A hard stop. This prototype must never be pointed at live money.
            raise ProviderError(
                "RAZORPAY_KEY_ID does not start with 'rzp_test_'. RECOVER refuses to run "
                "against live credentials."
            )
        self._key_id = settings.razorpay_key_id
        self._client = razorpay.Client(auth=(settings.razorpay_key_id,
                                             settings.razorpay_key_secret))
        self._client.set_app_details({"title": "RECOVER", "version": "0.1.0"})
        self._webhook_secret = settings.razorpay_webhook_secret

    @property
    def public_key_id(self) -> str | None:
        return self._key_id

    # -- writes ------------------------------------------------------------
    def create_order(self, *, amount_paise: int, currency: str, receipt: str,
                     notes: dict | None = None) -> ProviderOrder:
        try:
            data = self._client.order.create({
                "amount": int(amount_paise),          # smallest currency sub-unit
                "currency": currency,
                "receipt": receipt[:40],              # documented 40-char limit
                "notes": notes or {},
            })
        except Exception as exc:
            raise ProviderError(f"razorpay order.create failed: {exc}") from exc
        return ProviderOrder(
            id=data["id"], amount_paise=int(data["amount"]), currency=data["currency"],
            status=data["status"], receipt=data.get("receipt"),
        )

    def create_payment_link(self, *, amount_paise: int, currency: str, description: str,
                            reference_id: str, customer_name: str, customer_email: str,
                            customer_contact: str, callback_url: str,
                            notes: dict | None = None) -> ProviderPaymentLink:
        payload = {
            "amount": int(amount_paise),
            "currency": currency,
            "description": description[:2048],
            "reference_id": reference_id[:40],
            "customer": {
                "name": customer_name,
                "email": customer_email,
                "contact": customer_contact,
            },
            "notify": {"sms": False, "email": False},
            "reminder_enable": False,
            "callback_url": callback_url,
            "callback_method": "get",       # documented: must be "get"
            "notes": notes or {},
        }
        try:
            data = self._client.payment_link.create(payload)
        except Exception as exc:
            raise ProviderError(f"razorpay payment_link.create failed: {exc}") from exc
        return ProviderPaymentLink(
            id=data["id"], short_url=data["short_url"], amount_paise=int(data["amount"]),
            status=data["status"], reference_id=data.get("reference_id"),
        )

    # -- reads -------------------------------------------------------------
    def fetch_payment(self, payment_id: str) -> ProviderPayment:
        try:
            data = self._client.payment.fetch(payment_id)
        except Exception as exc:
            raise ProviderError(f"razorpay payment.fetch failed: {exc}") from exc
        return ProviderPayment(
            id=data["id"], order_id=data.get("order_id"), amount_paise=int(data["amount"]),
            currency=data.get("currency", "INR"), status=data["status"],
            method=data.get("method"), error_code=data.get("error_code"),
            error_description=data.get("error_description"),
            error_reason=data.get("error_reason"),
        )

    # -- verification ------------------------------------------------------
    def verify_checkout_signature(self, *, order_id: str, payment_id: str,
                                  signature: str) -> bool:
        """HMAC-SHA256 over "order_id|payment_id" keyed with the API secret."""
        try:
            self._client.utility.verify_payment_signature({
                "razorpay_order_id": order_id,
                "razorpay_payment_id": payment_id,
                "razorpay_signature": signature,
            })
            return True
        except SignatureVerificationError:
            return False
        except Exception as exc:
            log.warning("checkout signature verification error: %s", type(exc).__name__)
            return False

    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool:
        """HMAC-SHA256 over the RAW body keyed with the webhook secret.

        The body must not be parsed and re-serialised before this runs, or the
        bytes will differ and every delivery will be rejected.
        """
        if not self._webhook_secret:
            raise ProviderError("RAZORPAY_WEBHOOK_SECRET is not configured.")
        try:
            self._client.utility.verify_webhook_signature(
                body.decode("utf-8"), signature, self._webhook_secret
            )
            return True
        except SignatureVerificationError:
            return False
        except Exception as exc:
            log.warning("webhook signature verification error: %s", type(exc).__name__)
            return False
