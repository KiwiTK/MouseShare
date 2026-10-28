"""A capture must never outlive its handoff.

Capture is what suppresses the local mouse and keyboard while a peer is being
driven. Taking it before the peer confirmed meant every refused or slow handoff
froze this desktop for the whole pending window: clicks and keystrokes vanished,
and an application started meanwhile looked like it had failed to open. Capture
also had to be dropped by whichever path ended the handoff, so any path that
forgot to do it left a machine that could neither be clicked nor typed into.
"""

from mouseshare.config import Settings
from mouseshare.engine import Engine
from mouseshare.input.base import NullBackend


class FakeBackend(NullBackend):
    def __init__(self):
        super().__init__(lambda event: True)
        self.running = True
        self.moves = []

    def move(self, x, y):
        self.moves.append((x, y))


class FakeSession:
    peer_name = "Peer"

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


def build(tmp_path):
    settings = Settings(tmp_path)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0])
    engine.backend = FakeBackend()
    engine.session = FakeSession()
    engine.peer_available = True
    return engine, now


def cross(engine, now):
    engine.physical({"type": "move", "x": 1919, "y": 500, "dx": 10, "dy": 0})
    now[0] += 0.2
    engine.tick()


def test_an_unconfirmed_handoff_keeps_the_local_desktop_alive(tmp_path):
    engine, now = build(tmp_path)
    cross(engine, now)
    assert engine.state == "pending"
    assert not engine.backend.captured


def test_capture_starts_only_once_the_peer_confirms(tmp_path):
    engine, now = build(tmp_path)
    cross(engine, now)
    engine.receive({"type": "entered"})
    assert engine.state == "sending"
    assert engine.backend.captured


def test_a_refused_handoff_never_touches_the_local_pointer(tmp_path):
    engine, now = build(tmp_path)
    cross(engine, now)
    engine.receive({"type": "release"})
    assert engine.state == "local"
    assert not engine.backend.captured


def test_a_capture_left_behind_a_local_state_is_dropped(tmp_path):
    engine, now = build(tmp_path)
    engine.backend.capture(True)
    engine.tick()
    assert not engine.backend.captured


def test_a_capture_is_dropped_when_sharing_becomes_unavailable(tmp_path):
    engine, now = build(tmp_path)
    cross(engine, now)
    engine.receive({"type": "entered"})
    assert engine.backend.captured
    engine.paused = True
    engine.tick()
    assert engine.state == "local"
    assert not engine.backend.captured


def test_a_new_handoff_clears_a_capture_left_by_the_previous_one(tmp_path):
    engine, now = build(tmp_path)
    engine.backend.capture(True)
    cross(engine, now)
    assert not engine.backend.captured
