"""shared/.env handling, the secret guard, and port ownership (N-08, ADR-027)."""
import os
import socket
import stat
from pathlib import Path

import pytest

import release_common as rc
import release_probe as rp
from tests.ops.helpers import CANARY_OTHER, CANARY_PASSWORD, CANARY_USER, DATABASE_URL, write_env


# --- .env ------------------------------------------------------------------------------------

def test_parse_env_text_handles_quotes_export_and_comments():
    parsed = rp.parse_env_text("# c\n\nexport A=1\nB = 'two words'\nC=\"3\"\nnot a line\n1BAD=x\nD=a=b\n")
    assert parsed == {"A": "1", "B": "two words", "C": "3", "D": "a=b"}


def test_only_the_four_variables_the_app_reads_are_loaded_and_the_rest_are_names_only(tmp_path):
    env = rp.read_env_file(write_env(tmp_path))
    assert set(env.values) == {"APP_ENV", "DATABASE_URL", "DEBUG", "CORS_ALLOWED_ORIGINS"}
    assert env.ignored_keys == ["CLOUDFLARE_API_TOKEN", "SECRET_KEY"]
    for secret in (DATABASE_URL, CANARY_PASSWORD, CANARY_OTHER, "another-secret-value-123"):
        assert secret in env.guard._values  # guarded even though the app never receives them
    assert CANARY_USER in env.guard._values


@pytest.mark.parametrize("mode", [0o644, 0o640, 0o604, 0o666, 0o660])
def test_env_file_must_be_0600(tmp_path, mode):
    with pytest.raises(rc.OpsError, match="must be 0600") as err:
        rp.read_env_file(write_env(tmp_path, mode=mode))
    assert err.value.code == rc.Exit.CONFIG


def test_env_file_must_be_a_regular_file_not_a_symlink(tmp_path):
    real = write_env(tmp_path / "a"); link = tmp_path / "link.env"; link.symlink_to(real)
    with pytest.raises(rc.OpsError, match="not a symlink"):
        rp.read_env_file(link)
    with pytest.raises(rc.OpsError, match="not found"):
        rp.read_env_file(tmp_path / "missing.env")


def test_env_file_owner_is_checked(tmp_path, monkeypatch):
    path = write_env(tmp_path)
    monkeypatch.setattr(os, "geteuid", lambda: os.stat(path).st_uid + 1)
    with pytest.raises(rc.OpsError, match="owned by the service user"):
        rp.read_env_file(path)


def test_errors_never_echo_the_url_or_the_file(tmp_path):
    with pytest.raises(rc.OpsError) as err:
        rp.split_database_url("mysql://user:PWCANARY@host/db")
    assert "PWCANARY" not in err.value.message and "mysql" not in err.value.message
    with pytest.raises(rc.OpsError) as err:
        rp.read_env_file(write_env(tmp_path, mode=0o644))
    assert CANARY_PASSWORD not in err.value.message


@pytest.mark.parametrize("values,code", [
    ({}, rc.Exit.APP_ENV), ({"APP_ENV": "  "}, rc.Exit.APP_ENV), ({"APP_ENV": "dev"}, rc.Exit.APP_ENV), ({"APP_ENV": "prod"}, rc.Exit.APP_ENV),
    ({"APP_ENV": "staging"}, rc.Exit.APP_ENV), ({"APP_ENV": "test"}, rc.Exit.APP_ENV), ({"APP_ENV": "local"}, rc.Exit.APP_ENV),
    ({"APP_ENV": "production"}, rc.Exit.CONFIG),
    ({"APP_ENV": "production", "DATABASE_URL": DATABASE_URL, "DEBUG": "yes", "CORS_ALLOWED_ORIGINS": "https://artesanfc.com"}, rc.Exit.CONFIG),
    ({"APP_ENV": "production", "DATABASE_URL": DATABASE_URL, "CORS_ALLOWED_ORIGINS": ""}, rc.Exit.CONFIG),
    ({"APP_ENV": "production", "DATABASE_URL": "postgresql://u:change-me@h/db", "CORS_ALLOWED_ORIGINS": "https://artesanfc.com"}, rc.Exit.CONFIG),
    ({"APP_ENV": "production", "DATABASE_URL": "postgresql://u:@h/db", "CORS_ALLOWED_ORIGINS": "https://artesanfc.com"}, rc.Exit.CONFIG),
    ({"APP_ENV": "production", "DATABASE_URL": "postgresql://u:pw123456@/db", "CORS_ALLOWED_ORIGINS": "https://artesanfc.com"}, rc.Exit.CONFIG),
])
def test_validate_production_env_refusals(values, code):
    with pytest.raises(rc.OpsError) as err:
        rp.validate_production_env(values)
    assert err.value.code == code


def test_validate_production_env_accepts_a_correct_configuration():
    rp.validate_production_env({"APP_ENV": " Production ", "DATABASE_URL": DATABASE_URL, "DEBUG": "False", "CORS_ALLOWED_ORIGINS": "https://artesanfc.com, https://www.artesanfc.com"})


def test_child_env_is_composed_not_inherited(monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "postgresql://evil:evilpw1@evil/db")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "leak-me")
    env = rp.child_env({"DATABASE_URL": DATABASE_URL, "APP_ENV": "production"})
    assert env["DATABASE_URL"] == DATABASE_URL and "AWS_SECRET_ACCESS_KEY" not in env
    assert env["PYTHONDONTWRITEBYTECODE"] == "1" and env["PYTHONNOUSERSITE"] == "1"
    assert set(env) <= {*rp._PASSTHROUGH_ENV, "DATABASE_URL", "APP_ENV", "PYTHONDONTWRITEBYTECODE", "PYTHONNOUSERSITE", "PYTHONUNBUFFERED"}


