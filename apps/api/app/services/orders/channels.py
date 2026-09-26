"""
How each sales channel (`orders.source`) behaves, in one table.

The lifecycle, the mailer and the reports used to ask `source == "online"`
inline, each for its own reason, and a new channel meant finding every one of
them. The questions that differ between channels are named here instead, once
per channel, and the gates read the answer:

* **cashier** — rung up and settled at a till. No courier, no customer email.
* **online** — the storefront. MM books the courier on its own, holds the card
  money (and refunds it on a cancellation), and emails the customer at every
  step.
* **aggregator** — a marketplace order. The marketplace's rider carries it and
  the marketplace talks to the customer.
* **custom** — a bespoke cake the shop takes itself. MM books a courier only
  when an admin chooses one, never holds card money (payment is taken off the
  system), consumes stock when it is **packed** rather than when it is
  confirmed (it is taken days before it is made, and its recipe is only
  certain once it is boxed), and tells the customer nothing until a courier we
  booked has it.

Pure data with no service imports, so anything — including the modules that
sit underneath `order_service` — can read it without an import cycle.
"""

from __future__ import annotations

from dataclasses import dataclass

from app.models.order import OrderStatusEnum
from app.models.pos_order import OrderSourceEnum

#: The customer emails a custom order may earn — news about a courier we booked
#: carrying it, and nothing before. Keyed on template names, not statuses:
#: `email_service.repair_after_reassignment` sends a packed email without a
#: status change, and a gate on status would not see it.
_COURIER_NEWS_TEMPLATES = frozenset(
    {
        "order_out_for_delivery.html",
        "order_delivered.html",
        "order_undelivered.html",
    }
)


@dataclass(frozen=True)
class ChannelPolicy:
    source: str
    #: Confirmation schedules the order's arrival at the register, arrival
    #: publishes it there, and packing books a courier — all without a person.
    books_courier_automatically: bool
    #: A booking MM made is MM's to call off when the order is cancelled.
    manages_courier: bool
    #: A cancellation refunds the card through our gateway.
    auto_refunds_card: bool
    #: The status whose arrival posts the order's recipe consumption.
    consumes_stock_at: OrderStatusEnum
    #: The customer emails this channel may send; None means all of them.
    customer_email_templates: frozenset[str] | None
    #: Customer emails additionally need a courier we booked to be carrying the
    #: order (a third-party driver or a collection earns none).
    customer_emails_need_booked_courier: bool = False
    #: Whether the shop owners are emailed when an order is placed.
    notifies_owner_on_order: bool = True


_POLICIES: dict[str, ChannelPolicy] = {
    OrderSourceEnum.CASHIER.value: ChannelPolicy(
        source=OrderSourceEnum.CASHIER.value,
        books_courier_automatically=False,
        manages_courier=False,
        auto_refunds_card=False,
        consumes_stock_at=OrderStatusEnum.CONFIRMED,
        customer_email_templates=frozenset(),
        notifies_owner_on_order=False,
    ),
    OrderSourceEnum.ONLINE.value: ChannelPolicy(
        source=OrderSourceEnum.ONLINE.value,
        books_courier_automatically=True,
        manages_courier=True,
        auto_refunds_card=True,
        consumes_stock_at=OrderStatusEnum.CONFIRMED,
        customer_email_templates=None,
    ),
    OrderSourceEnum.AGGREGATOR.value: ChannelPolicy(
        source=OrderSourceEnum.AGGREGATOR.value,
        books_courier_automatically=False,
        manages_courier=False,
        auto_refunds_card=False,
        consumes_stock_at=OrderStatusEnum.CONFIRMED,
        customer_email_templates=frozenset(),
        notifies_owner_on_order=False,
    ),
    OrderSourceEnum.CUSTOM.value: ChannelPolicy(
        source=OrderSourceEnum.CUSTOM.value,
        books_courier_automatically=False,
        manages_courier=True,
        auto_refunds_card=False,
        consumes_stock_at=OrderStatusEnum.PACKED,
        customer_email_templates=_COURIER_NEWS_TEMPLATES,
        customer_emails_need_booked_courier=True,
        # The shop took it; there is nobody to tell.
        notifies_owner_on_order=False,
    ),
}

#: What an order with no recognised source gets — only reachable from an
#: in-memory order a test builds (`orders.source` has a CHECK since migration
#: 294). Matches what the inline gates did before this module: no courier or
#: refund machinery, and every email.
_UNKNOWN = ChannelPolicy(
    source="",
    books_courier_automatically=False,
    manages_courier=False,
    auto_refunds_card=False,
    consumes_stock_at=OrderStatusEnum.CONFIRMED,
    customer_email_templates=None,
)


def policy_for(source: str | None) -> ChannelPolicy:
    """The policy for an `orders.source` value (enum member or string)."""
    key = getattr(source, "value", source)
    return _POLICIES.get(key, _UNKNOWN) if isinstance(key, str) else _UNKNOWN


def is_custom(source: str | None) -> bool:
    return getattr(source, "value", source) == OrderSourceEnum.CUSTOM.value
