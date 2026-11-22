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
    for username in (r"..\other", "a/b", "C:", ""):
        (Path(tmp_path) / "last_user.txt").write_text(username, encoding="utf-8")
        assert winlock.prelogon_config_dir(profile_root=tmp_path) is None, username


def test_prelogon_config_dir_without_record(tmp_path, monkeypatch):
    winlock = load_winlock()
    monkeypatch.setattr(winlock, "LOG_DIR", str(tmp_path))
    assert winlock.prelogon_config_dir(profile_root=tmp_path) is None
