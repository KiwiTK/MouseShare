"""A secure-desktop OSError must not latch a permanent pause.

Win+L moves Windows to the Winlogon desktop, where user-mode SendInput and the
Qt cursor shield both fail. Those failures are temporary; the engine used to
turn them into paused=True forever, leaving the UI at "connected but sharing
stopped" until the user clicked resume or restarted the app.
"""
from mouseshare.config import Settings
from mouseshare.engine import Engine
from mouseshare.input.base import NullBackend


class FailingBackend(NullBackend):
    def __init__(self, enqueue):
        super().__init__(enqueue)
        self.running = True
        self.locked = False
        self.moves = []
        self.fail_move = False

    def desktop_active(self):
        return not self.locked

    def move(self, x, y):
        if self.fail_move:
            raise OSError('SendInput blocked')
        self.moves.append((x, y))


class FakeSession:
    peer_name = 'Peer'
    peer_id = 'b' * 64

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


def setup(tmp_path, locked=False, allow_locked=False):
    settings = Settings(tmp_path)
    settings.set(side='right', allow_locked=allow_locked)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0], local_id='a' * 64)
    backend = FailingBackend(engine.enqueue)
    backend.locked = locked
    engine.backend = backend
    engine.session = FakeSession()
    engine.peer_available = True
    return engine, now, backend


def drive(engine, now, seconds=0.6):
    now[0] += seconds
    engine.tick()


def test_oserror_on_locked_desktop_blocks_instead_of_pausing(tmp_path):
    engine, now, backend = setup(tmp_path, locked=True)
    engine.handle_failure(OSError('SendInput blocked'))
    assert engine.system_blocked and not engine.paused and not engine.auto_paused
    assert not engine.available and not engine.error
    assert any(m['type'] == 'blocked' for m in engine.session.messages)
    # Unlock: tick() lifts the block, no click on "resume" needed.
    backend.locked = False
    drive(engine, now)
    assert not engine.system_blocked and engine.available
    assert engine.session.messages[-1]['type'] == 'available'


def test_transient_oserror_auto_resumes(tmp_path):
    """Cursor-shield timeouts happen with the desktop still active."""
    engine, now, backend = setup(tmp_path)
    engine.handle_failure(OSError('cursor shield timeout'))
    assert engine.paused and engine.auto_paused
    now[0] += 6
    engine.tick()
    assert not engine.paused and not engine.auto_paused and not engine.error
    assert engine.available
    assert engine.session.messages[-1]['type'] == 'available'


def test_manual_pause_is_never_auto_resumed(tmp_path):
    engine, now, backend = setup(tmp_path)
    engine.handle({'type': 'command', 'action': 'pause'})
    assert engine.paused and not engine.auto_paused
    now[0] += 6
    engine.tick()
    assert engine.paused


def test_resume_click_clears_an_automatic_pause(tmp_path):
    engine, now, backend = setup(tmp_path)
    engine.handle_failure(OSError('x'))
    engine.handle({'type': 'command', 'action': 'pause'})
    assert not engine.paused and not engine.auto_paused


def test_release_survives_cursor_restore_failure(tmp_path):
    """A second OSError inside the exception handler used to kill the thread."""
    engine, now, backend = setup(tmp_path)
    engine.state = 'sending'
    backend.fail_move = True
    engine.release(True)
    assert engine.state == 'local'


def test_enter_refused_before_injecting_into_secure_desktop(tmp_path):
    engine, now, backend = setup(tmp_path, locked=True)
    session = engine.session
    engine.receive({'type': 'enter', 'edge': 'right', 'position': 0.5}, session)
    assert engine.state == 'local' and not backend.moves
    assert session.messages[-1]['type'] == 'release'


def test_allow_locked_still_accepts_an_enter(tmp_path):
    engine, now, backend = setup(tmp_path, locked=True, allow_locked=True)
    session = engine.session
    engine.receive({'type': 'enter', 'edge': 'right', 'position': 0.5}, session)
    assert engine.state == 'receiving' and backend.moves
