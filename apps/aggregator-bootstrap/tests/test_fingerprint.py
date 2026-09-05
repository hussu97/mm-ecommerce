"""Browser launch kwargs — Patchright's undetected set, no fingerprint injection."""

import sqlite3
from pathlib import Path

from aggregator_bootstrap.browser import chrome_cookie_names
from aggregator_bootstrap.fingerprint import (
    LOW_OVERHEAD_CHROME_ARGS,
    chrome_profile_dir,
    context_kwargs,
    headed_persistent_kwargs,
    standalone_chrome_args,
    warm_persistent_kwargs,
)


def test_headed_launch_is_patchright_recommended():
    """Custom headers/UA/locale are how Cloudflare loops. Don't set them."""
    kwargs = headed_persistent_kwargs()
    assert kwargs == {
        "channel": "chrome",
        "headless": False,
        "no_viewport": True,
    }
    assert "user_agent" not in kwargs
    assert "extra_http_headers" not in kwargs
    assert "args" not in kwargs
    assert "ignore_default_args" not in kwargs
    assert "locale" not in kwargs
    assert "geolocation" not in kwargs


def test_warm_kwargs_only_flip_headless():
    headed = warm_persistent_kwargs(headed=True)
    headless = warm_persistent_kwargs(headed=False)
    assert headed["headless"] is False
    assert headed["no_viewport"] is True
    assert headless["headless"] is True
    assert headless["viewport"] == {"width": 1440, "height": 900}
    assert headless["channel"] == "chrome"


def test_context_does_not_spoof_ua():
    kwargs = context_kwargs()
    assert "user_agent" not in kwargs
    assert "extra_http_headers" not in kwargs
    assert kwargs["viewport"] == {"width": 1440, "height": 900}


def test_context_passes_storage_state_when_given():
    kwargs = context_kwargs(storage_state="/data/sessions/noon.session.json")
    assert kwargs["storage_state"] == "/data/sessions/noon.session.json"


def test_lean_chrome_args_use_mesa_angle_not_disable_gpu():
    """Warm/pull Mesa llvmpipe. Never --disable-gpu or BotBrowser emulation."""
    assert "--use-angle=gl" in LOW_OVERHEAD_CHROME_ARGS
    joined = " ".join(LOW_OVERHEAD_CHROME_ARGS)
    assert "--disable-gpu" not in joined
    assert "--bot-gpu-emulation" not in joined
    assert "--disable-software-rasterizer" not in joined


def test_standalone_chrome_has_no_automation_flags():
    args = standalone_chrome_args(
        binary="/usr/bin/google-chrome",
        user_data_dir=Path("/tmp/profile"),
        port=9333,
        url="https://partner-hub.deliveroo.com/login",
    )
    joined = " ".join(args)
    assert args[0] == "/usr/bin/google-chrome"
    assert "--user-data-dir=/tmp/profile" in args
    assert "--remote-debugging-port=9333" in args
    assert args[-1].startswith("https://")
    assert "--enable-automation" not in joined
    assert "AutomationControlled" not in joined
    assert "--headless" not in joined
    assert "user-agent" not in joined.lower()
    # Login spawn stays fingerprint-clean: no Mesa/ANGLE override.
    assert "--use-angle=gl" not in joined
    assert "--disable-gpu" not in joined


def test_chrome_profile_dir_is_per_channel():
    path = chrome_profile_dir("/data/sessions", "deliveroo")
    assert path.name == "deliveroo.chrome"


def test_chrome_cookie_names_reads_sqlite(tmp_path: Path):
    cookies_dir = tmp_path / "Default" / "Network"
    cookies_dir.mkdir(parents=True)
    db = cookies_dir / "Cookies"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE cookies (name TEXT, host_key TEXT)")
    con.execute("INSERT INTO cookies VALUES ('token', '.deliveroo.com')")
    con.execute("INSERT INTO cookies VALUES ('cf_clearance', '.deliveroo.com')")
    con.commit()
    con.close()
    assert chrome_cookie_names(tmp_path) == {"token", "cf_clearance"}
