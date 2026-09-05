"""Careem headed login waits for a real reCAPTCHA-v3 token; v2 is solved in-process.

v3 is a score on a headed Chrome. A visible v2 checkbox/image/audio puzzle is
clicked and solved (audio transcription, or 2captcha when keyed) — never waited
out for 45 minutes, never a mocked grecaptcha / siteverify.
"""

from __future__ import annotations

import asyncio
import inspect

import pytest

from aggregator_bootstrap.channels import login as L


class _Mouse:
    def __init__(self):
        self.moves: list[tuple[int, int]] = []
        self.clicks: list[tuple[int, int]] = []

    async def move(self, x, y, steps=1):
        self.moves.append((x, y))

    async def click(self, x, y):
        self.clicks.append((x, y))


class _Body:
    def __init__(self, text: str = "Sign in with email"):
        self._text = text

    async def inner_text(self, timeout=0):
        return self._text


class _GrecaptchaPage:
    url = "https://auth.careem.com/login"

    def __init__(self, present: bool = True):
        self.present = present
        self.waited: list[str] = []
        self.evals: list[str] = []

    async def wait_for_function(self, script, timeout=None):
        self.waited.append(script)
        if not self.present:
            raise TimeoutError("grecaptcha missing")

    async def evaluate(self, script, arg=None):
        self.evals.append(script)
        return True


class _TokenPage:
    url = "https://auth.careem.com/login"

    def __init__(
        self,
        *,
        token: str = "",
        has_g: bool = True,
        sitekey: str = "6Lxxxxxxxxxxxxxxxxxxxx",
        action: str = "login",
        exec_token: str = "",
        body: str = "Sign in with email",
        challenge: bool = False,
    ):
        self.token = token
        self.has_g = has_g
        self.sitekey = sitekey
        self.action = action
        self.exec_token = exec_token
        self.exec_args: list = []
        self.body = body
        self.challenge = challenge
        self.mouse = _Mouse()

    async def evaluate(self, script, arg=None):
        if "info.action" in script or "async (info)" in script:
            self.exec_args.append(arg)
            if self.exec_token:
                self.token = self.exec_token
            return self.exec_token
        if "tokenLen" in script:
            return {
                "token": self.token,
                "tokenLen": len(self.token),
                "hasGrecaptcha": self.has_g,
                "enterprise": False,
                "buttonEnabled": False,
            }
        if "data-sitekey" in script:
            return {"sitekey": self.sitekey, "action": self.action}
        if "bframe" in script:
            return {
                "v2": self.challenge,
                "checkbox": self.challenge,
                "iframes": (
                    [
                        {
                            "id": "recaptcha",
                            "src": "https://www.google.com/recaptcha/api2/bframe",
                            "title": "recaptcha challenge",
                        }
                    ]
                    if self.challenge
                    else []
                ),
            }
        return True

    def locator(self, _sel):
        return _Body(self.body)

    async def wait_for_timeout(self, _ms):
        return None

    async def screenshot(self, **_k):
        return None


class _TypeLoc:
    def __init__(self):
        self.seq = None
        self.filled: list[str] = []

    async def click(self, **_k):
        return None

    async def fill(self, value):
        self.filled.append(value)

    async def press_sequentially(self, value, delay=0):
        self.seq = (value, delay)


class _SubmitPage:
    url = "https://auth.careem.com/login"

    def __init__(self):
        self.clicked = False
        self.paused: list[int] = []

    def get_by_role(self, *_a, **_k):
        return self

    async def wait_for(self, **_k):
        return None

    async def click(self, **_k):
        self.clicked = True

    async def wait_for_timeout(self, ms):
        self.paused.append(ms)


# ── pure classifiers ──────────────────────────────────────────────────────────


def test_token_is_present_requires_a_real_string():
    assert L._careem_token_is_present({"token": "a" * 20})
    assert not L._careem_token_is_present({"token": ""})
    assert not L._careem_token_is_present({"token": "short"})
    assert not L._careem_token_is_present(None)


def test_challenge_ui_detects_im_not_a_robot_and_unusual_traffic():
    assert L._careem_challenge_ui_from(None, "I'm not a robot")
    assert L._careem_challenge_ui_from(None, "Unusual traffic from your computer")
    assert L._careem_challenge_ui_from(None, "Select all images with traffic lights")


