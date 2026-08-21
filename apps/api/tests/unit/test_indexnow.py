"""
IndexNow: the key matches the file that proves it, and nothing it does can hurt.

The first of those is the only way this breaks silently. The key in the service
and the filename in `apps/web/public/` are two halves of the same fact, in two
different apps, and a mismatch is a 403 from the endpoint that nobody is
checking the return value of — so it would look exactly like working.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.services import indexnow_service as svc


def test_the_key_file_exists_and_matches_the_key():
    """
    `apps/web/public/{key}.txt` containing `{key}` is the whole ownership proof.

    Skipped where the storefront is not on disk — the API image ships without
    it. In the monorepo, which is where the two can drift, it runs.
    """
    public = Path(__file__).resolve().parents[3] / "web" / "public"
    if not public.is_dir():
        pytest.skip("storefront not on disk")

    key_file = public / f"{svc.INDEXNOW_KEY}.txt"
    assert key_file.is_file(), (
        f"{key_file.name} is missing. The key in indexnow_service and the file in "
        "apps/web/public are one fact in two places; changing the key means "
        "renaming the file in the same commit."
    )
    assert key_file.read_text().strip() == svc.INDEXNOW_KEY


def test_the_key_is_a_shape_the_protocol_accepts():
    # 8–128 characters, hex or hyphen. A key outside that is rejected before
    # anyone looks at the file.
    assert 8 <= len(svc.INDEXNOW_KEY) <= 128
    assert all(c in "0123456789abcdef-" for c in svc.INDEXNOW_KEY)


# ── URL shapes ───────────────────────────────────────────────────────────────


def test_urls_cover_both_languages():
    assert svc.category_urls("brownies") == ["/en/brownies", "/ar/brownies"]
    assert svc.product_urls("mix-boxes", "box-of-9") == [
        "/en/mix-boxes/box-of-9",
        "/ar/mix-boxes/box-of-9",
    ]
    assert svc.blog_urls("eggless") == ["/en/blog/eggless", "/ar/blog/eggless"]


# ── Submitting ───────────────────────────────────────────────────────────────


async def test_nothing_is_submitted_outside_production(monkeypatch):
    """
    A staging deploy announcing localhost to Bing is noise at best, and a 422
    against the real host at worst.
    """
    monkeypatch.setattr(settings, "APP_ENV", "development")
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
        assert await svc.submit(["/en/brownies"]) is False
    post.assert_not_called()


async def test_an_empty_list_is_not_a_request():
    with patch("httpx.AsyncClient.post", new=AsyncMock()) as post:
        assert await svc.submit([]) is False
    post.assert_not_called()


async def test_the_payload_names_the_host_the_key_file_is_on(monkeypatch):
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "WEB_URL", "https://meltingmomentscakes.com")

    captured: dict = {}

    async def fake_post(self, url, **kwargs):
        captured["url"] = url
        captured["json"] = kwargs["json"]

        class R:
            status_code = 200
            text = ""

        return R()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    assert await svc.submit(["/en/brownies", "/ar/brownies"]) is True

    assert captured["url"] == svc.ENDPOINT
    body = captured["json"]
    assert body["host"] == "meltingmomentscakes.com"
    assert body["key"] == svc.INDEXNOW_KEY
    # The endpoint fetches this to check the key. A host mismatch here is the
    # 403 the drift test above exists to prevent.
    assert body["keyLocation"] == (
        f"https://meltingmomentscakes.com/{svc.INDEXNOW_KEY}.txt"
    )
    # Relative in, absolute out — a relative URL in `urlList` is a 422.
    assert body["urlList"] == [
        "https://meltingmomentscakes.com/en/brownies",
        "https://meltingmomentscakes.com/ar/brownies",
    ]


@pytest.mark.parametrize("status", [200, 202])
async def test_accepted_statuses(monkeypatch, status):
    """202 is the normal answer while a new key is still being validated."""
    monkeypatch.setattr(settings, "APP_ENV", "production")

    async def fake_post(self, url, **kwargs):
        class R:
            status_code = status
            text = ""

        return R()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    assert await svc.submit(["/en/brownies"]) is True


@pytest.mark.parametrize("status", [400, 403, 422, 429, 500])
async def test_a_rejection_is_reported_not_raised(monkeypatch, status):
    monkeypatch.setattr(settings, "APP_ENV", "production")

    async def fake_post(self, url, **kwargs):
        class R:
            status_code = status
            text = "nope"

        return R()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)
    assert await svc.submit(["/en/brownies"]) is False


async def test_a_dead_endpoint_cannot_take_a_save_down(monkeypatch):
    """
    The callers are admin writes. Saving a product must not fail because a
    search engine did.
    """
    monkeypatch.setattr(settings, "APP_ENV", "production")

    async def explode(self, url, **kwargs):
        raise OSError("connection refused")

    monkeypatch.setattr("httpx.AsyncClient.post", explode)
    assert await svc.submit(["/en/brownies"]) is False


async def test_background_submission_is_awaited_by_something(monkeypatch):
    """
    `create_task` alone is not enough — the loop keeps only a weak reference, so
    a task nobody holds can be collected before it runs. The module keeps its
    own set; this is that set doing its job.
    """
    monkeypatch.setattr(settings, "APP_ENV", "production")
    calls: list[int] = []

    async def fake_post(self, url, **kwargs):
        calls.append(len(kwargs["json"]["urlList"]))

        class R:
            status_code = 200
            text = ""

        return R()

    monkeypatch.setattr("httpx.AsyncClient.post", fake_post)

    svc.submit_in_background(["/en/brownies"])
    assert svc._pending, "the task was not retained and can be collected mid-flight"

    import asyncio

    await asyncio.gather(*list(svc._pending))
    assert calls == [1]
    assert not svc._pending, "finished tasks must be discarded, or the set leaks"
