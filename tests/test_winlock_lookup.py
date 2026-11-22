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


def test_session_has_app_matches_by_image_name():
    winlock = load_winlock()
    sid = FakeSid()
    entries = [
        (1, 100, 'explorer.exe', sid),
        (1, 300, 'MouseShare.exe', sid),
        (1, 500, 'MouseShareService.exe', sid),  # service exe must not count
    ]
    assert winlock.session_has_app(entries, 1)
    assert not winlock.session_has_app(entries, 2)
    assert not winlock.session_has_app([(1, 100, 'explorer.exe', sid)], 1)


def _fake_pywin32(monkeypatch):
    """Install pywin32-shaped stub modules with the real module layout:
    OpenProcess lives in win32api; win32process has no such attribute."""
    import pathlib
    import types

    api = types.ModuleType('win32api')
    proc = types.ModuleType('win32process')
    con = types.ModuleType('win32con')
    con.PROCESS_QUERY_INFORMATION = 0x400
    con.PROCESS_VM_READ = 0x10

    opened = []
    closed = []

    def open_process(access, inherit, pid):
        opened.append((access, pid))
        return (f'handle-{pid}',)

    def get_module_file_name_ex(handle, module):
        pid = handle[0].split('-')[1]
        if pid == '300':
            return 'C:\\Program Files\\MouseShare\\MouseShare.exe'
        return 'C:\\Windows\\System32\\notepad.exe'

    api.OpenProcess = open_process
    api.CloseHandle = closed.append
    proc.GetModuleFileNameEx = get_module_file_name_ex
    monkeypatch.setitem(sys.modules, 'win32api', api)
    monkeypatch.setitem(sys.modules, 'win32process', proc)
    monkeypatch.setitem(sys.modules, 'win32con', con)
    return opened, closed, pathlib


def test_client_is_mouseshare_matches_image_name(monkeypatch):
    winlock = load_winlock()
    opened, closed, _ = _fake_pywin32(monkeypatch)
    assert winlock.client_is_mouseshare(300)
    assert opened == [(0x400 | 0x10, 300)]
    assert len(closed) == 1


def test_client_is_mouseshare_rejects_other_images(monkeypatch):
    winlock = load_winlock()
    _fake_pywin32(monkeypatch)
    assert not winlock.client_is_mouseshare(500)  # app500.exe


def test_no_misattributed_pywin32_api_in_source():
    """Guard: pywin32 constants used in winlock must live in the right module.

    OpenProcess lives in win32api; MAXIMUM_ALLOWED / SecurityPrimaryToken are
    not exported by win32security (real bugs hit on Windows only)."""
    winlock = load_winlock()
    import pathlib
    src = pathlib.Path(winlock.__file__).read_text(encoding='utf-8')
    for pattern in ('win32process.OpenProcess',
                    'win32security.MAXIMUM_ALLOWED',
                    'win32security.SecurityPrimaryToken',
                    'DuplicateTokenEx(token, MAXIMUM_ALLOWED, None'):
        assert pattern not in src, pattern
