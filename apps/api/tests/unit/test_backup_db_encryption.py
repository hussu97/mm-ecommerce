"""
P1-5: `scripts/backup-db.sh` encrypts dumps with `age` when BACKUP_AGE_RECIPIENT
is set — and must never fail because of it.

These run the real script end to end against stub `docker` / `gcloud` (and a
stub `age` for the decision logic), in a throwaway DEPLOY_DIR, because the
properties that matter are behavioural and live in the interaction of the
branches: a configured recipient never lets a plaintext dump leave the host,
an unconfigured or broken one still leaves a local backup and exits 0 (the
deploy aborts the migration on a non-zero exit), and plain + encrypted dumps
share one KEEP_BACKUPS rotation in both places.

The last test does a genuine encrypt -> decrypt round trip with a throwaway key
when the real `age` binary is installed, and is skipped otherwise.
"""

from __future__ import annotations

import gzip
import os
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[4]
BACKUP_DB = ROOT / "scripts" / "backup-db.sh"
RESTORE_DB = ROOT / "scripts" / "restore-db.sh"

FAKE_DUMP = "-- fake pg_dump\nCREATE TABLE alembic_version (version_num text);\n"
FAKE_RECIPIENT = "age1stubrecipientstubrecipientstubrecipientstubrecipientstub"

pytestmark = pytest.mark.skipif(not shutil.which("bash"), reason="bash not available")


def _write_exe(path: Path, body: str) -> None:
    path.write_text("#!/usr/bin/env bash\n" + body)
    path.chmod(0o755)


@pytest.fixture
def env(tmp_path: Path):
    deploy = tmp_path / "deploy"
    deploy.mkdir()
    bucket = tmp_path / "bucket" / "backups"
    bucket.mkdir(parents=True)
    stubs = tmp_path / "bin"
    stubs.mkdir()

    # pg_dump inside `docker compose exec` — the only docker call the script makes.
    _write_exe(stubs / "docker", f"cat <<'EOF'\n{FAKE_DUMP}EOF\n")
    # A tiny fake GCS rooted at tmp/bucket.
    _write_exe(
        stubs / "gcloud",
        f"""
BUCKET_DIR={bucket}
[ "$1" = storage ] || exit 2
shift
op=$1; shift
case "$op" in
  cp) cp "$1" "$BUCKET_DIR/$(basename "$2")" ;;
  ls) for f in "$BUCKET_DIR"/*; do [ -e "$f" ] && echo "gs://bkt/backups/$(basename "$f")"; done ;;
  rm) for u in "$@"; do rm -f "$BUCKET_DIR/$(basename "$u")"; done ;;
esac
""",
    )
    return {"tmp": tmp_path, "deploy": deploy, "bucket": bucket, "stubs": stubs}


def _stub_age(stubs: Path, *, works: bool = True) -> None:
    """`age -r R ... -o OUT IN` -> OUT = header + IN. Fails on a non-age1 key."""
    _write_exe(
        stubs / "age",
        f"""
out=""; in=""; ok={"1" if works else "0"}
while [ $# -gt 0 ]; do
  case "$1" in
    -r) case "$2" in age1*) ;; *) ok=0 ;; esac; shift 2 ;;
    -o) out=$2; shift 2 ;;
    *) in=$1; shift ;;
  esac
done
[ "$ok" = 1 ] || {{ echo "age: malformed recipient" >&2; exit 1; }}
{{ echo "AGE-STUB"; cat "$in"; }} > "$out"
""",
    )


def _run(env: dict, recipient: str | None = None, keep: int = 5):
    lines = ["POSTGRES_USER=u", "POSTGRES_DB=d", "BACKUP_GCS_BUCKET=bkt"]
    if recipient is not None:
        lines.append(f"BACKUP_AGE_RECIPIENT={recipient}")
    (env["deploy"] / ".env").write_text("\n".join(lines) + "\n")
    proc_env = {
        "PATH": f"{env['stubs']}:/usr/bin:/bin",
        "HOME": str(env["tmp"]),
        "DEPLOY_DIR": str(env["deploy"]),
        "KEEP_BACKUPS": str(keep),
    }
    return subprocess.run(
        ["bash", str(BACKUP_DB)],
        env=proc_env,
        capture_output=True,
        text=True,
        check=False,
        timeout=60,
    )


def _local(env: dict) -> list[str]:
    return sorted(p.name for p in (env["deploy"] / "backups").iterdir())


def _offsite(env: dict) -> list[str]:
    return sorted(p.name for p in env["bucket"].iterdir())


def test_no_recipient_keeps_todays_behaviour_with_a_warning(env):
    result = _run(env)
    assert result.returncode == 0, result.stdout + result.stderr
    [name] = _local(env)
    assert name.endswith(".sql.gz")
    assert _offsite(env) == [name]
    assert "BACKUP_AGE_RECIPIENT is not set" in result.stdout
    with gzip.open(env["deploy"] / "backups" / name, "rt") as fh:
        assert fh.read().strip() == FAKE_DUMP.strip()


def test_recipient_set_encrypts_uploads_ciphertext_and_deletes_plaintext(env):
    _stub_age(env["stubs"])
    result = _run(env, FAKE_RECIPIENT)
    assert result.returncode == 0, result.stdout + result.stderr
    [name] = _local(env)
    assert name.endswith(".sql.gz.age"), "plaintext must not be kept locally"
    assert _offsite(env) == [name], "only the ciphertext may be uploaded"
    assert not list((env["deploy"] / "backups").glob("*.partial"))


