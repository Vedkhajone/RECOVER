"""Payment provider port.

The rest of RECOVER talks to this interface only. Razorpay lives behind it, and
so does the local simulator used when no Test Mode credentials are configured -
which means the recovery engine, the policy engine and the webhook pipeline are
identical on both paths.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass


@dataclass(frozen=True)
class ProviderOrder:
    id: str
    amount_paise: int
    currency: str
    status: str
    receipt: str | None = None


@dataclass(frozen=True)
class ProviderPaymentLink:
    id: str
    short_url: str
    amount_paise: int
    status: str
    reference_id: str | None = None


@dataclass(frozen=True)
class ProviderPayment:
    id: str
    order_id: str | None
    amount_paise: int
    currency: str
    status: str
    method: str | None = None
    error_code: str | None = None
    error_description: str | None = None
    error_reason: str | None = None


class ProviderError(Exception):
    """Any failure talking to the payment provider. Never leaks credentials."""


class PaymentProvider(ABC):
    #: Short identifier surfaced in the UI so nobody mistakes the simulator for
    #: a real Razorpay integration.
    name: str = "abstract"
    #: "test" | "simulated". Never "live" - this prototype refuses live keys.
    mode: str = "simulated"

    @property
    def public_key_id(self) -> str | None:
        """The key id is public by design (it ships to the browser). The secret
        never leaves the server."""
        return None

    @abstractmethod
    def create_order(self, *, amount_paise: int, currency: str, receipt: str,
                     notes: dict | None = None) -> ProviderOrder: ...

    @abstractmethod
    def create_payment_link(self, *, amount_paise: int, currency: str, description: str,
                            reference_id: str, customer_name: str, customer_email: str,
                            customer_contact: str, callback_url: str,
                            notes: dict | None = None) -> ProviderPaymentLink: ...

    @abstractmethod
    def fetch_payment(self, payment_id: str) -> ProviderPayment: ...

    @abstractmethod
    def verify_checkout_signature(self, *, order_id: str, payment_id: str,
                                  signature: str) -> bool: ...

    @abstractmethod
    def verify_webhook_signature(self, *, body: bytes, signature: str) -> bool: ...
