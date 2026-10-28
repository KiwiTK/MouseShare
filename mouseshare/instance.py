"""Elect one long-lived instance per user.

Login can start two copies milliseconds apart: one from the launch agent and one
from macOS reopening the application.  Probing the local socket cannot tell them
apart because neither is listening yet, so ownership is decided by an atomic lock
file that records the process id, and the loser then waits for the winner's socket
instead of starting a second copy.
"""
import os
import time

from PySide6.QtNetwork import QLocalSocket

from .config import data_dir

# How long to wait for an instance that is already starting up.
START_TIMEOUT = 10.0
# How long a cold start may spend looking for a socket that should not exist.
PROBE_TIMEOUT = 0.4


def lock_file(name):
    return data_dir() / f"instance-{name}.lock"


def owner(path):
    """Return the recorded process id, or None when that process is gone."""
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return None
    if pid <= 0 or pid == os.getpid():
        return None
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return None
    except PermissionError:
        pass
    except OSError:
        return None
    return pid


def claim(path):
    """Take ownership atomically. False means another copy got there first."""
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        handle = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except (FileExistsError, OSError):
        return False
    with os.fdopen(handle, "w") as stream:
        stream.write(str(os.getpid()))
    return True


def release(path):
    """Drop ownership, but never a lock that now belongs to another copy."""
    try:
        pid = int(path.read_text().strip())
    except (OSError, ValueError):
        return
    if pid == os.getpid():
        discard(path)


def discard(path):
    try:
        path.unlink()
    except OSError:
        pass


def forward(name, payload, timeout):
    """Hand a request to the running instance, retrying while it comes up."""
    deadline = time.monotonic() + timeout
    while True:
        client = QLocalSocket()
        client.connectToServer(name)
        if client.waitForConnected(300):
            client.write(payload)
            client.waitForBytesWritten(300)
            client.disconnectFromServer()
            return True
        client.abort()
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.1)
