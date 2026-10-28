"""A handoff started from the wrong side used to strand the machine driving it.

The peer being controlled keeps receiving its own physical input. If that input
reaches an edge it can start a handoff of its own, which mints a second flow
token; the driving machine is still holding the first one and every release it
sends afterwards is scoped to a token the peer no longer recognises, so it stays
captured with a dead local pointer while refusing every later handoff.
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
    features = {'multi-v1', 'layout-v1', 'monitors-v1'}

    def __init__(self):
        self.messages = []

    def send(self, message):
        self.messages.append(message)
        return True


def setup(tmp_path):
    settings = Settings(tmp_path)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0])
    engine.backend = FakeBackend()
    engine.session = FakeSession()
    engine.peer_available = True
    return engine, now


def hit_edge(engine, now):
    engine.physical({"type": "move", "x": 1919, "y": 500, "dx": 10, "dy": 0})
    now[0] += 0.2
    engine.tick()


def test_controlled_machine_takes_its_pointer_back_without_a_second_handoff(tmp_path):
    """A deliberate local move reclaims control, but must not open a crossing.

    While a peer drives us, our own edge belongs to the remote cursor. Starting
    a handoff there mints a second flow token the peer does not hold, which is
    what stranded the driving machine in 'sending'. Reclaiming first — and
    letting the next sample route normally — keeps one token in play.
    """
    engine, now = setup(tmp_path)
    engine.settings.set(reclaim_on_input=False)
    engine.settings.set(side='left')
    engine.state, engine.controller, engine.flow = 'receiving', False, 'a' * 32
    engine.physical({"type": "move", "x": 0, "y": 500, "dx": -14, "dy": 0})
    assert engine.state == 'local'
    assert not any(m['type'] == 'enter' for m in engine.session.messages)


def test_a_nudge_at_our_own_edge_leaves_the_peer_in_control(tmp_path):
    """Two pixels of desk jitter must not steal control, even at the edge."""
    engine, now = setup(tmp_path)
    engine.settings.set(reclaim_on_input=False)
    engine.settings.set(side='left')
    engine.state, engine.controller, engine.flow = 'receiving', False, 'a' * 32
    engine.physical({"type": "move", "x": 0, "y": 500, "dx": -2, "dy": 0})
    assert engine.state == 'receiving'
    assert not any(m['type'] == 'enter' for m in engine.session.messages)


def test_controlled_machine_still_reclaims_when_that_is_asked_for(tmp_path):
    engine, now = setup(tmp_path)
    engine.settings.set(reclaim_on_input=True)
    engine.state, engine.controller, engine.flow = 'receiving', False, 'a' * 32
    engine.physical({"type": "move", "x": 700, "y": 500, "dx": 4, "dy": 0})
    assert engine.state == 'local'


def test_driving_machine_drops_its_handoff_when_the_peer_handoff_conflicts(tmp_path):
    engine, now = setup(tmp_path)
    hit_edge(engine, now)
    engine.receive({'type': 'entered'})
    assert engine.state == 'sending' and engine.backend.captured
    engine.receive({'type': 'enter', 'edge': 'left', 'position': 0.5, 'flow': 'b' * 32})
    assert engine.state == 'local' and not engine.backend.captured
    assert engine.session.messages[-1] == {'type': 'release', 'flow': 'b' * 32}


def test_conflicting_enter_from_another_peer_keeps_the_current_handoff(tmp_path):
    """A third machine asking for control must not cancel the one in flight."""
    engine, now = setup(tmp_path)
    hit_edge(engine, now)
    engine.receive({'type': 'entered'})
    engine.receive({'type': 'enter', 'edge': 'left', 'position': 0.5, 'flow': 'c' * 32},
                   FakeSession())
    assert engine.state == 'sending'
