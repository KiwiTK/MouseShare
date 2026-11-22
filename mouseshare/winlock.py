"""MouseShareInputService: inject input on the Windows lock screen.

Runs two cooperating processes, both packaged in one console executable:

``--service-run``  A LocalSystem service started by the service control
    manager at boot, in session 0. It watches the active console session and
    (re)spawns the injector helper inside it, as SYSTEM. Session 0 can never
    inject input itself; the helper must live in the console session, which is
    why the service duplicates the session's winlogon.exe token.

``--injector``  A SYSTEM process inside the console session. It hosts
    \\\\.\\pipe\\MouseShareLockedInput and, for every record the app sends,
    switches its thread to the current input desktop (Winlogon when locked,
    UAC's secure desktop when elevated prompts appear, default otherwise) and
    replays the SendInput record there. This is the same mechanism VNC servers
    use to type passwords on the lock screen.

Security: the pipe DACL grants only SYSTEM and the console-session user, and
the helper verifies the client's image name before accepting events. An
unprivileged process cannot reach the pipe; a process running as the console
user could, but such a process can already inject into that user's own
interactive desktop — the pipe only extends this to the secure desktop, which
is exactly what the user opted into by installing the service.
"""
import ctypes
import ctypes.wintypes as wt
import json
import os
import sys
import time

SERVICE_NAME = 'MouseShareInputService'
SERVICE_DISPLAY = 'MouseShare Lock-screen Input Service'
PIPE_NAME = 'MouseShareLockedInput'
PIPE_PATH = '\\\\.\\pipe\\' + PIPE_NAME
LOG_DIR = os.path.join(os.environ.get('ProgramData', r'C:\ProgramData'), 'MouseShare')
LOG_PATH = os.path.join(LOG_DIR, 'service.log')

GENERIC_ALL = 0x10000000
UOI_NAME = 2
MAXIMUM_ALLOWED = 0x02000000
CREATE_NO_WINDOW = 0x08000000
# CreateEnvironmentBlock yields a Unicode environment block; CreateProcessAsUserW
# reinterprets it as ANSI unless this flag is set, which reads as invalid
# parameter (WinError 87) on every single launch.
CREATE_UNICODE_ENVIRONMENT = 0x400

K32 = ctypes.WinDLL('kernel32', use_last_error=True)
U32 = ctypes.WinDLL('user32', use_last_error=True)

K32.WTSGetActiveConsoleSessionId.restype = wt.DWORD
U32.OpenInputDesktop.restype = wt.HANDLE
U32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
U32.SetThreadDesktop.argtypes = [wt.HANDLE]
U32.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
U32.CloseDesktop.argtypes = [wt.HANDLE]
U32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]

ADV = ctypes.WinDLL('advapi32', use_last_error=True)
ADV.DuplicateTokenEx.argtypes = [wt.HANDLE, wt.DWORD, ctypes.c_void_p, wt.DWORD, wt.DWORD,
                                 ctypes.POINTER(wt.HANDLE)]
ADV.DuplicateTokenEx.restype = wt.BOOL
# CreateEnvironmentBlock / DestroyEnvironmentBlock live in userenv.dll, not advapi32.
USERENV = ctypes.WinDLL('userenv', use_last_error=True)
USERENV.CreateEnvironmentBlock.argtypes = [ctypes.POINTER(ctypes.c_void_p), wt.HANDLE, wt.BOOL]
USERENV.DestroyEnvironmentBlock.argtypes = [ctypes.c_void_p]


def log(message):
    try:
        os.makedirs(LOG_DIR, exist_ok=True)
        with open(LOG_PATH, 'a', encoding='utf-8', errors='replace') as fh:
            fh.write(f'{time.strftime("%Y-%m-%d %H:%M:%S")} [{os.getpid()}] {message}\n')
    except OSError:
        pass