def test_challenge_ui_detects_iframe_id_recaptcha_and_bframe():
    assert L._careem_challenge_ui_from(
        {"iframes": [{"id": "recaptcha", "src": "", "title": ""}]}, ""
    )
    assert L._careem_challenge_ui_from(
        {
            "v2": True,
            "checkbox": False,
            "iframes": [
                {
                    "id": "",
                    "src": "https://www.google.com/recaptcha/api2/bframe?x=1",
                    "title": "recaptcha challenge",
                }
            ],
        },
        "",
    )


def test_challenge_ui_ignores_the_v3_badge_iframe():
    """v3 injects an invisible anchor iframe; that is not a puzzle to wait out."""
    probe = {
        "v2": False,
        "checkbox": False,
        "iframes": [
            {
                "id": "",
                "src": "https://www.google.com/recaptcha/api2/anchor?size=invisible",
                "title": "",
            }
        ],
    }
    assert not L._careem_challenge_ui_from(probe, "Sign in with email")


def test_score_failure_markers_do_not_trip_on_the_email_form():
    assert L._careem_looks_like_score_failure("Unusual traffic from your computer")
    assert L._careem_looks_like_score_failure("Please try again")
    assert not L._careem_looks_like_score_failure("Enter your email address")


# ── grecaptcha.ready wait ─────────────────────────────────────────────────────


def test_wait_for_grecaptcha_uses_wait_for_function():
    page = _GrecaptchaPage(present=True)
    assert asyncio.run(L._careem_wait_for_grecaptcha(page)) is True
    assert page.waited == [L._CAREEM_GRECAPTCHA_PRESENT_JS]
    assert any("api.ready" in s for s in page.evals)


def test_wait_for_grecaptcha_is_false_when_the_widget_never_loads():
    page = _GrecaptchaPage(present=False)
    assert asyncio.run(L._careem_wait_for_grecaptcha(page)) is False


def test_present_js_covers_enterprise():
    assert "g.enterprise" in L._CAREEM_GRECAPTCHA_PRESENT_JS
    assert "api.ready" in L._CAREEM_GRECAPTCHA_PRESENT_JS


# ── token wait / execute ──────────────────────────────────────────────────────


def test_wait_for_token_returns_immediately_when_the_textarea_is_filled():
    page = _TokenPage(token="x" * 40)
    assert asyncio.run(L._careem_wait_for_token(page)) is True
    assert page.exec_args == []


def test_wait_for_token_skips_when_this_step_has_no_widget():
    """OTP/password must not burn 12s waiting for a v3 token that isn't there."""
    page = _TokenPage(has_g=False, token="")
    assert asyncio.run(L._careem_wait_for_token(page)) is False
    assert page.exec_args == []


def test_wait_for_token_executes_with_the_page_action():
    page = _TokenPage(token="", action="signin", exec_token="y" * 40)
    assert asyncio.run(L._careem_wait_for_token(page)) is True
    assert page.exec_args[0]["sitekey"].startswith("6L")
    assert page.exec_args[0]["action"] == "signin"


def test_wait_for_token_does_not_guess_login_when_the_page_has_no_action():
    """A wrong action tanks the score. Empty means execute(sitekey) only."""
    page = _TokenPage(token="", action="", exec_token="z" * 40)
    asyncio.run(L._careem_wait_for_token(page))
    assert page.exec_args[0]["action"] == ""


def test_execute_js_uses_info_action_and_does_not_hardcode_login():
    assert "info.action" in L._CAREEM_EXECUTE_JS
    assert "action: 'login'" not in L._CAREEM_EXECUTE_JS
    assert "action: \"login\"" not in L._CAREEM_EXECUTE_JS


# ── human-like input ──────────────────────────────────────────────────────────


def test_type_human_uses_a_per_key_delay():
    loc = _TypeLoc()
    asyncio.run(L._careem_type_human(loc, "a@b.com"))
    assert loc.seq == ("a@b.com", L._CAREEM_TYPE_DELAY_MS)
    assert loc.filled == [""]  # cleared before typing


