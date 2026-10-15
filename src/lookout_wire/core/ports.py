"""Interfaces to external systems and the small value types that cross them."""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol


@dataclass(frozen=True)
class Trade:
    price: float
    timestamp: datetime


@dataclass(frozen=True)
class Session:
    """The market session for one trading day. Bounds are absent on a day with no session."""

    is_open: bool
    trading_day: date
    open: datetime | None = None
    close: datetime | None = None


class SendStatus(StrEnum):
    SENT = "sent"
    UNKNOWN = "unknown"
    RATE_LIMITED = "rate_limited"
    NOT_ACCEPTED = "not_accepted"
    REJECTED_RECIPIENT = "rejected_recipient"
    OTHER_CLIENT_ERROR = "other_client_error"


@dataclass(frozen=True)
class SendOutcome:
    status: SendStatus
    detail: str = ""


@dataclass(frozen=True)
class Explanation:
    text: str
    links: tuple[str, ...] = ()


class PriceError(Exception):
    """Prices could not be fetched this cycle."""


class CalendarError(Exception):
    """The market clock or calendar could not be read this cycle."""


class PriceSource(Protocol):
    def latest_trades(self, symbols: Sequence[str]) -> dict[str, Trade]: ...


class MarketCalendar(Protocol):
    def session(self, now: datetime) -> Session: ...


class Messenger(Protocol):
    def ready(self, now: datetime) -> bool:
        """False while spacing or backoff from earlier sends is still in effect."""
        ...

    def send(self, text: str) -> SendOutcome: ...


class Explainer(Protocol):
    def explain(self, question: str) -> Explanation | None: ...


@dataclass(frozen=True)
class Ports:
    prices: PriceSource
    calendar: MarketCalendar
    messenger: Messenger
    explainer: Explainer
    state_dir: Path
    clock: Callable[[], datetime]


def in_session(trade: Trade, session: Session) -> bool:
    if not session.is_open:
        return False
    if session.open is None or session.close is None:
        return False
    return session.open <= trade.timestamp <= session.close
