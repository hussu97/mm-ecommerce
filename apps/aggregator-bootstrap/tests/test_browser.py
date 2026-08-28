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