# --------------------------------------------------------------------------
# INPUT reconstruction (mirrors mouseshare/input/windows.py structures)
# --------------------------------------------------------------------------
class MouseInput(ctypes.Structure):
    _fields_ = [('dx', wt.LONG), ('dy', wt.LONG), ('mouseData', wt.DWORD),
                ('dwFlags', wt.DWORD), ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]


class KeyInput(ctypes.Structure):
    _fields_ = [('wVk', wt.WORD), ('wScan', wt.WORD), ('dwFlags', wt.DWORD),
                ('time', wt.DWORD), ('dwExtraInfo', ctypes.POINTER(wt.ULONG))]


class InputUnion(ctypes.Union):
    _fields_ = [('mi', MouseInput), ('ki', KeyInput)]


class Input(ctypes.Structure):
    _anonymous_ = ('data',)
    _fields_ = [('type', wt.DWORD), ('data', InputUnion)]


def build_input(record):
    value = Input(type=record['type'])
    if record['type'] == 0:
        value.mi = MouseInput(record.get('dx', 0), record.get('dy', 0),
                              record.get('mouseData', 0), record.get('dwFlags', 0), 0, None)
    else:
        value.ki = KeyInput(record.get('wVk', 0), record.get('wScan', 0),
                            record.get('dwFlags', 0), 0, None)
    return value


def send_records(records):
    if not records:
        return True
    ensure_input_desktop()
    array = (Input * len(records))(*[build_input(r) for r in records])
    return U32.SendInput(len(records), array, ctypes.sizeof(Input)) == len(records)


_current_desktop = None
_open_desktop = None


