"""Edge crossing must depend on intent (outward travel), not on event sample rate.

macOS reports screen displacement as the mouse delta, so the sample that lands on
the border carries a zero delta. The old rule required one single event to be both
"on the edge" and "pointing outward", which dropped fast pushes almost always and
slow pushes only occasionally. These cases pin both behaviours down.
"""
from mouseshare.config import Settings
from mouseshare.engine import Engine
from mouseshare.input.base import NullBackend

WIDTH, HEIGHT = 1920, 1080


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


def setup(tmp_path, side='right'):
    settings = Settings(tmp_path)
    settings.set(side=side)
    now = [100.0]
    engine = Engine(settings, clock=lambda: now[0])
    engine.backend = FakeBackend()
    engine.session = FakeSession()
    engine.peer_available = True
    return engine, now


def move(engine, now, x, y, dx, dy, step=0.008):
    now[0] += step
    engine.physical({"type": "move", "x": x, "y": y, "dx": dx, "dy": dy,
                     "_last_dx": dx, "_last_dy": dy})


def test_slow_push_into_right_edge_crosses(tmp_path):
    engine, now = setup(tmp_path)
    for offset in (14, 10, 6, 3, 1):
        move(engine, now, WIDTH-1-offset, 500, 4, 0)
    assert engine.state == 'pending'
    assert engine.session.messages[-1]['type'] == 'enter'


def test_clamped_border_sample_with_zero_delta_still_crosses(tmp_path):
    """The pointer is pinned on the last pixel; every further sample reports dx=0."""
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-8, 500, 12, 0)
    move(engine, now, WIDTH-1, 500, 0, 0)
    assert engine.state == 'pending'
    move(engine, now, WIDTH-1, 500, 0, 0)
    assert engine.state == 'pending'


def test_fast_flick_crosses_on_the_arrival_sample(tmp_path):
    """A fast flick arrives in a single sample that may already be clamped to dx=0."""
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-60, 500, 55, 0)
    move(engine, now, WIDTH-1, 500, 0, 0)
    assert engine.state == 'pending'


def test_single_fast_sample_past_the_edge_crosses(tmp_path):
    """No clamped sample at all: the sample itself would overshoot the border."""
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-8, 500, 140, 0)
    assert engine.state == 'pending'


def test_moving_inward_at_the_edge_does_not_cross(tmp_path):
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-30, 500, 25, 0)
    move(engine, now, WIDTH-1, 500, -6, 0)
    assert engine.state == 'local'


def test_push_expires_after_the_window(tmp_path):
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-6, 500, 5, 0)
    now[0] += 2.0
    move(engine, now, WIDTH-1, 500, 0, 0)
    assert engine.state == 'local'


def test_no_crossing_when_the_pointer_stops_short_of_the_edge(tmp_path):
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-40, 500, 30, 0)
    move(engine, now, WIDTH-20, 500, 0, 0)
    assert engine.state == 'local'


def test_cursor_clamped_at_exclusive_bound_still_crosses(tmp_path):
    """WindowServer pins a pushed cursor at x == width, not width-1.

    The production log showed 736 failed pushes, every one with the pointer
    reported at (1920, y): normalized() then produced 1.0005, the protocol's
    valid_point rejected it, and the crossing found no target.
    """
    engine, now = setup(tmp_path)
    move(engine, now, WIDTH-30, 500, 29, 0)
    move(engine, now, WIDTH, 500, 20, 0)   # clamped sample, delta still outward
    assert engine.state == 'pending'


def test_normalized_clamps_to_the_protocol_range():
    from mouseshare.displays import normalized
    assert normalized((WIDTH, 540), dict(w=WIDTH, h=HEIGHT))[0] == 1.0
    assert normalized((0, 0), dict(w=WIDTH, h=HEIGHT)) == [0.0, 0.0]
    assert normalized((-5, -5), dict(w=WIDTH, h=HEIGHT)) == [0.0, 0.0]
