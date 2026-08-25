"""
Both order lists carry the direct-cost column, or neither should claim to.

The console's orders screen reads the shop's margins from `cost_cover` /
`covers_direct_cost`, and its response model documents those as "Computed in SQL
by `order_service.get_all_admin`". They were not: `get_user_orders` selected the
economics columns and `get_all_admin` selected only `item_count`, so every row
on the admin screen — the one that number exists for — fell back to the `None`
default and rendered a dash, including on orders well past the bar.

The fix routed both lists through one pair of helpers. These tests pin that:
each list builder must select the economics columns and apply them, so the two
cannot drift apart again without a red test saying so.
"""

from __future__ import annotations

import inspect

from app.services.orders import order_service


def test_the_admin_list_computes_the_direct_cost_column():
    """The regression itself: the console list must fill the margin column."""
    source = inspect.getsource(order_service.get_all_admin)
    assert "_economics_columns()" in source, (
        "get_all_admin must select net_value/cost_cover, or the console shows a "
        "dash on every row while its schema promises the number"
    )
    assert "_apply_economics(" in source, (
        "the selected columns must be written onto the row"
    )


def test_the_account_list_computes_the_same_column():
    """The list the fix was ported *from* must keep using the shared helper."""
    source = inspect.getsource(order_service.get_user_orders)
    assert "_economics_columns()" in source
    assert "_apply_economics(" in source
