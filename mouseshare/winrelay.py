"""Relay that lets MouseShare inject input while the Windows desktop is locked.

Windows rejects user-session SendInput on the Winlogon secure desktop (and the
UAC desktop), so a LocalSystem helper running inside the console session does
the injecting instead. The helper is created by the MouseShareInputService and
hosts a named pipe; this module is the app-side client plus the wire format.

The wire format is deliberately plain JSON lines of INPUT-struct fields, so
both sides can be unit-tested without a live desktop:
    {"t": "input", "r": [{"type": 0, "dx": .., "dy": .., "mouseData": ..,
                          "dwFlags": ..},
                         {"type": 1, "wVk": .., "wScan": .., "dwFlags": ..}]}
"""
import json
import sys
import time

PIPE_NAME = 'MouseShareLockedInput'
PIPE_PATH = '\\\\.\\pipe\\' + PIPE_NAME


def serialize_input(value):
    """Flatten a windows Input struct into a JSON-safe dict (None if not injectable)."""
    kind = value.type
    if kind == 0:
        mi = value.mi
        return {'type': 0, 'dx': mi.dx, 'dy': mi.dy,
                'mouseData': mi.mouseData & 0xFFFFFFFF, 'dwFlags': mi.dwFlags}
    if kind == 1:
        ki = value.ki
        return {'type': 1, 'wVk': ki.wVk, 'wScan': ki.wScan, 'dwFlags': ki.dwFlags}
    return None


class LockedRelay:
    """Best-effort JSON-lines client for the lock-screen input pipe.

    Never raises: the caller (the input-owner thread) must keep running even
    when the service is absent, so every failure degrades to "relay offline"
    with a 1 s reconnect throttle, and direct SendInput takes over again the
    moment desktop_active() reports the interactive desktop is back.
    """

    def __init__(self, open_file=None, clock=time.monotonic):
        self._open = open_file or self._open_pipe
        self._clock = clock
        self._fh = None
        self._last_fail = 0.0
        self.sent = 0

    @staticmethod
    def _open_pipe():
        # Only meaningful on Windows; callers gate this class on win32 anyway.
        if sys.platform != 'win32':
            raise OSError('not windows')
        return open(PIPE_PATH, 'r+b', buffering=0)  # noqa: SIM115 (pipe, kept open)

    def ready(self):
        return self._fh is not None

    def connect(self):
        now = self._clock()
        if now - self._last_fail < 1.0:
            return False
        try:
            fh = self._open()
            fh.write(b'hello\n')  # handshake: server validates the client image
            fh.flush()
        except OSError:
            self._last_fail = now
            return False
        self._fh = fh
        return True

    def send_input(self, records):
        if not records:
            return True
        line = (json.dumps({'t': 'input', 'r': records}, separators=(',', ':')) + '\n').encode()
        if self._fh is None and not self.connect():
            return False
        try:
            self._fh.write(line)
        except OSError:
            self._drop()
            return False
        self.sent += 1
        return True

    def close(self):
        self._drop()

    def _drop(self):
        if self._fh is not None:
            try:
                self._fh.close()
            except OSError:
                pass
        self._fh = None
