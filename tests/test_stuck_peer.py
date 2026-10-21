"""A peer left behind in a handoff must be released, not waited on.

Every message that ends a handoff carries the flow token of the handoff that
created it. Once the two sides disagree about which handoff is current — one
side has moved on, or a handoff was refused — those messages are dropped, and
the stranded peer keeps its own cursor captured while refusing every later
attempt. Sharing then stays broken until something unscoped comes along, which
is why re-applying the layout (also unscoped) appeared to be the only cure.
"""
from mouseshare.config import Settings
from mouseshare.engine import Engine
from mouseshare.input.base import NullBackend

WIDTH, HEIGHT = 1920, 1080


class FakeBackend(NullBackend):
    def __init__(self):
        super().__init__(lambda event: True)
        self.running = True

    def move(self, x, y):
        pass


class FakeSession:
    peer_name = "Peer"

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


def setup(tmp_path, side='right'):
    settings = Settings(tmp_path)
    settings.set(side=side)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0])
    engine.backend = FakeBackend()
    engine.session = FakeSession()
    engine.peer_available = True
    return engine, now


def push(engine, now):
    for offset, dx in ((14, 4), (10, 4), (6, 4), (3, 3), (1, 2)):
        now[0] += 0.008
        engine.physical({"type": "move", "x": WIDTH-1-offset, "y": 500,
                         "dx": dx, "dy": 0, "_last_dx": dx, "_last_dy": 0})


def sent(engine):
    return [message['type'] for message in engine.session.messages]


def test_refused_handoff_nudges_the_peer(tmp_path):
    """The peer declined, so it will not act on our scoped release."""
    engine, now = setup(tmp_path)
    push(engine, now)
    assert engine.state == 'pending'
    engine.receive({'type': 'release', 'flow': engine.flow})
    assert engine.state == 'local'
    assert sent(engine)[-2:] == ['blocked', 'available']


def test_acknowledgement_that_does_not_match_the_wait_is_answered(tmp_path):
    """An 'entered' for a handoff we are no longer waiting for still strands it."""
    engine, now = setup(tmp_path)
    engine.receive({'type': 'entered'})
    assert sent(engine)[-2:] == ['blocked', 'available']


def test_abandoned_handoff_nudges_the_peer(tmp_path):
    engine, now = setup(tmp_path)
    push(engine, now)
    assert engine.state == 'pending'
    now[0] += 2.0
    engine.tick()
    assert engine.state == 'local'
    assert sent(engine)[-2:] == ['blocked', 'available']


def test_the_nudge_is_not_repeated_within_a_second(tmp_path):
    """A stuck peer must not turn into a stream of resets on every push."""
    engine, now = setup(tmp_path)
    for _ in range(3):
        push(engine, now)
        engine.receive({'type': 'release', 'flow': engine.flow})
        now[0] += 0.01
    assert sent(engine).count('blocked') == 1


def test_a_peer_that_gives_control_back_is_left_alone(tmp_path):
    """Win stopping its own control of us is a normal handover, not a failure."""
    engine, now = setup(tmp_path)
    engine.state = 'receiving'
    engine.controller = False
    engine.receive({'type': 'release'})
    assert engine.state == 'local'
    assert 'blocked' not in sent(engine)
