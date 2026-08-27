"""Lazy browser driver.

Imports stay lazy so unit tests do not need the browser package. Headed login
does not drive Chrome through this driver — it spawns Google Chrome as a
normal OS process and only connects over CDP after the operator is in.
"""

from __future__ import annotations

from typing import Any


def async_playwright():
    """Return Playwright's async playwright context manager."""
    try:
        from playwright.async_api import async_playwright as _factory
    except ImportError as exc:  # pragma: no cover — installed in the worker venv
        raise ImportError(
            "Playwright is required for aggregator session capture. "
            "Install with: uv add playwright"
        ) from exc
    return _factory()


async def evaluate_in_page(page: Any, expression: str, arg: Any = None) -> Any:
    """Run JS in the page. Extra kwargs (Patchright `isolated_context`) are optional."""
    kwargs: dict[str, Any] = {"isolated_context": False}
    try:
        if arg is None:
            return await page.evaluate(expression, **kwargs)
        return await page.evaluate(expression, arg, **kwargs)
    except TypeError:
        if arg is None:
            return await page.evaluate(expression)
        return await page.evaluate(expression, arg)
