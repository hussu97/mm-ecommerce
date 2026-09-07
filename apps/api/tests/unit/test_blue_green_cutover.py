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
BACKUP_DB = ROOT / "scripts" / "backup-db.sh"
RESTORE_DB = ROOT / "scripts" / "restore-db.sh"


@pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")
@pytest.mark.parametrize(
    "script", [CUTOVER, DEPLOY_SH, DECOMMISSION, BACKUP_DB, RESTORE_DB]
)
def test_deploy_scripts_parse(script: Path):
    result = subprocess.run(
        ["bash", "-n", str(script)], capture_output=True, text=True, check=False
    )
    assert result.returncode == 0, f"{script.name}: {result.stderr}"


def test_drain_matches_upstream_keepalive_timeout():
    upstreams = UPSTREAMS.read_text()
    script = CUTOVER.read_text()
    timeouts = set(re.findall(r"keepalive_timeout\s+(\d+)s", upstreams))
    assert timeouts == {"5"}, timeouts
    default = re.search(r'DRAIN_SECONDS="\$\{DRAIN_SECONDS:-(\d+)\}"', script)
    assert default is not None, "DRAIN_SECONDS default missing from cutover script"
    assert default.group(1) == "5"
    assert 'STOP_GRACE="${STOP_GRACE:-10}"' in script
    wait = re.search(r'WAIT_TIMEOUT="\$\{WAIT_TIMEOUT:-(\d+)\}"', script)
    assert wait is not None, "WAIT_TIMEOUT default missing from cutover script"
    assert int(wait.group(1)) >= 90


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


def test_nginx_runs_one_worker_on_the_e2_small():
    """This nginx only proxies api+pos; auto forked a worker per shared vCPU."""
    text = NGINX_CONF.read_text()
    assert re.search(r"^worker_processes\s+1\s*;", text, re.M)
    assert not re.search(r"^worker_processes\s+auto\s*;", text, re.M)


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


def test_cutover_stops_and_restarts_aggregator_worker_daemon():
    """aggregator-worker is a long-lived daemon (Phase 3), not a cron one-shot.
    Cutover stops it to free RAM for the green API slot, then restarts it via an
    EXIT trap — success or failure — so no caller (deploy.yml, rollback.yml,
    deploy.sh) leaves the box with no worker (and therefore no healing)."""
    text = CUTOVER.read_text()
    assert 'docker ps -q --filter "name=aggregator-worker"' in text
    assert "docker stop -t 15" in text
    assert "trap _restart_aggregator_worker EXIT" in text
    assert "up -d --no-deps aggregator-worker" in text
    # the old 20-minute wait/flock behaviour is gone
    assert "waiting (${i}/240)" not in text
    assert "still running after 20 minutes" not in text


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


def test_cutover_has_no_dead_flock_machinery():
    """The always-on worker daemon (Phase 3) serialises browser jobs in-process, so
    the old shared warm flock is dead. Cutover must not reference it — a leftover
    flock acquire is dead code that could only fail a deploy. Scheduling itself moved
    off cron into the daemon; `deploy/aggregator-warm.cron` is retired."""
    script = CUTOVER.read_text()
    assert "WARM_LOCK" not in script
    assert "mm-aggregator-warm.lock" not in script
    assert "flock -w" not in script  # the lock acquire
    assert "exec 9>" not in script  # the lock fd
    assert not (
        ROOT / "apps" / "aggregator-bootstrap" / "deploy" / "aggregator-warm.cron"
    ).exists()


def test_deploy_yml_runs_tests_and_image_build_as_sibling_jobs():
    """
    Serial pytest-then-docker on one runner was 98s+67s before SSH. Sibling
    jobs overlap them. :latest is promoted only after tests pass.
    """
    text = DEPLOY_YML.read_text()
    assert "name: Test API" in text
    assert "name: Build API image" in text
    assert "name: Deploy API to GCP" in text
    assert "Promote API image to :latest" in text
    assert "needs: [changes, test-api, build-api, build-bootstrap]" in text
    assert "--cov=app" not in text
    assert (
        "docker pull ghcr.io/hussu97/mm-ecommerce-aggregator-bootstrap:latest"
        not in text
    )


