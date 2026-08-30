"""
Blue/green cutover wiring: scripts parse, drain matches nginx keepalive, and
deploy/rollback no longer --force-recreate the APIs.
"""

from __future__ import annotations

import re
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
NGINX_CONF = ROOT / "nginx" / "nginx.conf"
UPSTREAMS = ROOT / "nginx" / "runtime" / "upstreams.conf"
CUTOVER = ROOT / "scripts" / "cutover-backend.sh"
DEPLOY_SH = ROOT / "scripts" / "deploy.sh"
DECOMMISSION = ROOT / "scripts" / "decommission-legacy-aggregator.sh"
DEPLOY_YML = ROOT / ".github" / "workflows" / "deploy.yml"
ROLLBACK_YML = ROOT / ".github" / "workflows" / "rollback.yml"


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
@pytest.mark.parametrize("script", [CUTOVER, DEPLOY_SH, DECOMMISSION])
def test_deploy_scripts_parse(script: Path):
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_drain_matches_upstream_keepalive_timeout():
    upstreams = UPSTREAMS.read_text()
    script = CUTOVER.read_text()
    timeouts = set(re.findall(r"keepalive_timeout\s+(\d+)s", upstreams))
    assert timeouts == {"15"}, timeouts
    default = re.search(r'DRAIN_SECONDS="\$\{DRAIN_SECONDS:-(\d+)\}"', script)
    assert default is not None, "DRAIN_SECONDS default missing from cutover script"
    assert default.group(1) == "15"
    assert 'STOP_GRACE="${STOP_GRACE:-30}"' in script


def test_committed_upstreams_point_at_slot_a():
    text = UPSTREAMS.read_text()
    assert "server api:8000;" in text
    assert "server pos-api:8000;" in text
    assert "api-green" not in text
    assert "pos-api-green" not in text


def test_nginx_includes_runtime_upstreams_and_has_no_inline_upstream():
    text = NGINX_CONF.read_text()
    assert "include /etc/nginx/runtime/upstreams.conf;" in text
    assert not re.search(r"^\s*upstream\s+\w+", text, re.M)


def test_deploy_yml_gates_nginx_rebuild_on_path_filter():
    text = DEPLOY_YML.read_text()
    assert "needs.changes.outputs.nginx" in text
    assert "NGINX_CHANGED" in text
    assert re.search(r'if \[ "\$NGINX_CHANGED" = "true" \]', text), (
        "nginx rebuild must be gated on NGINX_CHANGED"
    )
    match = re.search(r"^[^#\n]*up -d --no-deps --build nginx", text, re.M)
    assert match is not None, "expected a gated nginx --build"
    before = text[: match.start()]
    assert "NGINX_CHANGED" in before[-800:], (
        "the nginx --build line must sit inside the NGINX_CHANGED branch"
    )


def test_deploy_and_rollback_call_cutover_and_do_not_force_recreate():
    for path in (DEPLOY_YML, ROLLBACK_YML):
        text = path.read_text()
        assert "scripts/cutover-backend.sh" in text, f"{path.name} never calls cutover"
        assert not re.search(r"^[^#\n]*--force-recreate", text, re.M), (
            f"{path.name} still force-recreates a container; that is the "
            "stop-then-start hole cutover exists to close"
        )


def test_deploy_sh_is_a_wrapper_around_cutover():
    text = DEPLOY_SH.read_text()
    assert "scripts/cutover-backend.sh" in text
    assert not re.search(r"^[^#\n]*--force-recreate", text, re.M)
    assert not re.search(r"^[^#\n]*up -d --no-deps api", text, re.M)


def test_git_reset_is_followed_by_restore_upstreams():
    """
    The committed nginx/runtime/upstreams.conf always points at slot A.
    After a green cutover, `git reset --hard origin/main` would send the next
    nginx reload at a stopped container unless restore-upstreams runs first.
    """
    for path in (DEPLOY_YML, DEPLOY_SH):
        text = path.read_text()
        reset_at = text.find("git reset --hard origin/main")
        restore_at = text.find("restore-upstreams")
        assert reset_at != -1, f"{path.name} never git-resets"
        assert restore_at != -1, f"{path.name} never restores live upstreams"
        assert restore_at > reset_at, (
            f"{path.name} restores upstreams before git reset, so reset "
            "would still clobber the live slot pointer"
        )


def test_cutover_probes_health_with_production_host():
    text = CUTOVER.read_text()
    assert 'curl -sf -H "Host: $host" "http://localhost:8000/health"' in text
    assert "https://${host}/health" in text
    assert 'API_HOST="${API_HOST:-api.meltingmomentscakes.com}"' in text
    assert 'POS_HOST="${POS_HOST:-pos.meltingmomentscakes.com}"' in text
    assert "localhost:8000/ping" not in text


def test_failed_idle_start_stops_the_idle_slot():
    """
    compose --wait / /health failing with the idle colour already up is how
    both APIs end up in memory on this e2-small.
    """
    text = CUTOVER.read_text()
    assert re.search(
        r'if ! _compose up -d --no-deps --wait[^\n]*"\$idle"; then'
        r"\n(?:.*\n){0,8}?\s+_stop_slot \"\$idle\"",
        text,
    ), "compose --wait failure must docker-stop the idle slot"
    assert re.search(
        r'if ! _wait_health "\$idle" "\$host"; then'
        r"\n\s+_stop_slot \"\$idle\"",
        text,
    ), "/health failure must docker-stop the idle slot"


def test_decommission_never_drops_ecommerce_tables():
    text = DECOMMISSION.read_text()
    assert '-c "DROP DATABASE mm_aggregator;"' in text
    assert not re.search(r"DROP TABLE\s+\w+", text)
    assert "DROP DATABASE mm_ecommerce" not in text
    assert "melting-moments-cakes_aggregator_sessions" in text
    assert "/etc/cron.d/aggregator-warm" in text
