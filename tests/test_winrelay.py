"""Wire-format and client tests for the lock-screen input relay (no Windows needed)."""
import json
from types import SimpleNamespace

from mouseshare.winrelay import LockedRelay, serialize_input


def mouse(dx=0, dy=0, mouse_data=0, flags=0):
    return SimpleNamespace(type=0, mi=SimpleNamespace(dx=dx, dy=dy, mouseData=mouse_data, dwFlags=flags))


def key(vk=0, scan=0x2A, flags=0x0008):
    return SimpleNamespace(type=1, ki=SimpleNamespace(wVk=vk, wScan=scan, dwFlags=flags))


def records_of(fake_open):
    """Connect a relay through a captured fake pipe and return the records sent."""
    sent = []

    def open_file(_path, _mode, buffering=0):
        return SimpleNamespace(write=lambda data: sent.append(json.loads(data.decode())),
                               close=lambda: None)

    relay = LockedRelay(open_file=open_file, clock=lambda: 0.0)
    return relay, sent


def test_serialize_mouse_round_trip():
    value = mouse(dx=-1234, dy=987, flags=0xC001)
    record = serialize_input(value)
    assert record == {'type': 0, 'dx': -1234, 'dy': 987, 'mouseData': 0, 'dwFlags': 0xC001}
    assert json.loads(json.dumps(record)) == record


def test_serialize_key_round_trip():
    value = key(vk=0x1B, scan=0x01, flags=0x0009)
    assert serialize_input(value) == {'type': 1, 'wVk': 0x1B, 'wScan': 0x01, 'dwFlags': 0x0009}


def test_serialize_rejects_unknown_type():
    assert serialize_input(SimpleNamespace(type=3, mi=None, ki=None)) is None


def test_relay_sends_json_line_per_event():
    relay, sent = records_of(None)
    relay.connect = lambda: True  # pretend the pipe accepted us
    relay._fh = SimpleNamespace(write=lambda data: sent.append(json.loads(data.decode())),
                                close=lambda: None)
    assert relay.send_input([serialize_input(key())]) is True
    assert sent == [{'t': 'input', 'r': [{'type': 1, 'wVk': 0, 'wScan': 0x2A, 'dwFlags': 0x0008}]}]


def test_relay_failure_throttles_reconnect():
    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    attempts = []
    clock = Clock()

    def open_file(*args, **kwargs):
        attempts.append(1)
        raise OSError('no pipe')

    relay = LockedRelay(open_file=open_file, clock=clock)
    record = {'type': 1, 'wVk': 0, 'wScan': 1, 'dwFlags': 0}
    assert relay.send_input([record]) is False
    assert relay.send_input([record]) is False  # 0.1 s later: still inside the 1 s throttle
    clock.t += 5.0
    assert relay.send_input([record]) is False  # throttle elapsed: retried, failed again
    assert attempts == [1, 1]  # exactly two connect attempts, throttled between
    assert relay.ready() is False


def test_relay_recovers_after_drop():
    class Clock:
        def __init__(self):
            self.t = 1000.0

        def __call__(self):
            return self.t

    state = {'broken': True}

    class Pipe:
        def write(self, data):
            if state['broken']:
                raise OSError('pipe gone')

        def flush(self):
            pass

        def close(self):
            pass

    opened = []

    def open_file(*args, **kwargs):
        opened.append(1)
        return Pipe()

    clock = Clock()
    relay = LockedRelay(open_file=open_file, clock=clock)
    record = {'type': 1, 'wVk': 0, 'wScan': 1, 'dwFlags': 0}
    assert relay.send_input([record]) is False
    state['broken'] = False
    clock.t += 5.0
    assert relay.send_input([record]) is True  # reconnected on the next event
    assert relay.sent == 1


def test_relay_skips_empty_records():
    relay, sent = records_of(None)
    assert relay.send_input([]) is True
    assert sent == []