def ensure_input_desktop():
    """Switch this thread to whatever desktop currently owns the input."""
    global _current_desktop, _open_desktop
    handle = U32.OpenInputDesktop(0, False, MAXIMUM_ALLOWED)
    if not handle:
        raise ctypes.WinError(ctypes.get_last_error())
    name = ctypes.create_unicode_buffer(256)
    needed = wt.DWORD()
    if not U32.GetUserObjectInformationW(handle, UOI_NAME, name, ctypes.sizeof(name), ctypes.byref(needed)):
        U32.CloseDesktop(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    if name.value == _current_desktop:
        U32.CloseDesktop(handle)  # already injecting here
        return
    if not U32.SetThreadDesktop(handle):
        U32.CloseDesktop(handle)
        raise ctypes.WinError(ctypes.get_last_error())
    _current_desktop = name.value
    previous, _open_desktop = _open_desktop, handle  # keep the live handle open
    if previous:
        U32.CloseDesktop(previous)
    log(f'injecting on desktop "{name.value}"')


# --------------------------------------------------------------------------
# Injector helper (SYSTEM, console session)
# --------------------------------------------------------------------------
def run_injector():
    import pywintypes
    import win32file
    import win32pipe
    import win32security

    log(f'injector starting (session={K32.WTSGetActiveConsoleSessionId()})')
    client_pid = getattr(win32pipe, 'GetNamedPipeClientProcessId', None)

    while True:
        # The DACL must encode whoever is on the console *now*. The injector
        # starts before the first sign-in (no user yet -> SYSTEM-only pipe),
        # and the supervisor restarts it on user change; recomputing here also
        # covers fast user switches between client connections.
        user_sid = console_user_sid()
        if user_sid:
            sddl = f'D:P(A;;GA;;;SY)(A;;GA;;;{user_sid})'
        else:
            sddl = 'D:P(A;;GA;;;SY)'  # pre-login: SYSTEM only, fine — nobody to serve yet
        attributes = pywintypes.SECURITY_ATTRIBUTES()
        attributes.SECURITY_DESCRIPTOR = win32security.ConvertStringSecurityDescriptorToSecurityDescriptor(sddl, 1)
        pipe = win32pipe.CreateNamedPipe(
            PIPE_PATH, win32pipe.PIPE_ACCESS_DUPLEX,
            win32pipe.PIPE_TYPE_BYTE | win32pipe.PIPE_READMODE_BYTE | win32pipe.PIPE_WAIT,
            1, 65536, 65536, 0, attributes)
        try:
            win32pipe.ConnectNamedPipe(pipe)
        except OSError as exc:
            # ERROR_PIPE_CONNECTED: the client landed between CreateNamedPipe
            # and ConnectNamedPipe — the connection is valid, keep it.
            if getattr(exc, 'winerror', None) != 535:
                win32file.CloseHandle(pipe)
                continue
        if client_pid is not None and not client_is_mouseshare(client_pid(pipe)):
            log('rejected pipe client (unknown image)')
            win32pipe.DisconnectNamedPipe(pipe)
            win32file.CloseHandle(pipe)
            continue
        log('app connected')
        buffer = b''
        try:
            while True:
                # ReadFile/CloseHandle live in win32file — win32pipe only owns
                # pipe management, and ReadFile returns (hr, data).
                hr, chunk = win32file.ReadFile(pipe, 65536)
                if hr not in (0, 234):  # ERROR_MORE_DATA keeps the payload
                    break
                if not chunk:
                    break
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    handle_line(line.decode('utf-8', 'replace'))
        except (OSError, pywintypes.error):
            pass  # client went away (unlock, app exit) — wait for the next one
        win32pipe.DisconnectNamedPipe(pipe)
        win32file.CloseHandle(pipe)
        log('app disconnected')


def handle_line(text):
    if text == 'hello' or not text.strip():
        return
    try:
        message = json.loads(text)
        if message.get('t') == 'input':
            send_records(message.get('r') or [])
    except Exception as exc:  # never let one bad record kill the helper
        log(f'bad record: {exc}')


def console_user_sid():
    try:
        import win32security
        import win32ts
        session = K32.WTSGetActiveConsoleSessionId()
        info = win32ts.WTSQuerySessionInformation(win32ts.WTS_CURRENT_SERVER_HANDLE, session, win32ts.WTSUserName)
        domain = win32ts.WTSQuerySessionInformation(win32ts.WTS_CURRENT_SERVER_HANDLE, session, win32ts.WTSDomainName)
        if not info:
            return None
        sid = win32security.LookupAccountName(None, f'{domain}\\{info}' if domain else info)[0]
        return win32security.ConvertSidToStringSid(sid)
    except Exception as exc:
        log(f'console sid lookup failed: {exc}')
        return None


def console_user_name():
    try:
        import win32ts
        session = K32.WTSGetActiveConsoleSessionId()
        return win32ts.WTSQuerySessionInformation(
            win32ts.WTS_CURRENT_SERVER_HANDLE, session, win32ts.WTSUserName) or None
    except Exception as exc:
        log(f'console name lookup failed: {exc}')
        return None


def prelogon_config_state(profile_root=None):
    """(config dir of the last console user, human-readable skip reason).

    The pre-logon GUI runs as SYSTEM, so LOCALAPPDATA points at the SYSTEM
    profile and pairing would be lost. The supervisor records the console
    user name while they are signed in; after a reboot the service points
    the pre-logon GUI back at that user's real config dir. Verifying
    settings.json + identity.key keeps a wrong profile guess harmless.

    Returns (None, reason) when the pre-logon spawn must skip — the reason
    is what the service log shows, because a silent skip here reads exactly
    like "the feature does not exist" from the outside.
    """
    import pathlib
    if profile_root is None:
        # Path('C:') is *drive-relative* (its root is empty): appending 'Users'
        # yields 'C:Users\...', which resolves against the service's working
        # directory and never matches a real profile. The anchor carries the
        # missing separator: Path('C:').anchor == 'C:\\'.
        drive = pathlib.Path(os.environ.get('SystemDrive', 'C:')).anchor or 'C:\\'
        profile_root = pathlib.Path(drive) / 'Users'
    profile_root = pathlib.Path(profile_root)
    try:
        username = pathlib.Path(LOG_DIR, 'last_user.txt').read_text(encoding='utf-8').strip()
    except OSError:
        username = ''
    checked = None
    if username and username not in ('.', '..') and \
            not any(sep in username for sep in ('/', '\\', ':')):
        checked = profile_root / username / 'AppData' / 'Local' / 'MouseShare'
        if (checked / 'settings.json').exists() and (checked / 'identity.key').exists():
            return checked, ''
    # The recorded name may be stale (account renamed, profile reshuffled,
    # GUI run under a different account). Any local profile carrying a
    # complete config is a better guess than giving up; prefer the most
    # recently used one.
    best = None
    best_mtime = -1.0
    try:
        entries = list(profile_root.iterdir())
    except OSError:
        entries = []
    for profile in entries:
        directory = profile / 'AppData' / 'Local' / 'MouseShare'
        try:
            mtime = (directory / 'settings.json').stat().st_mtime
        except OSError:
            continue
        if mtime > best_mtime and (directory / 'identity.key').exists():
            best, best_mtime = directory, mtime
    if best is not None:
        return best, ''
    if checked is not None:
        return None, f'config incomplete in {checked}'
    if username:
        return None, f'unusable last user name {username!r}'
    return None, 'last_user.txt missing or empty'


def prelogon_config_dir(profile_root=None):
    directory, _reason = prelogon_config_state(profile_root)
    return directory


def local_system_sid():
    """String form of the LOCAL SYSTEM account SID, or None off-Windows."""
    try:
        import win32security
        return win32security.ConvertSidToStringSid(
            win32security.LookupAccountName(None, 'SYSTEM')[0])
    except Exception:
        return None


def system_app_pids(entries, session, system_sid):
    """pids of MouseShare.exe copies running as SYSTEM in the console session.

    The pre-logon GUI runs as SYSTEM; normally the sign-in handover kills it
    via the tracked handle. A service restart in between loses that handle,
    leaving an orphan that then coexists with the signed-in user's GUI —
    both share one identity, one of them loses the input-port bind and shows
    as "未连接" forever.
    """
    pids = []
    for entry in entries:
        if entry[0] != session or str(entry[2]).casefold() != 'mouseshare.exe':
            continue
        try:
            import win32security
            if win32security.ConvertSidToStringSid(entry[3]) == system_sid:
                pids.append(entry[1])
        except Exception:
            continue
    return pids


# --------------------------------------------------------------------------
# Launching the app at sign-in (matches how "无界鼠标" is instantly usable)
# --------------------------------------------------------------------------
class STARTUPINFOW(ctypes.Structure):
    _fields_ = [('cb', wt.DWORD), ('lpReserved', wt.LPWSTR), ('lpDesktop', wt.LPWSTR),
                ('lpTitle', wt.LPWSTR), ('dwX', wt.DWORD), ('dwY', wt.DWORD),
                ('dwXSize', wt.DWORD), ('dwYSize', wt.DWORD), ('dwXCountChars', wt.DWORD),
                ('dwYCountChars', wt.DWORD), ('dwFillAttribute', wt.DWORD), ('dwFlags', wt.DWORD),
                ('wShowWindow', wt.WORD), ('cbReserved2', wt.WORD), ('lpReserved2', ctypes.c_void_p),
                ('hStdInput', wt.HANDLE), ('hStdOutput', wt.HANDLE), ('hStdError', wt.HANDLE)]


class PROCESS_INFORMATION(ctypes.Structure):
    _fields_ = [('hProcess', wt.HANDLE), ('hThread', wt.HANDLE),
                ('dwProcessId', wt.DWORD), ('dwThreadId', wt.DWORD)]


ADV.CreateProcessAsUserW.argtypes = [wt.HANDLE, wt.LPCWSTR, wt.LPWSTR, ctypes.c_void_p,
                                     ctypes.c_void_p, wt.BOOL, wt.DWORD, ctypes.c_void_p,
                                     wt.LPCWSTR, ctypes.POINTER(STARTUPINFOW),
                                     ctypes.POINTER(PROCESS_INFORMATION)]


def app_image_path():
    return os.path.join(os.path.dirname(sys.executable), 'MouseShare.exe')


def session_has_app(entries, session):
    """True when MouseShare.exe is already running in the console session."""
    for entry in entries:
        if entry[0] == session and str(entry[2]).casefold() == 'mouseshare.exe':
            return True
    return False


def duplicate_primary_token(token):
    """Duplicate a token into a primary token via the raw advapi32 call.

    pywin32's DuplicateTokenEx wrapper kept failing in different ways per
    build (one rejected None for the attributes slot, the next treated a
    plain int as a PySECURITY_ATTRIBUTES object), so the service calls the
    API directly with explicit types and the documented order:
    (token, desiredAccess, attrs, impersonationLevel, tokenType, out).
    """
    primary = wt.HANDLE()
    if not ADV.DuplicateTokenEx(wt.HANDLE(int(token)), MAXIMUM_ALLOWED, None,
                                2,   # SecurityImpersonation
                                1,   # PrimaryToken
                                ctypes.byref(primary)):
        raise ctypes.WinError(ctypes.get_last_error())
    return primary


def spawn_app(entries, session, config_dir=None):
    """Start the GUI in the console session the moment someone signs in.

    The HKCU Run key only fires once Explorer has worked through its startup
    queue, which reads as "MouseShare takes forever after boot". The service
    is already alive by then, so it starts the app from the freshly signed-in
    winlogon token, the same way spawn_injector places the helper.

    The environment block is mandatory: CreateProcessAsUser inherits the
    service's environment, where %LOCALAPPDATA% still points at the SYSTEM
    profile and the app would silently lose its config, identity and pairing.

    config_dir is used for the pre-logon spawn: the app runs as SYSTEM with
    LOCALAPPDATA pointing at the SYSTEM profile, so the user's real config
    directory (identity, pairing, allow_locked) is passed explicitly.
    Returns the child process handle (0 when nothing was started).
    """
    import win32api
    import win32con
    import win32security

    exe = app_image_path()
    if not os.path.exists(exe):
        return 0  # portable layout without the GUI next to the service
    host_pid = find_winlogon_pid(entries, session)
    if host_pid is None:
        return 0
    process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, host_pid)
    try:
        token = win32security.OpenProcessToken(
            process, win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ASSIGN_PRIMARY)
        primary = duplicate_primary_token(token)
    finally:
        win32api.CloseHandle(process)
    # Python 3.13 removed int() support on ctypes c_void_p (it chokes on the
    # raw buffer bytes); the handle must go through .value explicitly.
    env = ctypes.c_void_p()
    if not USERENV.CreateEnvironmentBlock(ctypes.byref(env), primary.value or 0, False):
        log('app: environment block unavailable; launching with service env')
        env = None
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.lpDesktop = 'WinSta0\\Default'
    info = PROCESS_INFORMATION()
    command = f'"{exe}"' if not config_dir else f'"{exe}" --config-dir "{config_dir}"'
    command = ctypes.create_unicode_buffer(command)
    try:
        if ADV.CreateProcessAsUserW(primary.value or 0, exe, command, None, None, False,
                                    CREATE_UNICODE_ENVIRONMENT | CREATE_NO_WINDOW, env, None,
                                    ctypes.byref(startup), ctypes.byref(info)):
            log(f'app launched in session {session} (pid {info.dwProcessId})')
            if info.hThread:
                K32.CloseHandle(info.hThread)
            return info.hProcess or 0
        log(f'app launch failed: {ctypes.WinError(ctypes.get_last_error())}')
        if info.hThread:
            K32.CloseHandle(info.hThread)
        if info.hProcess:
            K32.CloseHandle(info.hProcess)
        return 0
    finally:
        K32.CloseHandle(primary)
        if env:
            USERENV.DestroyEnvironmentBlock(env)


