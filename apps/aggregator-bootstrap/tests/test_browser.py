"""Unit tests for browser helpers — pure filesystem, no Playwright, no network."""

from __future__ import annotations

from aggregator_bootstrap.browser import _clear_stale_singleton_locks


def test_clears_singleton_locks(tmp_path):
    """A profile left locked by a SIGKILLed warm is unblocked for the next launch."""
    (tmp_path / "SingletonLock").symlink_to("dead-host-123")
    (tmp_path / "SingletonCookie").write_text("x")
    (tmp_path / "SingletonSocket").write_text("y")
    (tmp_path / "Default").mkdir()  # real profile data must survive

    _clear_stale_singleton_locks(tmp_path)

    assert not (tmp_path / "SingletonLock").exists()
    assert not (tmp_path / "SingletonCookie").exists()
    assert not (tmp_path / "SingletonSocket").exists()
    assert (tmp_path / "Default").is_dir()


def test_no_locks_is_a_noop(tmp_path):
    """A clean profile (or a first-ever launch) clears nothing and does not raise."""
    _clear_stale_singleton_locks(tmp_path)  # must not raise on missing files
    assert list(tmp_path.iterdir()) == []


def test_clears_an_unexpected_singleton_variant(tmp_path):
    """Globbing Singleton* survives a Chrome-version rename and spares real data."""
    (tmp_path / "SingletonLock").symlink_to("dead-host-1")
    (tmp_path / "SingletonFoo").write_text("z")  # a hypothetical future variant
    (tmp_path / "Cookies").write_text("real")  # must NOT be touched

    _clear_stale_singleton_locks(tmp_path)

    assert not (tmp_path / "SingletonLock").exists()
    assert not (tmp_path / "SingletonFoo").exists()
    assert (tmp_path / "Cookies").read_text() == "real"


def test_spawn_chrome_clears_locks_before_launch(tmp_path, monkeypatch):
    """The raw debug-port launch (auto-relogin/heal) must clear a stale lock too,
    not just the Playwright warm — the fix for the recurring 'did not open a debug
    port' that had to be cleared by hand."""
    import subprocess

    from aggregator_bootstrap import browser

    profile = tmp_path / "noon.chrome"
    profile.mkdir()
    (profile / "SingletonLock").symlink_to("dead-host-9")  # left by a killed warm

    monkeypatch.setattr(browser, "chrome_binary", lambda: "/usr/bin/google-chrome")
    monkeypatch.setattr(browser, "standalone_chrome_args", lambda **k: ["chrome"])

    class _FakeProc:
        pass

    launched = {}

    def _fake_popen(args, **kwargs):
        # The lock must already be gone by the time Chrome is actually spawned.
        launched["lock_present_at_spawn"] = (profile / "SingletonLock").exists()
        return _FakeProc()

    monkeypatch.setattr(subprocess, "Popen", _fake_popen)

    proc = browser._spawn_chrome(profile=profile, port=45001, url="https://x")

    assert isinstance(proc, _FakeProc)
    assert launched["lock_present_at_spawn"] is False, (
        "stale SingletonLock was not cleared before Chrome was spawned"
    )
    assert not (profile / "SingletonLock").exists()
