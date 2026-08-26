"""The Keeta in-page pull, with `page.evaluate` mocked (no browser, no network).

Two things are proven here: (1) `fetch_keeta_orders` drives the in-page fetch and
returns the raw `getOrders` payloads unchanged, and (2) those payloads satisfy the
parse contract — the mm-ecommerce `keeta_provider.parse_orders` turns the sample
into orders with line items, so the shape the worker pushes is the shape the API
expects.
"""

from __future__ import annotations

import sys
from pathlib import Path

# The mm-ecommerce API package lives two levels up (apps/api); it holds the
# provider whose parse contract we assert against. Added to the path so `app`
# imports when the tests run under the api venv.
_API_ROOT = Path(__file__).resolve().parents[2] / "api"

from aggregator_bootstrap.keeta_pull import fetch_keeta_orders  # noqa: E402

# A realistic getOrders response: `data.list[]` of the Keeta envelope
# (baseOrder / merchantOrder / products / feeDtl.merchantFee) that parse_orders
# flattens. totalCount == 1 so pagination stops after the first page.
SAMPLE_GET_ORDERS = {
    "code": 0,
    "data": {
        "totalCount": 1,
        "list": [
            {
                "baseOrder": {
                    "orderViewId": "70012345",
                    "status": "completed",
                    "orderCreateTime": 1_723_363_200_000,
                },
                "merchantOrder": {"shopId": "123", "orderAmount": 4500},
                "products": [
                    {
                        "name": "Chocolate Cake",
                        "count": 1,
                        "price": 4500,
                        "skuId": "sku-1",
                    }
                ],
                "feeDtl": {"merchantFee": {"commission": 675, "total": 3825}},
            }
        ],
    },
}


class _FakePage:
    """Stands in for a Playwright page: evaluate returns SHOP_IDS then payloads."""

    def __init__(self, payload: dict, shop_ids: list[str]) -> None:
        self._payload = payload
        self._shop_ids = shop_ids
        self.evaluate_calls: list[tuple[str, object]] = []
        self.closed = False

    async def goto(self, *args, **kwargs) -> None:
        return None

    async def wait_for_timeout(self, *args, **kwargs) -> None:
        return None

    async def evaluate(self, script: str, arg: object = None):
        self.evaluate_calls.append((script, arg))
        if "SHOP_IDS" in script:
            return list(self._shop_ids)
        return self._payload

    async def close(self) -> None:
        self.closed = True


class _FakeContext:
    """Stands in for a Playwright browser context."""

    def __init__(self, page: _FakePage) -> None:
        self._page = page

    async def new_page(self) -> _FakePage:
        return self._page


async def test_fetch_keeta_orders_returns_raw_payloads_via_page_evaluate():
    page = _FakePage(SAMPLE_GET_ORDERS, ["123"])
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)

    # One shop, one (current-month) window, one page -> exactly one raw payload,
    # handed back untouched.
    assert payloads == [SAMPLE_GET_ORDERS]
    assert payloads[0] is SAMPLE_GET_ORDERS
    # The order fetch went through the in-page `fetch` (page.evaluate with an
    # endpoint/payload arg), not any out-of-page HTTP client.
    assert any(
        arg and isinstance(arg, dict) and arg.get("endpoint", "").endswith("getOrders")
        for _, arg in page.evaluate_calls
    )
    assert page.closed


async def test_no_shop_ids_still_makes_one_combined_call():
    page = _FakePage(SAMPLE_GET_ORDERS, [])
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)
    # With SHOP_IDS empty we still attempt one call (shopIds: []).
    assert payloads == [SAMPLE_GET_ORDERS]


def _load_keeta_provider():
    if str(_API_ROOT) not in sys.path:
        sys.path.insert(0, str(_API_ROOT))
    from app.services.providers import keeta_provider

    return keeta_provider


async def test_fetched_payload_matches_keeta_provider_parse_contract():
    page = _FakePage(SAMPLE_GET_ORDERS, ["123"])
    payloads = await fetch_keeta_orders(_FakeContext(page), months_back=0)

    keeta_provider = _load_keeta_provider()
    orders = keeta_provider.provider.parse_orders(payloads[0])

    assert orders, "sample getOrders payload should parse into at least one order"
    order = orders[0]
    assert order.external_order_id == "70012345"
    assert order.external_outlet_id == "123"
    assert order.items and order.items[0].item_name == "Chocolate Cake"
