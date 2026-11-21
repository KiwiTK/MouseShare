"""Regression: the supervisor must locate winlogon.exe to spawn the injector.

The real bug only reproduces on Windows: WTSEnumerateProcesses rows are
(session_id, pid, image_name, user_sid) and the fourth column is a PySID
object, which has no `.casefold()`. The old code read the SID column as the
image name, so every spawn attempt crashed every 10 seconds and the
lock-screen pipe never existed.
"""
import sys
from types import SimpleNamespace


class FakeDll:
    """Stand-in for ctypes.WinDLL: any attribute read yields a stub object."""

    def __getattr__(self, name):
        obj = SimpleNamespace()
        object.__setattr__(self, name, obj)
        return obj


def load_winlock():
    """Import mouseshare.winlock without a real Windows DLL layer."""
    import ctypes

    sys.modules.pop('mouseshare.winlock', None)
    # ctypes resolves WinDLL lazily (and only on Windows), so mock.patch
    # cannot intercept it: assign directly and restore afterwards.
    ctypes.WinDLL = lambda name, **kwargs: FakeDll()
    try:
        import mouseshare.winlock as winlock
        return winlock
    finally:
        del ctypes.WinDLL


class FakeSid:
    """Mimics PySID: comparable, hashable, no string methods."""

    def __eq__(self, other):
        return isinstance(other, FakeSid)

    def __ne__(self, other):
        return not isinstance(other, FakeSid)

    def __hash__(self):
        return 1


def test_find_winlogon_pid_uses_the_image_name_column():
    winlock = load_winlock()
    sid = FakeSid()
    entries = [
        (1, 100, 'explorer.exe', sid),
        (1, 432, 'winlogon.exe', sid),
        (1, 433, 'WINLOGON.EXE', sid),   # case must not matter
        (2, 500, 'winlogon.exe', sid),   # wrong session
    ]
    assert winlock.find_winlogon_pid(entries, 1) == 432


def test_find_winlogon_pid_returns_none_when_absent():
    winlock = load_winlock()
    entries = [(1, 100, 'explorer.exe', FakeSid())]
    assert winlock.find_winlogon_pid(entries, 1) is None


def test_find_winlogon_pid_survives_pysid_in_name_slot():
    """The old bug: reading entry[3] (a PySID) crashed with AttributeError."""
    winlock = load_winlock()
    entries = [(1, 432, 'winlogon.exe', FakeSid())]
    assert winlock.find_winlogon_pid(entries, 1) == 432