def test_pg_env_decodes_percent_escapes_and_keeps_credentials_out_of_argv_shapes():
    env = rp.pg_env("postgresql://svc:p%40ss%25w%2Frd@db.internal:5433/artesa?sslmode=require")
    assert env == {"PGHOST": "db.internal", "PGPORT": "5433", "PGDATABASE": "artesa", "PGUSER": "svc", "PGPASSWORD": "p@ss%w/rd", "PGSSLMODE": "require"}


# --- secret guard ---------------------------------------------------------------------------------

def test_secret_guard_scrubs_detects_and_refuses_argv():
    guard = rp.SecretGuard([CANARY_PASSWORD, "tiny"])
    assert guard.contains(f"x {CANARY_PASSWORD} y") and not guard.contains("x tiny y")  # <6 chars ignored
    assert guard.scrub(f"a {CANARY_PASSWORD} b") == "a *** b"
    with pytest.raises(rc.OpsError) as err:
        guard.check_argv(["pg_dump", f"--password={CANARY_PASSWORD}"])
    assert err.value.code == rc.Exit.INTERNAL and CANARY_PASSWORD not in err.value.message


def test_real_runner_refuses_to_spawn_when_a_secret_is_in_argv():
    runner = rp.Runner(rp.SecretGuard([CANARY_PASSWORD]))
    with pytest.raises(rc.OpsError, match="argv"):
        runner.run(["/bin/echo", CANARY_PASSWORD])
    assert runner.run(["/bin/echo", "hello"]).stdout.strip() == "hello"


def test_real_runner_reports_missing_commands_and_timeouts_without_raising():
    runner = rp.Runner()
    assert runner.run(["definitely-not-a-command-xyz"]).returncode == 127
    assert runner.run(["/bin/sleep", "5"], timeout=0.2).returncode == 124


# --- ports -------------------------------------------------------------------------------------------

def make_proc(tmp_path: Path, *, port: int, addr_hex: str = "0100007F", pid: int | None = 1234, cwd: str = "/srv/app", inode: int = 777, visible: bool = True) -> Path:
    proc = tmp_path / "proc"
    (proc / "net").mkdir(parents=True)
    line = f"   0: {addr_hex}:{port:04X} 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000        0 {inode} 1 0000000000000000 100 0 0 10 0\n"
    (proc / "net" / "tcp").write_text("  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid  timeout inode\n" + line)
    if pid is not None and visible:
        (proc / str(pid) / "fd").mkdir(parents=True)
        os.symlink(f"socket:[{inode}]", proc / str(pid) / "fd" / "3")
        os.symlink(cwd, proc / str(pid) / "cwd")
    return proc


def test_port_free(tmp_path):
    proc = tmp_path / "proc"; (proc / "net").mkdir(parents=True); (proc / "net" / "tcp").write_text("header\n")
    state = rp.port_owner_state(8000, "/srv/app", proc=str(proc))
    assert (state.state, state.ok) == ("free", True)


def test_port_held_by_the_expected_release(tmp_path):
    proc = make_proc(tmp_path, port=8000, cwd=str(tmp_path))
    state = rp.port_owner_state(8000, tmp_path, proc=str(proc))
    assert state.state == "expected" and state.ok and state.pid == 1234


def test_port_held_by_an_alien_process_reports_pid_and_cwd_only(tmp_path):
    proc = make_proc(tmp_path, port=8000, cwd="/home/other/artesa-nfc-backend")
    state = rp.port_owner_state(8000, tmp_path / "releases" / "x", proc=str(proc))
    assert state.state == "alien" and not state.ok
    assert "pid 1234" in state.detail and "/home/other/artesa-nfc-backend" in state.detail and "argv" not in state.detail


def test_port_held_by_an_uninspectable_process_fails_closed(tmp_path):
    proc = make_proc(tmp_path, port=8000, visible=False)
    state = rp.port_owner_state(8000, tmp_path, proc=str(proc))
    assert state.state == "unknown" and not state.ok


def test_port_listening_on_all_interfaces_is_flagged_exposed(tmp_path):
    proc = make_proc(tmp_path, port=8000, addr_hex="00000000", cwd=str(tmp_path))
    state = rp.port_owner_state(8000, tmp_path, proc=str(proc))
    assert state.state == "exposed" and not state.ok and "non-loopback" in state.detail


def test_no_expected_release_means_any_listener_is_alien(tmp_path):
    proc = make_proc(tmp_path, port=8000, cwd=str(tmp_path))
    assert rp.port_owner_state(8000, None, proc=str(proc)).state == "alien"


def test_a_real_listener_is_attributed_to_this_process_by_cwd(tmp_path):
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0)); sock.listen(1)
        port = sock.getsockname()[1]
        assert not rp.port_is_free(port)
        assert rp.port_owner_state(port, os.getcwd()).state == "expected"
        other = rp.port_owner_state(port, tmp_path)
        assert other.state == "alien" and str(os.getpid()) in other.detail
    assert rp.port_is_free(port)


def test_decode_ipv6_addresses():
    assert rp._decode_addr("00000000000000000000000001000000") == "::1"
    assert rp._decode_addr("00000000000000000000000000000000") == "::"
    assert rp._decode_addr("0100007F") == "127.0.0.1"
