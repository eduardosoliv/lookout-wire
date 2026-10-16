import os
import signal
import threading
from datetime import UTC, datetime

import pytest

from lookout_wire.core.capability import FatalError
from lookout_wire.core.loop import Loop, install_signal_handlers

NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


class Recorder:
    def __init__(self, name, calls, error=None):
        self.name = name
        self.calls = calls
        self.error = error

    def run(self, now):
        self.calls.append((self.name, now))
        if self.error:
            raise self.error


def make_loop(capabilities, interval=0.01, stop=None):
    return Loop(capabilities, interval, stop or threading.Event(), clock=lambda: NOW)


def test_each_capability_runs_once_per_cycle_in_order():
    calls = []
    loop = make_loop([Recorder("a", calls), Recorder("b", calls)])

    loop.run_once()

    assert calls == [("a", NOW), ("b", NOW)]


def test_every_capability_receives_the_same_now():
    calls = []
    ticks = iter([NOW, datetime(2030, 1, 1, tzinfo=UTC)])
    loop = Loop(
        [Recorder("a", calls), Recorder("b", calls)],
        0.01,
        threading.Event(),
        clock=lambda: next(ticks),
    )

    loop.run_once()

    assert {now for _, now in calls} == {NOW}


def test_ordinary_exception_is_logged_and_next_capability_still_runs(caplog):
    calls = []
    loop = make_loop([Recorder("a", calls, ValueError("boom")), Recorder("b", calls)])

    loop.run_once()

    assert [name for name, _ in calls] == ["a", "b"]
    assert "boom" in caplog.text


def test_fatal_error_stops_the_loop_and_propagates():
    stop = threading.Event()
    loop = make_loop([Recorder("a", [], FatalError("token revoked"))], stop=stop)

    with pytest.raises(FatalError):
        loop.run()

    assert stop.is_set()


def test_setting_stop_during_the_wait_ends_the_loop_promptly():
    stop = threading.Event()
    loop = make_loop([Recorder("a", [])], interval=30, stop=stop)
    thread = threading.Thread(target=loop.run)

    thread.start()
    stop.set()
    thread.join(timeout=1)

    assert not thread.is_alive()


def test_interval_controls_the_wait():
    calls = []
    stop = threading.Event()
    loop = make_loop([Recorder("a", calls)], interval=0.05, stop=stop)
    thread = threading.Thread(target=loop.run)

    thread.start()
    threading.Timer(0.12, stop.set).start()
    thread.join(timeout=1)

    assert 2 <= len(calls) <= 4


@pytest.mark.parametrize("signum", [signal.SIGTERM, signal.SIGINT])
def test_stop_signals_set_the_stop_event(signum):
    stop = threading.Event()
    originals = {s: signal.getsignal(s) for s in (signal.SIGINT, signal.SIGTERM)}
    try:
        install_signal_handlers(stop)
        os.kill(os.getpid(), signum)
    finally:
        for s, handler in originals.items():
            signal.signal(s, handler)

    assert stop.is_set()
