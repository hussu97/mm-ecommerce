"""The Careem email step waits for a client-rendered form, and leaves evidence.

Careem's `auth.careem.com` login document is a ~5 KB SPA shell — the email box
only exists once the bundle has booted. So the wait there races the box's ability
to run JavaScript, not the network. On the 2-vCPU production VM, with headed
Chrome pinned to one core by the worker's CPU cap, a flat 20s lost that race from
2026-09-02 12:17: 44 consecutive re-logins failed, each arming the needs-human
hour backoff, and Careem sat dead for two days with its sales uningested. Worse,
this was the one failure path in the flow that captured nothing — no screenshot,
no DOM — so all 44 attempts produced no diagnosis.
"""

from __future__ import annotations

import asyncio

import pytest

from aggregator_bootstrap.channels import login as L


class _Locator:
    """Becomes visible on the Nth wait_for; raises before that."""

    def __init__(self, ready_on_call: int):
        self.ready_on_call = ready_on_call
        self.calls = 0

    async def wait_for(self, **kwargs):
        self.calls += 1
        if self.calls < self.ready_on_call:
            raise TimeoutError("locator not visible")
        return None


# ── the readiness helper ────────────────────────────────────────────────────────


def test_email_input_ready_is_true_when_the_form_appears():
    assert asyncio.run(L._careem_email_input_ready(_Locator(ready_on_call=1)))


def test_email_input_ready_is_false_on_timeout_rather_than_raising():
    """A miss must be a boolean the caller can retry on, not an exception that
    aborts the login — the retry is the whole point."""
    assert not asyncio.run(L._careem_email_input_ready(_Locator(ready_on_call=99)))


def test_email_input_ready_swallows_a_strict_mode_violation():
    """Careem renders responsive variants. `wait_for` against a locator matching
    more than one element raises a Playwright strict-mode violation, which this
    flow used to report as the same opaque 'did not expose an email input' as a
    genuine miss. The caller passes `.first`; this must not re-raise either."""

    class _Strict:
        async def wait_for(self, **kwargs):
            raise RuntimeError("strict mode violation: resolved to 2 elements")

    assert not asyncio.run(L._careem_email_input_ready(_Strict()))


def test_the_step_budget_is_generous_enough_to_outlast_a_slow_spa_boot():
    """Pin the intent: seconds inside a 480s re-login budget are cheap; the 20s
    that shipped before cost two days of a dead channel."""
    assert L._CAREEM_EMAIL_STEP_SECONDS >= 45


# ── the evidence capture ────────────────────────────────────────────────────────


class _DumpPage:
    def __init__(self, evaluate_raises=False):
        self.evaluate_raises = evaluate_raises
        self.shots: list[str] = []

    async def evaluate(self, _script):
        if self.evaluate_raises:
            raise RuntimeError("execution context destroyed")
        return {"url": "https://auth.careem.com/login", "inputs": []}

    async def screenshot(self, **kwargs):
        self.shots.append(kwargs.get("path", ""))


def test_capture_failure_writes_a_screenshot_and_a_dom_dump(tmp_path, monkeypatch):
    monkeypatch.setattr(L.settings, "STORAGE_STATE_DIR", str(tmp_path))
    page = _DumpPage()
    asyncio.run(L._careem_capture_failure(page, "no-email-input"))
    assert page.shots, "a screenshot must be taken"
    dump = tmp_path / "careem-debug-no-email-input.json"
    assert dump.exists(), "the DOM dump is what distinguishes the failure modes"
    assert "auth.careem.com" in dump.read_text()


def test_capture_failure_never_raises_when_the_page_is_gone(tmp_path, monkeypatch):
    """Diagnostics must never break a login — a torn-down page is exactly when
    this runs."""
    monkeypatch.setattr(L.settings, "STORAGE_STATE_DIR", str(tmp_path))
    asyncio.run(L._careem_capture_failure(_DumpPage(evaluate_raises=True), "t"))


