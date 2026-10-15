"""Pure level rules: a price and a trading day in, new level state and at most one alert out."""

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lookout_wire.capabilities.watchlist.config import Entry


class Kind(StrEnum):
    DIP = "dip"
    BUY = "buy"


class AlertKind(StrEnum):
    CROSSED = "crossed"
    NEW_LOW = "new_low"


@dataclass(frozen=True)
class LevelState:
    """baseline is the lowest price the owner was already notified about, not the lowest seen."""

    kind: Kind
    level: float
    armed: bool = True
    baseline: float | None = None
    last_message_day: date | None = None


@dataclass(frozen=True)
class Alert:
    kind: Kind
    level: float
    alert: AlertKind
    price: float


@dataclass(frozen=True)
class Decision:
    """When alert is set, state is the state as if the message was sent."""

    state: LevelState
    alert: Alert | None = None


@dataclass(frozen=True)
class TickerState:
    symbol: str
    dip: LevelState
    buy: LevelState | None = None

    def levels(self) -> list[LevelState]:
        return [level for level in (self.dip, self.buy) if level is not None]


def evaluate(state: LevelState, price: float, day: date) -> Decision:
    if state.armed:
        return _evaluate_armed(state, price, day)
    return _evaluate_disarmed(state, price, day)


def record_sent(state: LevelState, alert: Alert, day: date) -> LevelState:
    return replace(state, armed=False, baseline=alert.price, last_message_day=day)


def _evaluate_armed(state: LevelState, price: float, day: date) -> Decision:
    if price >= state.level:
        return Decision(state)
    if state.last_message_day == day:
        return Decision(replace(state, armed=False))
    return _send(state, AlertKind.CROSSED, price, day)


def _evaluate_disarmed(state: LevelState, price: float, day: date) -> Decision:
    if price >= state.level:
        return Decision(replace(state, armed=True))
    if not _is_new_low(state, price, day):
        return Decision(state)
    return _send(state, AlertKind.NEW_LOW, price, day)


def _is_new_low(state: LevelState, price: float, day: date) -> bool:
    if state.baseline is None:
        return False
    return price < state.baseline and state.last_message_day != day


def _send(state: LevelState, kind: AlertKind, price: float, day: date) -> Decision:
    alert = Alert(kind=state.kind, level=state.level, alert=kind, price=price)
    return Decision(record_sent(state, alert, day), alert)


def reconcile(
    states: dict[str, TickerState], entries: list[Entry], keep: set[str]
) -> dict[str, TickerState]:
    """Applies a parsed watchlist. Symbols in keep were skipped for a config error this cycle."""
    result = {symbol: states[symbol] for symbol in keep if symbol in states}
    for entry in entries:
        result[entry.symbol] = _reconcile_ticker(states.get(entry.symbol), entry)
    return result


def _reconcile_ticker(current: TickerState | None, entry: Entry) -> TickerState:
    return TickerState(
        symbol=entry.symbol,
        dip=_reconcile_level(current.dip if current else None, Kind.DIP, entry.dip),
        buy=_reconcile_level(current.buy if current else None, Kind.BUY, entry.buy),
    )


def _reconcile_level(
    current: LevelState | None, kind: Kind, level: float | None
) -> LevelState | None:
    if level is None:
        return None
    if current is not None and current.level == level:
        return current
    return LevelState(kind=kind, level=level)
