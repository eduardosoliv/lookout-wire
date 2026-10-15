"""The always-on loop: one tick per interval, every enabled capability per tick."""

import logging
import signal
import threading
from collections.abc import Callable, Sequence
from datetime import UTC, datetime

from lookout_wire.core.capability import Capability, FatalError

log = logging.getLogger(__name__)


def utc_now() -> datetime:
    return datetime.now(UTC)


class Loop:
    def __init__(
        self,
        capabilities: Sequence[Capability],
        interval: float,
        stop: threading.Event,
        clock: Callable[[], datetime] = utc_now,
    ):
        self._capabilities = list(capabilities)
        self._interval = interval
        self._stop = stop
        self._clock = clock

    def run(self) -> None:
        while not self._stop.is_set():
            self.run_once()
            self._stop.wait(self._interval)

    def run_once(self) -> None:
        now = self._clock()
        for capability in self._capabilities:
            self._run_capability(capability, now)

    def _run_capability(self, capability: Capability, now: datetime) -> None:
        try:
            capability.run(now)
        except FatalError:
            self._stop.set()
            raise
        except Exception:
            log.exception("Capability %s failed this cycle", capability.name)


def install_signal_handlers(stop: threading.Event) -> None:
    def request_stop(signum, frame):
        log.info("Stopping after the current step")
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    signal.signal(signal.SIGTERM, request_stop)