def test_capture_failure_survives_an_unwritable_dump_dir(monkeypatch):
    monkeypatch.setattr(L.settings, "STORAGE_STATE_DIR", "/nope/not/a/dir")
    asyncio.run(L._careem_capture_failure(_DumpPage(), "t"))


# ── the retry, end to end over the email step ──────────────────────────────────


class _FlowPage:
    """Enough page surface for `login_careem`'s email step."""

    def __init__(self, ready_on_call: int):
        self.url = "https://auth.careem.com/login?emailOnly=true"
        self.locator_obj = _Locator(ready_on_call)
        self.reloads = 0
        self.filled: list[str] = []
        self.shots: list[str] = []

    def locator(self, _sel):
        return self

    @property
    def first(self):
        return self.locator_obj

    async def reload(self, **kwargs):
        self.reloads += 1

    async def wait_for_timeout(self, _ms):
        return None

    async def fill(self, value):
        self.filled.append(value)

    async def evaluate(self, _s):
        return {"url": self.url, "inputs": []}

    async def screenshot(self, **kwargs):
        self.shots.append(kwargs.get("path", ""))


def _run_email_step(page, monkeypatch, tmp_path):
    """Replay the email step's composition against the fake page.

    This mirrors the step rather than calling `login_careem` (which would need a
    fake for the portal nav, the method chooser, the mailbox and the password
    step). It documents the intended behaviour; the test below pins that the real
    function is actually wired this way, so the mirror cannot drift silently.
    """
    monkeypatch.setattr(L.settings, "STORAGE_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(L, "_careem_is_authenticated", lambda _p: _false())

    async def _step():
        email_input = page.locator("input[type='email']").first
        if not await L._careem_email_input_ready(email_input):
            if await L._careem_is_authenticated(page):
                return "authed"
            await L._careem_capture_failure(page, "no-email-input")
            await page.reload(wait_until="domcontentloaded", timeout=60_000)
            await page.wait_for_timeout(3_000)
            if not await L._careem_email_input_ready(email_input):
                await L._careem_capture_failure(page, "no-email-input-retry")
                raise L.LoginError(f"no email input at {page.url}")
        return "ready"

    return asyncio.run(_step())


async def _false():
    return False


def test_a_slow_spa_boot_is_recovered_by_the_reload(monkeypatch, tmp_path):
    """The form appears on the second look — the login proceeds instead of
    arming an hour-long needs-human backoff."""
    page = _FlowPage(ready_on_call=2)
    assert _run_email_step(page, monkeypatch, tmp_path) == "ready"
    assert page.reloads == 1
    assert (tmp_path / "careem-debug-no-email-input.json").exists()


def test_a_genuine_miss_still_fails_but_leaves_both_dumps(monkeypatch, tmp_path):
    page = _FlowPage(ready_on_call=99)
    with pytest.raises(L.LoginError):
        _run_email_step(page, monkeypatch, tmp_path)
    assert page.reloads == 1
    assert (tmp_path / "careem-debug-no-email-input.json").exists()
    assert (tmp_path / "careem-debug-no-email-input-retry.json").exists()


# ── and the real function is wired that way ────────────────────────────────────


def test_login_careem_really_retries_and_captures_on_both_misses():
    """Guard the shape of the fix in the SHIPPING code, not just the mirror above.

    The bug was a single un-instrumented `wait_for` that failed the whole login.
    So the real email step must: check readiness through the helper, capture
    evidence on the first miss, reload, check again, and capture again before
    raising. If someone collapses this back to one bare wait, this fails.
    """
    import inspect

    src = inspect.getsource(L.login_careem)
    step = src.split("# auth.careem.com email step")[1].split("Verification-code")[0]

    assert step.count("_careem_email_input_ready") == 2, "must re-check after reload"
    assert step.count("_careem_capture_failure") == 2, "evidence on BOTH misses"
    assert "page.reload" in step, "the retry must actually reload the SPA"
    assert ".first" in step, "strict-mode guard on the email locator"
    assert "timeout=20_000" not in step, "the flat 20s wait must be gone"