def test_dwells_fit_inside_the_relogin_budget():
    """Two email attempts (first + score retry) of dwell+ready+token+pause
    must leave room for the 120s OTP mailbox wait inside 480s."""
    extra = (
        2
        * (
            L._CAREEM_NAV_DWELL_MS
            + L._CAREEM_RECAPTCHA_READY_MS
            + L._CAREEM_TOKEN_WAIT_MS
            + L._CAREEM_PRE_SUBMIT_PAUSE_MS
        )
        / 1000
    )
    assert extra < 120
    assert L._CAREEM_NAV_DWELL_MS >= 3_000
    assert 60 <= L._CAREEM_CHALLENGE_SOLVE_SECONDS <= 90


# ── submit waits for a token ──────────────────────────────────────────────────


def test_careem_submit_waits_for_a_token_before_clicking(monkeypatch):
    calls: list[str] = []

    async def fake_token(_page):
        calls.append("token")
        return True

    async def fake_chal(_page):
        return False

    monkeypatch.setattr(L, "_careem_wait_for_token", fake_token)
    monkeypatch.setattr(L, "_careem_challenge_ui_present", fake_chal)
    page = _SubmitPage()
    asyncio.run(L._careem_submit(page, object()))
    assert calls == ["token"]
    assert page.clicked
    assert L._CAREEM_PRE_SUBMIT_PAUSE_MS in page.paused


def test_careem_submit_no_longer_races_with_a_flat_1_5s():
    src = inspect.getsource(L._careem_submit)
    assert "_careem_wait_for_token" in src
    assert "wait_for_timeout(1_500)" not in src


# ── v2 checkbox + in-process solver ───────────────────────────────────────────


class _CheckboxPage:
    url = "https://auth.careem.com/login"

    def __init__(self):
        self.mouse = _Mouse()
        self.selector = None
        self.inner = None

    def frame_locator(self, sel):
        self.selector = sel
        return self

    def locator(self, inner):
        self.inner = inner
        return self

    async def bounding_box(self):
        return {"x": 100.0, "y": 200.0, "width": 28.0, "height": 28.0}

    async def click(self, **_k):
        return None

    async def wait_for_timeout(self, _ms):
        return None


