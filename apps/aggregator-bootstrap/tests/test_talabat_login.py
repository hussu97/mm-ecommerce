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
        self.jar: list[dict] = []
        self.added: list[dict] = []

    async def new_page(self):
        return self.page

    async def clear_cookies(self, *, domain=None):
        self.cleared.append(domain)
        self.jar = [c for c in self.jar if domain is not None and c["domain"] != domain]

    async def cookies(self, _url=None):
        return list(self.jar)

    async def add_cookies(self, cookies):
        self.added.extend(cookies)
        self.jar.extend(cookies)


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


_DEVICE = {
    "name": "deviceToken",
    "value": "eyJ.trusted.device",
    "domain": "partner-app.talabat.com",
    "path": "/",
    "expires": -1,
    "httpOnly": False,
    "secure": True,
    "sameSite": "Lax",
}


async def test_the_wipe_keeps_the_trusted_device_cookie(portal, monkeypatch, tmp_path):
    # 2026-11-05: wiping it made the fresh login a "new device", so Talabat
    # asked for a code (by SMS to the owner) on every hourly retry.
    monkeypatch.setattr(login.settings, "STORAGE_STATE_DIR", str(tmp_path))
    page, ctx, state = portal
    state["fresh"] = False
    ctx.jar = [dict(_DEVICE), {**_DEVICE, "name": "accessToken", "value": "dead"}]

    async def is_authenticated(_page):
        return not ctx.cleared

    monkeypatch.setattr(login, "_talabat_is_authenticated_app", is_authenticated)
    with pytest.raises(login.LoginError, match="credential inputs"):
        await login.login_talabat(ctx, email="a@b.c", password="pw", page=page)
    names = [c["name"] for c in ctx.jar]
    assert names == ["deviceToken"]  # the dead session is gone, the marker is not
    assert ctx.jar[0]["value"] == _DEVICE["value"]


async def test_a_profile_without_it_gets_the_saved_one_back(monkeypatch, tmp_path):
    import json

    (tmp_path / "talabat.session.json").write_text(
        json.dumps({"cookies": [dict(_DEVICE), {**_DEVICE, "name": "_ga"}]})
    )
    monkeypatch.setattr(login.settings, "STORAGE_STATE_DIR", str(tmp_path))
    ctx = _Context(_Page())
    await login._ensure_talabat_trusted_device(ctx)
    assert [c["name"] for c in ctx.added] == ["deviceToken"]
    assert set(ctx.added[0]) <= set(login._COOKIE_FIELDS)


async def test_a_profile_that_has_it_is_left_alone(monkeypatch, tmp_path):
    monkeypatch.setattr(login.settings, "STORAGE_STATE_DIR", str(tmp_path))
    ctx = _Context(_Page())
    ctx.jar = [dict(_DEVICE)]
    await login._ensure_talabat_trusted_device(ctx)
    assert ctx.added == []


async def test_nothing_saved_means_nothing_restored(monkeypatch, tmp_path):
    monkeypatch.setattr(login.settings, "STORAGE_STATE_DIR", str(tmp_path))
    ctx = _Context(_Page())
    await login._ensure_talabat_trusted_device(ctx)  # never raises
    assert ctx.added == []
