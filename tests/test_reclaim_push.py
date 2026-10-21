"""A driven machine must be able to take its own pointer back.

With reclaim-on-input switched off, local input used to be dropped outright
while a peer was driving: the machine stayed a passenger until the peer gave
control back, so the only remaining escape was a manual reset. The switch is
meant to stop a desk nudge from stealing control, not to remove the way back.
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

    def foreground(self):
        return "", False


class FakeSession:
    peer_name = "Peer"
    features = {'multi-v1', 'layout-v1', 'monitors-v1'}

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


def setup(tmp_path, reclaim=False):
    settings = Settings(tmp_path)
    settings.set(reclaim_on_input=reclaim)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0])
    engine.backend = FakeBackend()
    engine.session = FakeSession()
    engine.peer_available = True
    # A peer is driving this machine.
    engine.state, engine.controller, engine.flow = 'receiving', False, 'a' * 32
    return engine, now


def push(engine, now, steps, dx):
    for _ in range(steps):
        engine.physical({"type": "move", "x": 900, "y": 500, "dx": dx, "dy": 0})
        now[0] += 0.012


def test_driven_machine_reclaims_after_a_deliberate_move(tmp_path):
    engine, now = setup(tmp_path)
    assert engine.state == 'receiving'
    push(engine, now, 2, 4)     # 8 px total: a desk nudge, below the 10 px threshold
    assert engine.state == 'receiving'
    push(engine, now, 2, 4)     # 16 px within the window: someone picked the mouse up
    assert engine.state == 'local'


def test_a_single_fast_flick_reclaims_immediately(tmp_path):
    engine, now = setup(tmp_path)
    engine.physical({"type": "move", "x": 900, "y": 500, "dx": 24, "dy": -9})
    assert engine.state == 'local'


def test_jitter_that_never_adds_up_keeps_the_peer_in_control(tmp_path):
    engine, now = setup(tmp_path)
    for _ in range(6):
        push(engine, now, 1, 1)
        now[0] += 0.5           # long gaps: the window expires, travel never accumulates
    assert engine.state == 'receiving'


def test_reclaim_stays_immediate_when_the_switch_is_on(tmp_path):
    engine, now = setup(tmp_path, reclaim=True)
    engine.physical({"type": "move", "x": 900, "y": 500, "dx": 1, "dy": 0})
    assert engine.state == 'local'


def test_a_keypress_reclaims_even_when_the_switch_is_off(tmp_path):
    engine, now = setup(tmp_path)
    engine.physical({"type": "key", "key": 8, "down": True})
    assert engine.state == 'local'


def test_reclaiming_does_not_mint_a_second_handoff(tmp_path):
    """Local input takes the pointer back; it must not open a crossing of its own."""
    engine, now = setup(tmp_path)
    push(engine, now, 4, 6)
    assert engine.state == 'local'
    assert not [m for m in engine.session.messages if m.get('type') == 'enter']
    assert any(m.get('type') == 'release' for m in engine.session.messages)
