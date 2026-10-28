"""A locked desktop must never strand the local pointer.

"Handoff while locked" (allow_locked) is opt-in: it keeps the machine open to
being controlled while the screen is locked. Turning it on used to also disable
the only path that released control when the screen locked (the blocked
transition), so a machine left in 'sending' kept its cursor detached — its own
mouse looked dead — and answered every incoming handoff with 'release', which
is why the peer could no longer cross in either.
"""
from mouseshare.config import Settings
from mouseshare.engine import Engine
from mouseshare.input.base import NullBackend


class LockedBackend(NullBackend):
    def __init__(self, locked=False):
        super().__init__(lambda event: True)
        self.running = True
        self.locked = locked
        self.moves = []

    def move(self, x, y):
        self.moves.append((x, y))

    def desktop_active(self):
        return not self.locked


class FakeSession:
    peer_name = "Peer"
    peer_id = "b" * 64

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


def setup(tmp_path, locked=False, allow_locked=True, reclaim_on_input=False):
    settings = Settings(tmp_path)
    settings.set(side='right', allow_locked=allow_locked, reclaim_on_input=reclaim_on_input)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0], local_id='a' * 64)
    backend = LockedBackend(locked)
    engine.backend = backend
    engine.session = FakeSession()
    engine.peer_available = True
    return engine, now, backend


def drive(engine, now, seconds=0.6):
    now[0] += seconds
    engine.tick()


def test_locked_sender_is_released(tmp_path):
    """Driving another machine from a locked screen is impossible; stop holding it."""
    engine, now, backend = setup(tmp_path)
    engine.physical({"type": "move", "x": 1919, "y": 500, "dx": 12, "dy": 0})
    engine.receive({"type": "entered"})
    assert engine.state == "sending" and backend.captured
    backend.locked = True
    drive(engine, now)
    assert engine.state == "local"
    assert not backend.captured


def test_locked_peer_stays_available_with_the_option(tmp_path):
    engine, now, backend = setup(tmp_path)
    backend.locked = True
    drive(engine, now)
    assert engine.available and not engine.system_blocked


def test_locked_peer_blocks_without_the_option(tmp_path):
    engine, now, backend = setup(tmp_path, allow_locked=False)
    backend.locked = True
    drive(engine, now)
    assert not engine.available and engine.system_blocked


def test_local_input_reclaims_control_while_locked(tmp_path):
    """Someone at a locked machine always wins, even with reclaim-on-input off."""
    engine, now, backend = setup(tmp_path, reclaim_on_input=False)
    engine.state = "receiving"
    backend.locked = True
    engine.desktop_live = False
    engine.physical({"type": "move", "x": 500, "y": 500, "dx": 4, "dy": 0})
    assert engine.state == "local"


def test_reclaim_setting_is_still_respected_when_unlocked(tmp_path):
    engine, now, backend = setup(tmp_path, reclaim_on_input=False)
    engine.state = "receiving"
    engine.physical({"type": "move", "x": 500, "y": 500, "dx": 4, "dy": 0})
    assert engine.state == "receiving"


def test_crossing_works_again_once_a_blocked_peer_returns(tmp_path):
    """Used to need a manual reset (re-applying the layout) before sharing worked."""
    engine, now, backend = setup(tmp_path)
    engine.receive({"type": "blocked"})
    engine.physical({"type": "move", "x": 1919, "y": 500, "dx": 12, "dy": 0})
    assert engine.state == "local"  # refused while the peer is blocked
    engine.receive({"type": "available"})
    assert engine.peer_available
    engine.physical({"type": "move", "x": 1919, "y": 500, "dx": 12, "dy": 0})
    assert engine.state == "pending"
