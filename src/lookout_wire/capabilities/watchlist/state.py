"""Persisted level state and the per-ticker pre-send marker."""

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from pathlib import Path
from typing import Any

from lookout_wire.capabilities.watchlist.rules import (
    Alert,
    AlertKind,
    Kind,
    LevelState,
    TickerState,
    record_sent,
)
from lookout_wire.core.state import read_json, write_json


class Phase(StrEnum):
    IN_FLIGHT = "in_flight"
    NOT_SENT = "not_sent"


@dataclass(frozen=True)
class Marker:
    phase: Phase
    trading_day: date
    alerts: tuple[Alert, ...]
    explanation_text: str | None = None
    explanation_links: tuple[str, ...] = ()


@dataclass(frozen=True)
class WatchlistState:
    tickers: dict[str, TickerState]
    markers: dict[str, Marker]


def load_state(path: Path) -> WatchlistState:
    document = read_json(path)
    if document is None:
        return WatchlistState(tickers={}, markers={})
    return WatchlistState(
        tickers={s: _ticker_from(s, d) for s, d in document["tickers"].items()},
        markers={s: _marker_from(d) for s, d in document["markers"].items()},
    )


def save_state(path: Path, state: WatchlistState) -> None:
    write_json(
        path,
        {
            "tickers": {s: _ticker_to(t) for s, t in state.tickers.items()},
            "markers": {s: _marker_to(m) for s, m in state.markers.items()},
        },
    )


def resolve_markers(state: WatchlistState) -> WatchlistState:
    """A marker still in flight at startup may have been delivered, so it counts as sent."""
    tickers = dict(state.tickers)
    markers = {}
    for symbol, marker in state.markers.items():
        if symbol not in tickers:
            continue
        if marker.phase == Phase.NOT_SENT:
            markers[symbol] = marker
            continue
        tickers[symbol] = _apply_sent(tickers[symbol], marker)
    return WatchlistState(tickers=tickers, markers=markers)


def _apply_sent(ticker: TickerState, marker: Marker) -> TickerState:
    for alert in marker.alerts:
        if alert.kind == Kind.DIP:
            ticker = replace(ticker, dip=record_sent(ticker.dip, alert, marker.trading_day))
        elif ticker.buy is not None:
            ticker = replace(ticker, buy=record_sent(ticker.buy, alert, marker.trading_day))
    return ticker


def _ticker_to(ticker: TickerState) -> dict[str, Any]:
    return {
        "dip": _level_to(ticker.dip),
        "buy": _level_to(ticker.buy) if ticker.buy is not None else None,
    }


def _ticker_from(symbol: str, data: dict[str, Any]) -> TickerState:
    buy = data["buy"]
    return TickerState(
        symbol=symbol,
        dip=_level_from(data["dip"]),
        buy=_level_from(buy) if buy is not None else None,
    )


def _level_to(level: LevelState) -> dict[str, Any]:
    return {
        "kind": level.kind.value,
        "level": level.level,
        "armed": level.armed,
        "baseline": level.baseline,
        "last_message_day": _day_to(level.last_message_day),
    }


def _level_from(data: dict[str, Any]) -> LevelState:
    return LevelState(
        kind=Kind(data["kind"]),
        level=data["level"],
        armed=data["armed"],
        baseline=data["baseline"],
        last_message_day=_day_from(data["last_message_day"]),
    )


def _marker_to(marker: Marker) -> dict[str, Any]:
    return {
        "phase": marker.phase.value,
        "trading_day": marker.trading_day.isoformat(),
        "alerts": [_alert_to(alert) for alert in marker.alerts],
        "explanation_text": marker.explanation_text,
        "explanation_links": list(marker.explanation_links),
    }


def _marker_from(data: dict[str, Any]) -> Marker:
    return Marker(
        phase=Phase(data["phase"]),
        trading_day=date.fromisoformat(data["trading_day"]),
        alerts=tuple(_alert_from(alert) for alert in data["alerts"]),
        explanation_text=data["explanation_text"],
        explanation_links=tuple(data["explanation_links"]),
    )


def _alert_to(alert: Alert) -> dict[str, Any]:
    return {
        "kind": alert.kind.value,
        "level": alert.level,
        "alert": alert.alert.value,
        "price": alert.price,
    }


def _alert_from(data: dict[str, Any]) -> Alert:
    return Alert(
        kind=Kind(data["kind"]),
        level=data["level"],
        alert=AlertKind(data["alert"]),
        price=data["price"],
    )


def _day_to(day: date | None) -> str | None:
    return day.isoformat() if day else None


def _day_from(text: str | None) -> date | None:
    return date.fromisoformat(text) if text else None