def client_is_mouseshare(pid):
    try:
        import win32api
        import win32con
        import win32process
        # OpenProcess lives in win32api, not win32process.
        handle = win32api.OpenProcess(
            win32con.PROCESS_QUERY_INFORMATION | win32con.PROCESS_VM_READ, False, pid)
        try:
            image = win32process.GetModuleFileNameEx(handle, 0).casefold()
        finally:
            win32api.CloseHandle(handle)
        return image.endswith('mouseshare.exe')
    except Exception as exc:
        log(f'image check failed for pid {pid}: {exc}')
        return False


# --------------------------------------------------------------------------
# Service (LocalSystem, session 0)
# --------------------------------------------------------------------------
_stop = None


def run_service():
    import servicemanager
    import win32event
    import win32service
    import win32serviceutil
    import winerror

    class Service(win32serviceutil.ServiceFramework):
        _svc_name_ = SERVICE_NAME
        _svc_display_name_ = SERVICE_DISPLAY
        _svc_description_ = ('Lets MouseShare control this computer while it is locked or '
                             'before anyone signs in.')

        def __init__(self, args):
            super().__init__(args)
            self.halt = win32event.CreateEvent(None, 0, 0, None)

        def SvcStop(self):
            self.ReportServiceStatus(win32service.SERVICE_STOP_PENDING)
            win32event.SetEvent(self.halt)

        def SvcDoRun(self):
            global _stop
            _stop = self.halt
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STARTED, (self._svc_name_, ''))
            supervisor(self.halt)
            servicemanager.LogMsg(servicemanager.EVENTLOG_INFORMATION_TYPE,
                                  servicemanager.PYS_SERVICE_STOPPED, (self._svc_name_, ''))

    # Reachable here only when hosted by the SCM (install writes --service-run
    # into binPath) or from `python -m mouseshare.winlock --service-run` for
    # debugging. Manage install/uninstall via HandleCommandLine equivalents in
    # install()/uninstall()/status() instead.
    servicemanager.Initialize()
    servicemanager.PrepareToHostSingle(Service)
    try:
        servicemanager.StartServiceCtrlDispatcher()
    except win32service.error as exc:
        if exc.winerror == winerror.ERROR_FAILED_SERVICE_CONTROLLER_CONNECT:
            print(f'not running under the service control manager; install first: {exe_hint()} --install')
    return


