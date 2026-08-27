"""The pure capture mapping — no Playwright, asserting each channel's shape."""

from __future__ import annotations

from aggregator_bootstrap.session_capture import (
    build_session,
    bundle_browser_state,
    earliest_token_expiry,
    jwt_expiry,
    split_browser_state,
)


def test_careem_puts_authorization_in_the_header_profile():
    cookies = [{"name": "session-token", "value": "abc"}, {"name": "_ga", "value": "x"}]
    headers = {
        "user-agent": "Chrome/151",
        "authorization": "Bearer tok",
        "application": "web",
        "uuid": "u1",
        "meta": "m",
        "time-zone": "Asia/Dubai",
    }
    s = build_session("careem", cookies, headers)
    assert s["cookies"]["session-token"] == "abc"
    # Careem replays Authorization from the profile, so it must be there.
    assert s["header_profile"]["authorization"] == "Bearer tok"
    assert s["header_profile"]["application"] == "web"
    assert "user-agent" in s["header_profile"]


def test_talabat_lifts_accesstoken_from_the_cookie():
    cookies = [
        {"name": "accessToken", "value": "jwt.jwt.jwt"},
        {"name": "_px3", "value": "p"},
    ]
    headers = {
        "x-global-entity-id": "tb_ae",
        "authorization": "Bearer z",
        "user-agent": "C",
    }
    s = build_session("talabat", cookies, headers)
    assert s["tokens"]["accessToken"] == "jwt.jwt.jwt"
    assert s["tokens"]["authorization"] == "Bearer z"
    assert s["header_profile"]["x-global-entity-id"] == "tb_ae"
    assert s["cookies"]["_px3"] == "p"  # the anti-bot cookie is carried


def test_noon_lifts_restaurant_code_and_project_from_headers():
    cookies = [{"name": "bm_sv", "value": "c"}]
    headers = {
        "n-restaurantcode": "MLTNGM1GBF",
        "x-project": "restaurant",
        "x-locale": "en-ae",
        "user-agent": "C",
    }
    s = build_session("noon", cookies, headers)
    assert s["tokens"]["restaurant_code"] == "MLTNGM1GBF"
    assert s["tokens"]["project"] == "restaurant"
    assert s["header_profile"]["n-restaurantcode"] == "MLTNGM1GBF"
    assert s["cookies"]["bm_sv"] == "c"


def test_deliveroo_lifts_token_cookie():
    cookies = [{"name": "token", "value": "dl-jwt"}]
    headers = {"user-agent": "C"}
    s = build_session("deliveroo", cookies, headers)
    assert s["tokens"]["token"] == "dl-jwt"
    assert s["cookies"]["token"] == "dl-jwt"


def test_build_session_bundles_storage_state_and_harvests_refresh():
    cookies = [
        {"name": "token", "value": "dl-jwt"},
        {"name": "refreshToken", "value": "rt-1"},
    ]
    state = {
        "cookies": cookies,
        "origins": [
            {
                "origin": "https://partner-hub.deliveroo.com",
                "localStorage": [{"name": "refresh_token", "value": "rt-ls"}],
            }
        ],
    }
    s = build_session(
        "deliveroo",
        cookies,
        {"user-agent": "C"},
        playwright_state=state,
        session_storage={"SHOP_IDS": "[1]"},
        origin="https://partner-hub.deliveroo.com",
    )
    assert s["tokens"]["refreshToken"] == "rt-1"
    assert s["tokens"]["refresh_token"] == "rt-ls"
    assert s["storage_state"]["playwright"]["cookies"][0]["name"] == "token"
    assert (
        s["storage_state"]["session_storage"]["https://partner-hub.deliveroo.com"][
            "SHOP_IDS"
        ]
        == "[1]"
    )


def test_jwt_expiry_reads_exp_and_ignores_garbage():
    import base64
    import json

    payload = (
        base64.urlsafe_b64encode(json.dumps({"exp": 2_000_000_000}).encode())
        .decode()
        .rstrip("=")
    )
    token = f"aaa.{payload}.bbb"
    exp = jwt_expiry(token)
    assert exp is not None
    assert exp.year == 2033
    assert jwt_expiry("not-a-jwt") is None
    assert earliest_token_expiry({"a": token}) is not None


def test_bundle_round_trips():
    bundled = bundle_browser_state(
        {"cookies": [], "origins": []},
        session_storage={"k": "v"},
        origin="https://example.com",
    )
    playwright, extra = split_browser_state(bundled)
    assert extra["https://example.com"]["k"] == "v"
    assert playwright["cookies"] == []
