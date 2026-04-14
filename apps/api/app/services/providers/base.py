"""Abstract base class for payment providers."""

from __future__ import annotations

from abc import ABC, abstractmethod

from app.models.order import Order

__all__ = [
    "PaymentProvider",
]


class PaymentProvider(ABC):
    """Interface every payment provider must satisfy."""

    @abstractmethod
    def create_session(self, order: Order) -> dict:
        """
        Create a checkout session for *order*.

        Returns a dict with at least::

            {"session_id": str, "checkout_url": str}
        """

    @abstractmethod
    def handle_webhook(self, payload: bytes, signature: str) -> dict:
        """
        Verify and parse an incoming webhook event.

        Returns a normalised dict with at least::

            {"event_id": str, "event_type": str}

        Additional keys (``order_number``, ``payment_intent_id``, …) are
        provider-specific and consumed by *payment_service*.
        """

    def is_confirmed_payment_id(self, payment_id: str | None) -> bool:
        """
        Return True when *payment_id* represents a successfully confirmed
        payment (not a pending checkout session).  Override in providers
        where the two are distinguishable by ID format.
        """
        return False