def exe_hint():
    return sys.executable if getattr(sys, 'frozen', False) else 'python -m mouseshare.winlock'


def supervisor(halt):
    """Keep one injector alive in the active console session until the service stops."""
    import win32api
    import win32con
    import win32event
    import win32process
    import win32ts

    child = None
    app_child = None  # pre-logon SYSTEM GUI; terminated once someone signs in
    last_sid = None
    last_app_check = 0.0
    prelogon_skip_reason = None
    system_sid = local_system_sid()
    while win32event.WaitForSingleObject(halt, 2000) != 0:  # wake every 2 s
        handle = 0
        try:
            sid = console_user_sid()
            # Reap a dead injector BEFORE handling user changes: terminating
            # an already-exited process fails with ERROR_ACCESS_DENIED, and a
            # user flip on the same wake used to surface that as a supervisor
            # error and delay the respawn.
            if child is not None and win32process.GetExitCodeProcess(child) != 259:  # STILL_ACTIVE
                log('injector exited; respawning')
                win32api.CloseHandle(child)
                child = None
            if child is not None and sid != last_sid:
                # The injector's pipe DACL encodes the console user captured at
                # its startup. A boot-time injector serves SYSTEM only, so once
                # someone signs in the app could never open the pipe: restart
                # the helper so it is rebuilt for the signed-in user.
                log('console user changed; restarting injector')
                try:
                    win32api.TerminateProcess(child, 0)
                except Exception as exc:
                    log(f'injector terminate skipped: {exc}')
                win32api.CloseHandle(child)
                child = None
            if app_child is not None and sid != last_sid:
                # The pre-logon GUI runs as SYSTEM. At sign-in hand the job
                # over: kill it and let the app check below relaunch the GUI
                # inside the user's own token.
                log('handing the app over to the signed-in user')
                try:
                    win32api.TerminateProcess(app_child, 0)
                except Exception as exc:
                    log(f'app handover terminate skipped: {exc}')
                win32api.CloseHandle(app_child)
                app_child = None
                prelogon_skip_reason = None
            last_sid = sid
            if sid:
                username = console_user_name()
                if username:
                    try:
                        with open(os.path.join(LOG_DIR, 'last_user.txt'), 'w',
                                  encoding='utf-8') as fh:
                            fh.write(username)
                    except OSError:
                        pass
            if child is None:
                exe = sys.executable
                if not os.path.exists(PIPE_PATH):
                    child = spawn_injector(exe)
            # Keep exactly one GUI alive in the console session: launched with
            # the user's token after sign-in, as SYSTEM (pointed at the last
            # user's config) before anyone signs in — so the lock screen after
            # a reboot can be driven from the other computer too.
            if time.time() - last_app_check >= 10:
                last_app_check = time.time()
                session = K32.WTSGetActiveConsoleSessionId()
                if session:
                    entries = win32ts.WTSEnumerateProcesses(win32ts.WTS_CURRENT_SERVER_HANDLE)
                    if not session_has_app(entries, session):
                        if sid:
                            handle = spawn_app(entries, session)
                        else:
                            directory, reason = prelogon_config_state()
                            if directory:
                                handle = spawn_app(entries, session, config_dir=str(directory))
                                if handle:
                                    log('pre-logon app running; crossing works before sign-in')
                            elif reason != prelogon_skip_reason:
                                # Log once per distinct reason: a silent skip
                                # here reads exactly like "feature missing".
                                prelogon_skip_reason = reason
                                log(f'pre-logon GUI skipped: {reason}')
                        if handle:
                            if sid:
                                K32.CloseHandle(handle)  # user-context GUI needs no tracking
                            else:
                                app_child = handle
                    elif sid and system_sid:
                        # Signed in with an app present: kill any SYSTEM-owned
                        # copy — a signed-in desktop must never share the
                        # session with the pre-logon GUI (handle tracking can
                        # miss it after a service restart).
                        for pid in system_app_pids(entries, session, system_sid):
                            log(f'terminating stray pre-logon GUI (pid {pid})')
                            try:
                                proc = win32api.OpenProcess(win32con.PROCESS_TERMINATE, False, pid)
                                win32api.TerminateProcess(proc, 0)
                                win32api.CloseHandle(proc)
                            except Exception as exc:
                                log(f'stray kill failed: {exc}')
        except Exception as exc:
            log(f'supervisor error: {exc}')
    if child is not None:
        win32api.TerminateProcess(child, 0)
        win32api.CloseHandle(child)
    if app_child is not None:
        win32api.TerminateProcess(app_child, 0)
        win32api.CloseHandle(app_child)


