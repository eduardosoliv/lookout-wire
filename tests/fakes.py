"""In-memory stand-ins for every port, shared by capability tests."""

from collections import deque
from datetime import datetime, timedelta

from lookout_wire.core.ports import Explanation, SendOutcome, SendStatus, Session, Trade


class FakeClock:
    def __init__(self, now: datetime):
        self.now = now

    def __call__(self) -> datetime:
        return self.now

    def advance(self, **kwargs) -> None:
        self.now += timedelta(**kwargs)


class FakePriceSource:
    def __init__(self, trades: dict[str, Trade] | None = None, error: Exception | None = None):
        self.trades = trades or {}
        self.error = error
        self.calls: list[list[str]] = []

    def latest_trades(self, symbols):
        self.calls.append(list(symbols))
        if self.error:
            raise self.error
        return {s: t for s, t in self.trades.items() if s in symbols}


class FakeCalendar:
    def __init__(self, session: Session | None = None, error: Exception | None = None):
        self.current = session
        self.error = error

    def session(self, now):
        if self.error:
            raise self.error
        return self.current


class FakeMessenger:
    """Returns queued outcomes in order, then SENT. A queued exception is raised instead."""

    def __init__(self, *outcomes):
        self.outcomes = deque(outcomes)
        self.sent: list[str] = []
        self.is_ready = True

    def ready(self, now):
        return self.is_ready

    def send(self, text):
        self.sent.append(text)
        outcome = self.outcomes.popleft() if self.outcomes else SendOutcome(SendStatus.SENT)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


class FakeExplainer:
    def __init__(self, result: Explanation | None = None, error: Exception | None = None):
        self.result = result
        self.error = error
        self.questions: list[str] = []

    def explain(self, question):
        self.questions.append(question)
        if self.error:
            raise self.error
        return self.result
