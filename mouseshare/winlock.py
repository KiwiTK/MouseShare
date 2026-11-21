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

K32 = ctypes.WinDLL('kernel32', use_last_error=True)
U32 = ctypes.WinDLL('user32', use_last_error=True)

K32.WTSGetActiveConsoleSessionId.restype = wt.DWORD
U32.OpenInputDesktop.restype = wt.HANDLE
U32.OpenInputDesktop.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
U32.SetThreadDesktop.argtypes = [wt.HANDLE]
U32.GetUserObjectInformationW.argtypes = [wt.HANDLE, ctypes.c_int, ctypes.c_void_p, wt.DWORD, ctypes.POINTER(wt.DWORD)]
U32.CloseDesktop.argtypes = [wt.HANDLE]
U32.SendInput.argtypes = [wt.UINT, ctypes.c_void_p, ctypes.c_int]


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
        except OSError:
            win32pipe.CloseHandle(pipe)
            continue
        if client_pid is not None and not client_is_mouseshare(client_pid(pipe)):
            log('rejected pipe client (unknown image)')
            win32pipe.DisconnectNamedPipe(pipe)
            win32pipe.CloseHandle(pipe)
            continue
        log('app connected')
        buffer = b''
        try:
            while True:
                chunk = win32pipe.ReadFile(pipe, 65536)
                if not chunk:
                    break
                buffer += chunk
                while b'\n' in buffer:
                    line, buffer = buffer.split(b'\n', 1)
                    handle_line(line.decode('utf-8', 'replace'))
        except (OSError, pywintypes.error):
            pass  # client went away (unlock, app exit) — wait for the next one
        win32pipe.DisconnectNamedPipe(pipe)
        win32pipe.CloseHandle(pipe)
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


def client_is_mouseshare(pid):
    try:
        import win32api
        import win32process
        handle = win32process.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
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
    import win32event
    import win32process

    child = None
    last_sid = None
    while win32event.WaitForSingleObject(halt, 10000) != 0:  # wake every 10 s
        try:
            sid = console_user_sid()
            if child is not None and sid != last_sid:
                # The injector's pipe DACL encodes the console user captured at
                # its startup. A boot-time injector serves SYSTEM only, so once
                # someone signs in the app could never open the pipe: restart
                # the helper so it is rebuilt for the signed-in user.
                log('console user changed; restarting injector')
                win32api.TerminateProcess(child, 0)
                win32api.CloseHandle(child)
                child = None
            last_sid = sid
            if child is not None and win32process.GetExitCodeProcess(child) != 259:  # STILL_ACTIVE
                log('injector exited; respawning')
                win32api.CloseHandle(child)
                child = None
            if child is None:
                exe = sys.executable
                if not os.path.exists(PIPE_PATH):
                    child = spawn_injector(exe)
        except Exception as exc:
            log(f'supervisor error: {exc}')
    if child is not None:
        win32api.TerminateProcess(child, 0)
        win32api.CloseHandle(child)


def spawn_injector(exe):
    import win32api
    import win32con
    import win32process
    import win32security
    import win32ts

    session = K32.WTSGetActiveConsoleSessionId()
    if not session:
        return None
    host_pid = None
    for entry in win32ts.WTSEnumerateProcesses(win32ts.WTS_CURRENT_SERVER_HANDLE):
        if entry[0] == session and entry[3].casefold() == 'winlogon.exe':
            host_pid = entry[1]
            break
    if host_pid is None:
        log('no winlogon.exe in the console session yet')
        return None
    process = win32process.OpenProcess(win32con.PROCESS_QUERY_LIMITED_INFORMATION, False, host_pid)
    try:
        token = win32security.OpenProcessToken(
            process, win32con.TOKEN_DUPLICATE | win32con.TOKEN_QUERY | win32con.TOKEN_ASSIGN_PRIMARY)
        primary = win32security.DuplicateTokenEx(token, win32security.MAXIMUM_ALLOWED, None,
                                                 win32security.SecurityImpersonation,
                                                 win32security.SecurityPrimaryToken)
    finally:
        win32api.CloseHandle(process)
    startup = win32process.STARTUPINFO()
    startup.lpDesktop = 'WinSta0\\Default'
    startup.dwFlags = win32con.STARTF_USESHOWWINDOW
    startup.wShowWindow = 0
    try:
        child, _, _, _ = win32process.CreateProcessAsUser(
            primary, None, f'"{exe}" --injector', None, None, False,
            CREATE_NO_WINDOW, None, None, startup)
        log(f'injector spawned in session {session}')
        return child
    except Exception as exc:
        log(f'spawn failed: {exc}')
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
