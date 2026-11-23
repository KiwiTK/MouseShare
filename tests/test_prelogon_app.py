"""Regression: the pre-logon GUI must find the last console user's config.

After a reboot nobody has signed in, so the service launches the GUI as
SYSTEM. LOCALAPPDATA then points at the SYSTEM profile and the app would
lose its identity and pairing — the service passes the real config dir via
MOUSESHARE_CONFIG_DIR, and prelogon_config_dir() picks it from the recorded
console user name (validating that it looks like a real MouseShare config).
"""
import sys
from pathlib import Path

from mouseshare.config import data_dir


def test_data_dir_prefers_the_override(monkeypatch):
    monkeypatch.setenv("MOUSESHARE_CONFIG_DIR", r"C:\Users\kiwi\AppData\Local\MouseShare")
    assert data_dir() == Path(r"C:\Users\kiwi\AppData\Local\MouseShare")


def test_data_dir_without_override_stays_default(monkeypatch):
    import platform
    monkeypatch.delenv("MOUSESHARE_CONFIG_DIR", raising=False)
    directory = data_dir()
    if platform.system() == "Windows":
        import os
        root = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
        assert directory == root / "MouseShare"
    else:
        assert directory.name == "MouseShare"


def load_winlock():
    sys.modules.pop('mouseshare.winlock', None)
    import ctypes
    ctypes.WinDLL = lambda name, **kwargs: object.__new__(type('FakeDll', (), {
        '__getattr__': lambda self, name: type('Stub', (), {})()}))
    try:
        import mouseshare.winlock as winlock
        return winlock
    finally:
        del ctypes.WinDLL


def _profile(tmp_path, username="kiwi", with_files=True):
    directory = tmp_path / username / "AppData" / "Local" / "MouseShare"
    directory.mkdir(parents=True)
    if with_files:
        (directory / "settings.json").write_text("{}", encoding="utf-8")
        (directory / "identity.key").write_text("-----BEGIN-----", encoding="utf-8")
    return directory


def test_prelogon_config_dir_resolves_last_user(tmp_path, monkeypatch):
    winlock = load_winlock()
    _profile(tmp_path)
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    (Path(tmp_path) / "last_user.txt").write_text("kiwi", encoding="utf-8")
    assert winlock.prelogon_config_dir(profile_root=tmp_path) == \
        tmp_path / "kiwi" / "AppData" / "Local" / "MouseShare"


def test_prelogon_config_dir_rejects_incomplete_config(tmp_path, monkeypatch):
    winlock = load_winlock()
    directory = tmp_path / "kiwi" / "AppData" / "Local" / "MouseShare"
    directory.mkdir(parents=True)
    (directory / "settings.json").write_text("{}", encoding="utf-8")  # no identity yet
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    (Path(tmp_path) / "last_user.txt").write_text("kiwi", encoding="utf-8")
    assert winlock.prelogon_config_dir(profile_root=tmp_path) is None


def test_prelogon_config_dir_rejects_path_tricks(tmp_path, monkeypatch):
    winlock = load_winlock()
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    for username in (r"..\other", "a/b", "C:", "", ".."):
        (Path(tmp_path) / "last_user.txt").write_text(username, encoding="utf-8")
        assert winlock.prelogon_config_dir(profile_root=tmp_path) is None, username


def test_prelogon_config_dir_without_record(tmp_path, monkeypatch):
    winlock = load_winlock()
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    assert winlock.prelogon_config_dir(profile_root=tmp_path) is None


def test_prelogon_state_reports_skip_reason(tmp_path, monkeypatch):
    """A silent skip reads like a missing feature; the reason must reach the log."""
    winlock = load_winlock()
    directory = tmp_path / "kiwi" / "AppData" / "Local" / "MouseShare"
    directory.mkdir(parents=True)
    (directory / "settings.json").write_text("{}", encoding="utf-8")  # no identity.key
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    (Path(tmp_path) / "last_user.txt").write_text("kiwi", encoding="utf-8")
    found, reason = winlock.prelogon_config_state(profile_root=tmp_path)
    assert found is None
    assert "config incomplete" in reason and "kiwi" in reason


def test_prelogon_state_falls_back_to_valid_profile(tmp_path, monkeypatch):
    """A stale/renamed last-user record must not kill the pre-logon GUI.

    Regression: the field machine recorded 'adminstrator' while the real
    profile carrying settings.json + identity.key lives under another name —
    the service silently skipped and pre-logon crossing never started.
    """
    winlock = load_winlock()
    real = _profile(tmp_path, username="Administrator")
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    (Path(tmp_path) / "last_user.txt").write_text("ghost", encoding="utf-8")
    assert winlock.prelogon_config_state(profile_root=tmp_path) == (real, "")
    assert winlock.prelogon_config_dir(profile_root=tmp_path) == real


def test_prelogon_state_reason_without_record(tmp_path, monkeypatch):
    winlock = load_winlock()
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    found, reason = winlock.prelogon_config_state(profile_root=tmp_path)
    assert found is None
    assert "last_user.txt" in reason


def _pkg_source(filename):
    import mouseshare
    return (Path(mouseshare.__file__).parent / filename).read_text(encoding="utf-8")


