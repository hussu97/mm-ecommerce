"""login_talabat on a profile that only *looks* logged in."""

from __future__ import annotations

import pytest

from aggregator_bootstrap.channels import login


class _Locator:
    async def wait_for(self, **_kw):
        raise TimeoutError("no credential inputs")


class _Page:
    url = "https://partner-app.talabat.com/dashboard"

    def __init__(self) -> None:
        self.gotos: list[str] = []
        self.evaluated: list[str] = []

    async def goto(self, url, **_kw):
        self.gotos.append(url)

    async def wait_for_timeout(self, _ms):
        return None

    async def evaluate(self, script):
        self.evaluated.append(script)

    def locator(self, _selector):
        return _Locator()


class _Context:
    def __init__(self, page: _Page) -> None:
        self.page = page
        self.cleared: list[str | None] = []

    async def new_page(self):
        return self.page

    async def clear_cookies(self, *, domain=None):
        self.cleared.append(domain)


@pytest.fixture
def portal(monkeypatch):
    page = _Page()
    ctx = _Context(page)
    state = {"authenticated": True, "fresh": True}

    async def is_authenticated(_page):
        return state["authenticated"]

    async def ensure_fresh(_page):
        return state["fresh"]

    async def nothing(*_a, **_kw):
        return None

    async def no_wall(_page):
        return False

    monkeypatch.setattr(login, "_talabat_is_authenticated_app", is_authenticated)
    monkeypatch.setattr(login, "_ensure_fresh_talabat_token", ensure_fresh)
    monkeypatch.setattr(login, "_dismiss_talabat_cookie_banner", nothing)
    monkeypatch.setattr(login, "_talabat_debug_shot", nothing)
    monkeypatch.setattr(login, "_talabat_human_verification_present", no_wall)
    return page, ctx, state


async def test_a_fresh_profile_is_returned_as_is(portal):
    page, ctx, _ = portal
    got = await login.login_talabat(ctx, email="a@b.c", password="pw", page=page)
    assert got is page
    assert ctx.cleared == []


async def test_an_unrefreshable_token_forces_a_real_login(portal, monkeypatch):
    # The SPA renders from persisted cookies but cannot mint a bearer: the
    # profile is wiped and the credential flow runs instead of pushing a dead
    # token as `live`.
    page, ctx, state = portal
    state["fresh"] = False

    async def is_authenticated(_page):
        # Logged in on first load; after the wipe, the login form.
        return not ctx.cleared

    monkeypatch.setattr(login, "_talabat_is_authenticated_app", is_authenticated)
    with pytest.raises(login.LoginError, match="credential inputs"):
        await login.login_talabat(ctx, email="a@b.c", password="pw", page=page)
    assert "partner-app.talabat.com" in ctx.cleared
    assert page.evaluated  # web storage cleared too
    assert page.gotos == [login.TALABAT_LOGIN_URL, login.TALABAT_LOGIN_URL]