def test_several_recipients_are_all_passed_to_age(env):
    _write_exe(
        env["stubs"] / "age",
        'echo "$@" > "$(dirname "$0")/age_args"\n'
        'while [ $# -gt 0 ]; do [ "$1" = -o ] && out=$2; in=$1; shift; done\n'
        'cp "$in" "$out"\n',
    )
    result = _run(env, f"{FAKE_RECIPIENT}1,{FAKE_RECIPIENT}2")
    assert result.returncode == 0, result.stdout + result.stderr
    args = (env["stubs"] / "age_args").read_text()
    assert f"-r {FAKE_RECIPIENT}1 -r {FAKE_RECIPIENT}2 -o" in args


@pytest.mark.skipif(
    shutil.which("age", path="/usr/bin:/bin") is not None,
    reason="a system age is on the base PATH, so 'age missing' can't be simulated",
)
def test_recipient_set_but_age_missing_never_uploads_plaintext_and_exits_0(env):
    result = _run(env, FAKE_RECIPIENT)
    assert result.returncode == 0, "encryption tooling must never abort a deploy"
    [name] = _local(env)
    assert name.endswith(".sql.gz"), "the migration still gets a local backup"
    assert _offsite(env) == [], "a plaintext dump must not leave the host"
    assert "::error::backup-db.sh: BACKUP_AGE_RECIPIENT is set" in result.stdout


def test_recipient_set_but_age_fails_never_uploads_plaintext_and_exits_0(env):
    _stub_age(env["stubs"])
    result = _run(env, "not-an-age-key")
    assert result.returncode == 0, result.stdout + result.stderr
    [name] = _local(env)
    assert name.endswith(".sql.gz")
    assert _offsite(env) == []
    assert "age encryption of" in result.stdout and "FAILED" in result.stdout
    assert not list((env["deploy"] / "backups").glob("*.partial"))


def test_plain_and_encrypted_dumps_share_one_rotation(env):
    """Switching encryption on must not change how many dumps are kept."""
    _stub_age(env["stubs"])
    backups = env["deploy"] / "backups"
    backups.mkdir()
    old = [
        "mm_ecommerce_20260101_020000.sql.gz",
        "mm_ecommerce_20260102_020000.sql.gz",
        "mm_ecommerce_20260103_020000.sql.gz.age",
        "mm_ecommerce_20260104_020000.sql.gz",
    ]
    for i, name in enumerate(old):
        for where in (backups, env["bucket"]):
            p = where / name
            p.write_bytes(b"x")
            os.utime(p, (1_700_000_000 + i * 86400,) * 2)

    result = _run(env, FAKE_RECIPIENT, keep=3)
    assert result.returncode == 0, result.stdout + result.stderr

    for listing in (_local(env), _offsite(env)):
        assert len(listing) == 3, listing
        assert listing[:2] == [
            "mm_ecommerce_20260103_020000.sql.gz.age",
            "mm_ecommerce_20260104_020000.sql.gz",
        ]
        assert listing[2].endswith(".sql.gz.age")


def test_restore_refuses_an_encrypted_backup_without_an_identity(tmp_path):
    f = tmp_path / "mm_ecommerce_20260101_020000.sql.gz.age"
    f.write_bytes(b"x")
    result = subprocess.run(
        ["bash", str(RESTORE_DB), str(f)],
        env={"PATH": "/usr/bin:/bin", "DEPLOY_DIR": str(tmp_path)},
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert result.returncode != 0
    assert "AGE_IDENTITY" in result.stderr + result.stdout


def test_restore_pipes_the_dump_to_psql_not_dev_null():
    """
    `_compose` redirects stdin from /dev/null, so piping the dump into it fed
    psql an empty stream: the restore dropped the database, "restored" nothing
    and then failed the alembic_version check against an empty database.
    """
    text = RESTORE_DB.read_text()
    assert "| \\\n  _compose exec" not in text
    assert '_dump_sql | \\\n  docker compose -f "$COMPOSE_FILE"' in text
    # The dump is proven readable before anything is stopped or dropped.
    assert text.find("_dump_sql > /dev/null") < text.find("_compose stop")


@pytest.mark.skipif(
    not (shutil.which("age") and shutil.which("age-keygen")),
    reason="real age/age-keygen not installed",
)
def test_real_age_round_trip_with_a_throwaway_key(env):
    identity = env["tmp"] / "identity.txt"
    subprocess.run(["age-keygen", "-o", str(identity)], check=True, capture_output=True)
    recipient = subprocess.run(
        ["age-keygen", "-y", str(identity)], check=True, capture_output=True, text=True
    ).stdout.strip()
    os.symlink(shutil.which("age"), env["stubs"] / "age")

    result = _run(env, recipient)
    assert result.returncode == 0, result.stdout + result.stderr
    [name] = _local(env)
    assert name.endswith(".sql.gz.age")
    encrypted = env["deploy"] / "backups" / name
    assert b"CREATE TABLE" not in encrypted.read_bytes()

    decrypted = subprocess.run(
        ["age", "-d", "-i", str(identity), str(encrypted)],
        check=True,
        capture_output=True,
    ).stdout
    assert gzip.decompress(decrypted).decode().strip() == FAKE_DUMP.strip()