def find_winlogon_pid(entries, session):
    """Return the pid of winlogon.exe running in the active console session.

    WTSEnumerateProcesses rows are (session_id, pid, image_name, user_sid);
    the fourth element is a PySID object, never the image name.
    """
    for entry in entries:
        if entry[0] == session and str(entry[2]).casefold() == 'winlogon.exe':
            return entry[1]
    return None


def spawn_injector(exe):
    import win32api
    import win32con
    import win32security
    import win32ts

    session = K32.WTSGetActiveConsoleSessionId()
    if not session:
        return None
    host_pid = find_winlogon_pid(
        win32ts.WTSEnumerateProcesses(win32ts.WTS_CURRENT_SERVER_HANDLE), session)
    if host_pid is None:
        log('no winlogon.exe in the console session yet')
        return None
    process = win32api.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, host_pid)
    try:
        token = win32security.OpenProcessToken(
            process, win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ASSIGN_PRIMARY)
        primary = duplicate_primary_token(token)
    finally:
        win32api.CloseHandle(process)
    startup = STARTUPINFOW()
    startup.cb = ctypes.sizeof(STARTUPINFOW)
    startup.lpDesktop = 'WinSta0\\Default'
    info = PROCESS_INFORMATION()
    command = ctypes.create_unicode_buffer(f'"{exe}" --injector')
    try:
        if ADV.CreateProcessAsUserW(primary.value or 0, None, command, None, None, False,
                                    CREATE_NO_WINDOW, None, None,
                                    ctypes.byref(startup), ctypes.byref(info)):
            log(f'injector spawned in session {session}')
            # Structure fields of c_void_p come back as plain int (or None),
            # unlike standalone instances which still expose .value.
            if info.hThread:
                K32.CloseHandle(info.hThread)
            return info.hProcess or 0
        log(f'spawn failed: {ctypes.WinError(ctypes.get_last_error())}')
    except Exception as exc:
        log(f'spawn failed: {exc}')
    finally:
        K32.CloseHandle(primary)
    return None