def test_bootstrap_path_filter_does_not_include_the_workflow_file():
    """A deploy.yml-only or cron-only change must not rebuild the 4.7GB image."""
    text = DEPLOY_YML.read_text()
    match = re.search(
        r"bootstrap:\n((?:[ \t]+- .+\n)+)",
        text,
    )
    assert match is not None, "bootstrap path filter missing"
    listed = match.group(1)
    assert "deploy.yml" not in listed
    assert "aggregator-bootstrap/deploy" not in listed
    assert "aggregator-bootstrap/src/**" in listed


def test_cutover_pair_cuts_pos_api_before_the_storefront():
    """
    F-OPS-14/15: pos-api is the smaller pair (256m, 2+3 connections vs the
    storefront's 512m, 5+8) — cutting it over first keeps the bigger overlap
    (the one that OOM-killed a slot on 2026-09-07) from ever landing on top of
    a pos-api overlap that has not drained yet.
    """
    text = CUTOVER.read_text()
    match = re.search(r"^cutover\(\) \{.*?^\}", text, re.M | re.S)
    assert match is not None, "could not find the cutover() function body"
    body = match.group(0)
    pos_at = body.find("_cutover_pair pos-api pos-api-green")
    api_at = body.find("_cutover_pair api api-green")
    assert pos_at != -1, "cutover() never cuts over pos-api"
    assert api_at != -1, "cutover() never cuts over api"
    assert pos_at < api_at, (
        "pos-api must be cut over before the storefront api (F-OPS-14/15)"
    )


def test_restore_db_stops_every_writer_before_dropping_the_database():
    """
    F-OPS-8: the old restore-db.sh stopped only `api`. `pos-api`, either green
    slot (mid-cutover) and the aggregator-worker daemon can all still hold a
    connection and keep writing right up to `DROP DATABASE`, which either
    fails outright or races the drop.
    """
    text = RESTORE_DB.read_text()
    assert "WRITERS=(api pos-api api-green pos-api-green aggregator-worker)" in text
    stop_at = text.find('_compose stop "${WRITERS[@]}"')
    drop_at = text.find("DROP DATABASE IF EXISTS")
    assert stop_at != -1, "restore-db.sh never stops the writer services"
    assert drop_at != -1
    assert stop_at < drop_at, "writers must be stopped before the database is dropped"


def test_restore_db_terminates_backends_and_fails_closed_on_a_bad_dump():
    text = RESTORE_DB.read_text()
    assert "pg_terminate_backend" in text, (
        "DROP DATABASE refuses while any session is still connected; a "
        "lingering connection (a healthcheck, a stray psql) must be "
        "terminated explicitly, not just asked to stop"
    )
    assert "ON_ERROR_STOP=1" in text, (
        "without this, psql keeps going past a failed statement in the dump "
        "and a truncated restore looks like a clean success"
    )
    assert "alembic_version" in text, (
        "a dump that restores cleanly but is not actually an mm_ecommerce "
        "database (wrong file, an empty dump) has to be caught after the "
        "fact, since ON_ERROR_STOP alone would not see anything wrong with it"
    )
    # A bad restore must leave services stopped, not restart on top of it.
    assert re.search(
        r'if \[ -z "\$ALEMBIC_VERSION" \]; then\n(?:.*\n){0,6}?\s*exit 1', text
    ), "an empty alembic_version must abort before the restart step"


def test_backup_db_gcs_upload_failure_is_loud():
    """
    F-OPS-8: a failed offsite upload used to be a plain log line ("WARNING:
    GCS upload failed") — easy to miss in a deploy log nobody is tailing.
    `::error::` is a GitHub Actions annotation; this script runs over SSH from
    deploy.yml/rollback.yml, and Actions parses the annotation out of the
    step's own stdout regardless of which host produced it, so a failed
    offsite backup now surfaces as a red annotation on the run instead of
    text buried in the log. Still non-fatal, deliberately — the local dump is
    what protects the migration this run is about to make.
    """
    text = BACKUP_DB.read_text()
    assert "::error::backup-db.sh: GCS upload" in text
    assert text.count("::error::backup-db.sh") >= 2, (
        "both the cp-failed and no-CLI-available cases should be loud"
    )
