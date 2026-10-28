import os
import subprocess
import sys

import mouseshare.instance as instance
from mouseshare.instance import alive, claim, discard, lock_file, owner, release


def test_the_lock_lives_in_the_app_data_directory(tmp_path, monkeypatch):
    monkeypatch.setattr(instance, "data_dir", lambda: tmp_path)
    assert lock_file("MouseShare-kiwi") == tmp_path / "instance-MouseShare-kiwi.lock"


def test_only_one_copy_claims_the_lock(tmp_path):
    path = tmp_path / "instance.lock"
    assert claim(path)
    assert not claim(path)


def test_absent_and_stale_locks_report_no_owner(tmp_path):
    path = tmp_path / "instance.lock"
    assert owner(path) is None
    path.write_text("not-a-pid")
    assert owner(path) is None
    path.write_text("999999")
    assert owner(path) is None


def test_a_live_copy_is_reported_as_the_owner(tmp_path):
    path = tmp_path / "instance.lock"
    path.write_text(str(os.getppid()))
    assert owner(path) == os.getppid()


def test_release_drops_only_its_own_lock(tmp_path):
    path = tmp_path / "instance.lock"
    path.write_text(str(os.getpid()))
    release(path)
    assert not path.exists()
    path.write_text(str(os.getppid()))
    release(path)
    assert path.exists()
    discard(path)
    assert not path.exists()


def test_a_second_copy_recovers_a_lock_left_by_a_crash(tmp_path):
    path = tmp_path / "instance.lock"
    path.write_text("999999")
    assert not claim(path)
    discard(path)
    assert claim(path)


def test_a_finished_process_is_not_alive():
    finished = subprocess.Popen([sys.executable, "-c", "pass"])
    finished.wait()
    assert not alive(finished.pid)


def test_the_probe_never_signals_the_console(monkeypatch):
    """os.kill(pid, 0) means CTRL_C_EVENT on Windows: it would interrupt every
    process sharing the console instead of testing the pid."""
    used = []
    monkeypatch.setattr(os, "kill", lambda pid, sig: used.append((pid, sig)))
    assert alive(os.getpid())
    if sys.platform == "win32":
        assert not used