# --------------------------------------------------------------------------
# Install / uninstall / status (must run elevated)
# --------------------------------------------------------------------------
def install():
    import win32service
    import win32serviceutil

    exe = sys.executable
    log(f'install requested from {exe}')
    # Upgrade path: drop any previously registered service first, so its
    # start type (e.g. an old DEMAND_START install) gets rewritten too.
    try:
        win32serviceutil.StopService(SERVICE_NAME)
        log('install: old service stopped')
    except win32service.error as exc:
        log(f'install: stop old service: {exc}')
    try:
        win32serviceutil.RemoveService(SERVICE_NAME)
        log('install: old service removed')
    except win32service.error as exc:
        log(f'install: remove old service: {exc}')
    # Without an explicit startType pywin32 defaults to SERVICE_DEMAND_START,
    # which means the SCM never starts the service after a reboot.
    try:
        win32serviceutil.InstallService(exeName=exe, exeArgs='--service-run',
                                        pythonClassString='', serviceName=SERVICE_NAME,
                                        displayName=SERVICE_DISPLAY,
                                        startType=win32service.SERVICE_AUTO_START)
        log('install: service registered with AUTO_START')
    except Exception as exc:
        log(f'install: FAILED to register service: {exc!r}')
        raise
    try:
        win32serviceutil.StartService(SERVICE_NAME)
        log('install: service started')
    except Exception as exc:
        log(f'install: start after install failed: {exc}')
    print(f'{SERVICE_NAME} installed from {exe}')


def uninstall():
    import win32serviceutil
    try:
        win32serviceutil.StopService(SERVICE_NAME)
    except Exception:
        pass
    win32serviceutil.RemoveService(SERVICE_NAME)
    print(f'{SERVICE_NAME} removed')


def status():
    try:
        import win32service
        import win32serviceutil
        state = win32serviceutil.QueryServiceStatus(SERVICE_NAME)[1]
        names = {win32service.SERVICE_RUNNING: 'running', win32service.SERVICE_STOPPED: 'stopped',
                 win32service.SERVICE_START_PENDING: 'starting', win32service.SERVICE_STOP_PENDING: 'stopping'}
        print(names.get(state, f'state {state}'))
        return 0
    except Exception:
        print('not installed')
        return 1


def main():
    argv = sys.argv[1:]
    if '--service-run' in argv or (not argv and getattr(sys, 'frozen', False)):
        run_service()
    elif '--injector' in argv:
        run_injector()
    elif '--install' in argv:
        install()
    elif '--uninstall' in argv:
        uninstall()
    elif '--status' in argv:
        return status()
    else:
        print('usage: MouseShareService.exe --install | --uninstall | --status | --service-run | --injector')
        return 2
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