def _patch_wait_out(monkeypatch, tmp_path, *, click, solver, cleared):
    monkeypatch.setattr(L.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(L, "_CAREEM_CHECKBOX_GRACE_MS", 0)
    monkeypatch.setattr(L, "_careem_click_recaptcha_checkbox", click)
    monkeypatch.setattr(L, "_careem_invoke_v2_solver", solver)
    monkeypatch.setattr(L, "_careem_challenge_cleared", cleared)


def test_checkbox_click_uses_human_like_mouse():
    page = _CheckboxPage()
    assert asyncio.run(L._careem_click_recaptcha_checkbox(page)) is True
    assert page.inner == "#recaptcha-anchor"
    assert page.mouse.moves
    assert page.mouse.clicks


def test_checkbox_click_is_false_without_a_frame():
    page = _TokenPage(challenge=True)
    assert asyncio.run(L._careem_click_recaptcha_checkbox(page)) is False


def test_wait_out_challenge_skips_solver_when_checkbox_clears_it(
    monkeypatch, tmp_path
):
    order: list[str] = []

    async def _click(_p):
        order.append("click")
        return True

    async def _solver(_p):
        order.append("solver")
        return "t" * 40

    async def _cleared(_p):
        return "click" in order

    _patch_wait_out(
        monkeypatch, tmp_path, click=_click, solver=_solver, cleared=_cleared
    )
    page = _TokenPage(challenge=True, body="I'm not a robot")
    assert asyncio.run(L._careem_wait_out_challenge(page)) is True
    assert order == ["click"]


def test_wait_out_challenge_invokes_solver_when_checkbox_is_not_enough(
    monkeypatch, tmp_path
):
    """Daemon auto path: first sight of a v2 iframe must solve, not NeedsHumanLogin."""
    order: list[str] = []
    n = {"c": 0}

    async def _click(_p):
        order.append("click")
        return True

    async def _solver(_p):
        order.append("solver")
        return "t" * 40

    async def _cleared(_p):
        n["c"] += 1
        return n["c"] > 1

    _patch_wait_out(
        monkeypatch, tmp_path, click=_click, solver=_solver, cleared=_cleared
    )
    page = _TokenPage(challenge=True, body="I'm not a robot")
    assert asyncio.run(L._careem_wait_out_challenge(page)) is True
    assert order == ["click", "solver"]
    assert "NeedsHumanLogin" not in inspect.getsource(L._careem_wait_out_challenge)
    assert "raise NeedsHumanLogin" not in inspect.getsource(L.login_careem)


def test_wait_out_challenge_raises_after_solver_fails(monkeypatch, tmp_path):
    async def _click(_p):
        return True

    async def _solver(_p):
        raise RuntimeError("audio failed")

    async def _cleared(_p):
        return False

    _patch_wait_out(
        monkeypatch, tmp_path, click=_click, solver=_solver, cleared=_cleared
    )
    page = _TokenPage(challenge=True, body="I'm not a robot")
    with pytest.raises(L.AntiBotChallengeError, match="could not be solved"):
        asyncio.run(L._careem_wait_out_challenge(page))


def test_challenge_solve_does_not_wait_45_minutes():
    src = inspect.getsource(L._careem_wait_out_challenge)
    assert "_careem_click_recaptcha_checkbox" in src
    assert "_careem_invoke_v2_solver" in src
    assert "45 * 60" not in src
    assert 60 <= L._CAREEM_CHALLENGE_SOLVE_SECONDS <= 90
    click_src = inspect.getsource(L._careem_click_recaptcha_checkbox)
    assert "rc-imageselect" not in click_src
    assert "recaptcha-anchor" in click_src


def test_invoke_solver_uses_twocaptcha_when_key_set(monkeypatch):
    monkeypatch.setattr(L.settings, "TWOCAPTCHA_API_KEY", "2c-secret")
    calls: list[str] = []

    async def fake_2c(_page, key):
        calls.append(key)
        return "t" * 40

    async def fake_inject(_page, token):
        calls.append(token)

    async def fake_audio(_page):
        raise AssertionError("audio path must not run when a key is set")

    monkeypatch.setattr(L, "_careem_twocaptcha_token", fake_2c)
    monkeypatch.setattr(L, "_careem_inject_recaptcha_token", fake_inject)
    monkeypatch.setattr(L, "_careem_playwright_recaptcha_audio", fake_audio)
    token = asyncio.run(L._careem_invoke_v2_solver(object()))
    assert token == "t" * 40
    assert calls == ["2c-secret", "t" * 40]


def test_invoke_solver_uses_audio_library_when_no_key(monkeypatch):
    monkeypatch.setattr(L.settings, "TWOCAPTCHA_API_KEY", "")

    async def fake_audio(_page):
        return "u" * 40

    async def fake_2c(_page, _key):
        raise AssertionError("2captcha must not run without a key")

    monkeypatch.setattr(L, "_careem_playwright_recaptcha_audio", fake_audio)
    monkeypatch.setattr(L, "_careem_twocaptcha_token", fake_2c)
    assert asyncio.run(L._careem_invoke_v2_solver(object())) == "u" * 40


def test_login_careem_retries_solve_failure_then_last_resort():
    src = inspect.getsource(L.login_careem)
    assert "AntiBotChallengeError" in src
    assert "raise NeedsHumanLogin" not in src
    assert "_careem_wait_out_challenge" in src


# ── wiring in login_careem ────────────────────────────────────────────────────


def test_login_careem_waits_for_grecaptcha_types_like_a_human_retries_score():
    src = inspect.getsource(L.login_careem)
    assert "_careem_wait_email_input" in src
    assert "_careem_submit_email" in src
    assert "recaptcha-score" in src
    sub = inspect.getsource(L._careem_submit_email)
    assert "_careem_wait_for_grecaptcha" in sub
    assert "_careem_type_human" in sub
    assert "_careem_dwell" in sub


def test_score_retry_reloads_the_email_step_once_not_a_tight_loop():
    src = inspect.getsource(L.login_careem)
    email = src.split("# auth.careem.com email step")[1].split("Verification-code")[0]
    assert email.count("recaptcha-score") == 1
    assert "page.reload" in email
    otp = src.split("Verification-code step")[1].split("Password step")[0]
    assert "page.reload" not in otp
