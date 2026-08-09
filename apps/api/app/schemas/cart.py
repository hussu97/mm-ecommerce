from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class SelectedOption(BaseModel):
    modifier_id: UUID
    option_id: UUID
    #: How many of this option. A mixed box is two Ferrero and one Fudge, and
    #: saying so once is clearer than sending the option twice — but a client
    #: that still repeats it gets the same basket, because the server sums
    #: repeats before applying the group's limits.
    quantity: int = Field(default=1, ge=1, le=99)


#: A ceiling on what any product may ask for, independent of how a product is
#: configured. `personalisation_max_length` is the real limit and is checked
#: against the product itself; this only stops a hostile client posting a
#: megabyte before that check can run.
PERSONALISATION_HARD_LIMIT = 500


class CartItemCreate(BaseModel):
    product_id: UUID
    quantity: int = Field(ge=1, le=99, default=1)
    selected_options: list[SelectedOption] = Field(default_factory=list)
    #: What to write, for a product that asks. Rejected on one that does not.
    personalisation_note: str | None = Field(
        default=None, max_length=PERSONALISATION_HARD_LIMIT
    )


class CartItemUpdate(BaseModel):
    quantity: int = Field(ge=1, le=99)


class CartItemNoteUpdate(BaseModel):
    """
    Its own request, and its own endpoint, rather than a field on
    `CartItemUpdate`.

    A note is edited by typing, which means the save is debounced. A debounced
    save carrying a quantity it read before the customer touched the stepper
    will eventually put the old quantity back — a bug that only appears when
    somebody types and re-counts in the same second, and is invisible in
    testing. Sending only the note makes that unrepresentable.
    """

    note: str = Field(max_length=PERSONALISATION_HARD_LIMIT)


class CartItemResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    cart_id: UUID
    product_id: UUID
    quantity: int
    selected_options: list[Any] = []
    personalisation_note: str | None = None
    created_at: datetime

    # Computed fields (populated by service layer)
    product_name: str | None = None
    product_image: str | None = None
    product_translations: dict[str, Any] | None = None
    unit_price: float | None = None
    line_total: float | None = None
    #: The product's personalisation config, carried on the line so the cart page
    #: can render the field and its counter without fetching every product again.
    personalisation_type: str | None = None
    personalisation_max_length: int | None = None


class CartResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    user_id: UUID | None
    session_id: str | None
    items: list[CartItemResponse] = []

    # Computed fields
    item_count: int = 0
    subtotal: float = 0.0
