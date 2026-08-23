"""The small badge that says who is carrying an order — code, name, logo."""

from __future__ import annotations

from pydantic import BaseModel


class CourierBadge(BaseModel):
    """A carrier identified for display: a stable code, a name, a logo URL.

    Built by `courier_catalog.badge_for_order` from either an aggregator order's
    channel or a delivery order's fulfilment provider, so the register and admin
    show the same thing the same way. The `logo_url` comes from the `couriers`
    table (cached), so a logo can be swapped in the database without touching an
    app.
    """

    code: str
    name: str
    logo_url: str
    is_aggregator: bool = False

    @classmethod
    def for_order(
        cls,
        *,
        source: str | None,
        aggregator_channel: str | None = None,
        delivery_provider: str | None = None,
    ) -> "CourierBadge | None":
        # Imported here, not at module load: `courier_catalog` lives under
        # `app.services`, and importing that package while `app.schemas.order`
        # is still initialising (it, in turn, is imported by the services during
        # their own init) is a circular import. By call time everything is up.
        from app.services import courier_catalog

        data = courier_catalog.badge_for_order(
            source=source,
            aggregator_channel=aggregator_channel,
            delivery_provider=delivery_provider,
        )
        return cls(**data) if data else None