def test_lock_wait_loops_while_owner_alive():
    """Loser of the startup race must wait, never delete a live lock and race it.

    Regression: a cold start (Qt + antivirus first-run scan) can exceed the
    10 s forward timeout; discarding the lock then started a second full
    instance, one of them stuck 未连接 on the occupied input port.
    """
    src = _pkg_source("app.py")
    assert "if forward(name, outgoing(), START_TIMEOUT):\n            return 0\n        discard(lock)" not in src
    assert "Keep waiting while the recorded owner is alive" in src


def test_alive_treats_access_denied_as_alive():
    """A privileged recorded owner must not read as dead (that steals the lock)."""
    src = _pkg_source("instance.py")
    assert "ERROR_ACCESS_DENIED" in src


def test_supervisor_kills_stray_system_gui():
    """A service restart orphans the pre-logon GUI handle; strays must die."""
    src = _pkg_source("winlock.py")
    assert "terminating stray pre-logon GUI" in src
    assert "system_app_pids" in src


def test_profile_root_uses_drive_anchor():
    """Path(SystemDrive) is drive-relative: 'C:' / 'Users' == 'C:Users'.

    Regression (0.8.30): the pre-logon check resolved C:Users\\... against
    the service working directory, config validation always failed, and the
    pre-logon GUI never launched ("config incomplete in C:Users\\...").

    Second regression (0.8.31/0.8.32): the "fix" used Path('C:').anchor — but
    the anchor of a bare drive letter is still 'C:' (no root separator), so
    the check kept failing with the identical message. The separator must
    come from an explicit trailing backslash.
    """
    src = _pkg_source("winlock.py")
    assert ".anchor" not in src
    assert "endswith('\\\\')" in src
    assert "Path(os.environ.get('SystemDrive', 'C:')) / 'Users'" not in src


def test_prelogon_default_profile_root_is_absolute(tmp_path, monkeypatch):
    """The default profile root must resolve to C:\\Users, never C:Users.

    Behavioral (not just source) on purpose: 0.8.31/0.8.32 shipped
    Path('C:').anchor as the "fix", but the anchor of a bare drive letter is
    still 'C:' — a silent no-op that kept every reboot logging
    'config incomplete in C:Users\\Administrator\\...' (verbatim in the
    0.8.32 service log) and the pre-logon GUI never launched.
    """
    winlock = load_winlock()
    monkeypatch.setenv('SystemDrive', 'C:')
    monkeypatch.setattr(winlock, 'LOG_DIR', str(tmp_path))
    (Path(tmp_path) / 'last_user.txt').write_text('Administrator', encoding='utf-8')
    directory, reason = winlock.prelogon_config_state()
    assert directory is None
    assert reason.startswith('config incomplete in C:\\')
    assert 'C:Users' not in reason


def test_signin_spawn_uses_user_token_not_winlogon():
    """Post-sign-in GUI must run under the user's token, not winlogon's.

    Regression (0.8.31): every spawn used the winlogon (SYSTEM) token, so the
    signed-in GUI was SYSTEM-owned, read the SYSTEM profile (empty identity
    and pairing), and the stray cleanup killed it seconds after each launch —
    a spawn/kill loop whose visible symptom was "peer gone from the layout,
    crossing broken".
    """
    src = _pkg_source("winlock.py")
    assert "WTSQueryUserToken" in src
    spawn = src.split("def spawn_app")[1].split("\ndef ")[0]
    assert "if config_dir is None:" in spawn
    assert spawn.index("WTSQueryUserToken") < spawn.index("find_winlogon_pid")


def test_user_token_closed_via_pyhandle_not_raw_ctypes():
    """The WTSQueryUserToken handle must not be closed by raw ctypes.

    Regression (0.8.32 log): WTSQueryUserToken returns a PyHANDLE; the raw
    ctypes CloseHandle cannot convert it and raised ArgumentError from the
    finally block, discarding the successfully duplicated token — so every
    post-sign-in spawn silently fell back to the SYSTEM token and was killed
    by the stray cleanup 10 s later (spawn/kill loop, "peer gone" again).
    """
    spawn = _pkg_source("winlock.py").split("def spawn_app")[1].split("\ndef ")[0]
    assert "user_token.Close()" in spawn
    assert "K32.CloseHandle(user_token)" not in spawn


def test_terminate_races_are_tolerated():
    """Terminating an already-dead injector must not raise into the loop.

    Regression (0.8.30 log): a user flip on a wake where the injector had
    just exited surfaced ERROR_ACCESS_DENIED from TerminateProcess as a
    supervisor error instead of a clean respawn.
    """
    src = _pkg_source("winlock.py")
    assert "injector terminate skipped" in src
    assert "app handover terminate skipped" in src
    assert "GetExitCodeProcess(child) != 259" in src.split("console user changed")[0]


def test_handover_spawns_user_gui_immediately():
    """The handover must not wait for the routine 10 s app-check cadence.

    The TLS connection dies with the killed pre-logon GUI; the user GUI
    must spawn in the same supervisor iteration (0.8.34), otherwise the
    reconnect gap grows by up to 10 s of dead air on top of cold start.
    """
    src = _pkg_source("winlock.py")
    handover = src.split("handing the app over to the signed-in user")[1]
    check_gate = handover.split("last_app_check >= 10")[0]
    assert "last_app_check = 0.0" in check_gate
