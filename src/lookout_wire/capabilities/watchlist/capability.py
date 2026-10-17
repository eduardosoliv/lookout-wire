"""The watchlist capability: one tick checks every ticker and sends alerts for crossed levels."""

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from pathlib import Path

from lookout_wire.capabilities.watchlist.config import WatchlistFile
from lookout_wire.capabilities.watchlist.message import build_question, heading, render, title
from lookout_wire.capabilities.watchlist.rules import (
    Alert,
    Decision,
    LevelState,
    TickerState,
    evaluate,
    reconcile,
)
from lookout_wire.capabilities.watchlist.state import (
    Marker,
    Phase,
    WatchlistState,
    load_state,
    resolve_markers,
    save_state,
)
from lookout_wire.core.capability import FatalError
from lookout_wire.core.ports import (
    CalendarError,
    Explanation,
    Ports,
    PriceError,
    SendStatus,
    Session,
    Trade,
    in_session,
)
from lookout_wire.core.settings import Settings

log = logging.getLogger(__name__)

HEALTH_THRESHOLD = 6
FAILURE_NOTICE = "lookout-wire cannot read market data. Alerts are paused until it recovers."
RECOVERY_NOTICE = "lookout-wire is back: market data is readable again."
RETRY_LATER = (SendStatus.RATE_LIMITED, SendStatus.NOT_ACCEPTED)


class WatchlistCapability:
    name = "watchlist"

    def __init__(self, watchlist: WatchlistFile, state_path: Path, ports: Ports):
        self._watchlist = watchlist
        self._state_path = state_path
        self._ports = ports
        self._saved = load_state(state_path)
        self._state = resolve_markers(self._saved)
        self._failures = 0
        self._skipped: set[str] = set()
        self._names: dict[str, str | None] = {}
        self._notice: str | None = None
        self._failure_notice_delivered = False
        self._save()

    def run(self, now: datetime) -> None:
        self._reconcile()
        self._check_all(now)
        self._send_pending_notice(now)
        self._save()

    def _check_all(self, now: datetime) -> None:
        session = self._session(now)
        if session is None:
            return
        if not session.is_open:
            self._record_success()
            return
        trades = self._trades()
        if trades is None:
            return
        for ticker in self._active_tickers():
            self._check(ticker, trades.get(ticker.symbol), session, now)

    def _active_tickers(self) -> list[TickerState]:
        return [t for t in self._state.tickers.values() if t.symbol not in self._skipped]

    def _reconcile(self) -> None:
        parsed = self._watchlist.read()
        for error in parsed.errors:
            log.warning("Watchlist: %s", error)
        if not parsed.parsed:
            return
        self._skipped = parsed.invalid_symbols
        self._names = {entry.symbol: entry.name for entry in parsed.entries}
        tickers = reconcile(self._state.tickers, parsed.entries, parsed.invalid_symbols)
        markers = {s: m for s, m in self._state.markers.items() if s in tickers}
        self._state = WatchlistState(tickers, markers)

    def _session(self, now: datetime) -> Session | None:
        try:
            return self._ports.calendar.session(now)
        except CalendarError as error:
            log.warning("Market clock unavailable: %s", error)
            self._record_failure()
            return None

    def _trades(self) -> dict[str, Trade] | None:
        symbols = [ticker.symbol for ticker in self._active_tickers()]
        if not symbols:
            return None
        try:
            trades = self._ports.prices.latest_trades(symbols)
        except PriceError as error:
            log.warning("Prices unavailable: %s", error)
            trades = {}
        if not trades:
            self._record_failure()
            return None
        self._record_success()
        return trades

    def _check(self, ticker: TickerState, trade: Trade | None, session: Session, now: datetime):
        if trade is None or not in_session(trade, session):
            log.info("Skipping %s: no trade in today's session yet", ticker.symbol)
            return
        day = session.trading_day
        decisions = Decisions(
            dip=evaluate(ticker.dip, trade.price, day),
            buy=evaluate(ticker.buy, trade.price, day) if ticker.buy is not None else None,
        )
        alerts = decisions.alerts()
        if not alerts:
            self._set(_ticker_sent(ticker, decisions), marker=None)
            return
        if not self._ports.messenger.ready(now):
            self._set(_ticker_unsent(ticker, decisions), self._marker(ticker.symbol))
            return
        self._alert(ticker, decisions, trade.price, day)

    def _alert(self, ticker: TickerState, decisions: Decisions, price: float, day: date) -> None:
        alerts = decisions.alerts()
        explanation = self._explanation(ticker.symbol, price, alerts, day)
        marker = Marker(Phase.IN_FLIGHT, day, tuple(alerts), *_explanation_fields(explanation))
        unsent = _ticker_unsent(ticker, decisions)
        if not self._write_marker(unsent, marker):
            return
        try:
            outcome = self._ports.messenger.send(
                render(self._heading(ticker.symbol), price, alerts, explanation)
            )
        except FatalError:
            self._set(unsent, replace(marker, phase=Phase.NOT_SENT))
            self._save()
            raise
        if outcome.status in RETRY_LATER:
            log.warning("Alert for %s not sent (%s); retrying", ticker.symbol, outcome.detail)
            self._set(unsent, replace(marker, phase=Phase.NOT_SENT))
            self._save()
            return
        _log_outcome(ticker.symbol, outcome.status, outcome.detail)
        self._set(_ticker_sent(ticker, decisions), marker=None)

    def _explanation(
        self, symbol: str, price: float, alerts: list[Alert], day: date
    ) -> Explanation | None:
        kept = self._marker(symbol)
        if kept is not None and kept.trading_day == day and kept.explanation_text:
            return Explanation(kept.explanation_text, kept.explanation_sources)
        return self._ports.explainer.explain(
            build_question(self._title(symbol), price, alerts, day)
        )

    def _write_marker(self, ticker: TickerState, marker: Marker) -> bool:
        previous = self._state
        self._set(ticker, marker)
        try:
            self._save()
        except OSError as error:
            log.error(
                "Could not record pending alert for %s, skipping send: %s", ticker.symbol, error
            )
            self._state = previous
            return False
        return True

    def _title(self, symbol: str) -> str:
        return title(symbol, self._names.get(symbol))

    def _heading(self, symbol: str) -> str:
        return heading(symbol, self._names.get(symbol))

    def _marker(self, symbol: str) -> Marker | None:
        return self._state.markers.get(symbol)

    def _set(self, ticker: TickerState, marker: Marker | None) -> None:
        tickers = {**self._state.tickers, ticker.symbol: ticker}
        markers = {s: m for s, m in self._state.markers.items() if s != ticker.symbol}
        if marker is not None:
            markers[ticker.symbol] = marker
        self._state = WatchlistState(tickers, markers)

    def _save(self) -> None:
        if self._state == self._saved:
            return
        save_state(self._state_path, self._state)
        self._saved = self._state

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures == HEALTH_THRESHOLD:
            self._notice = FAILURE_NOTICE

    def _record_success(self) -> None:
        if self._failures >= HEALTH_THRESHOLD:
            self._notice = RECOVERY_NOTICE if self._failure_notice_delivered else None
        self._failures = 0

    def _send_pending_notice(self, now: datetime) -> None:
        if self._notice is None or not self._ports.messenger.ready(now):
            return
        outcome = self._ports.messenger.send(self._notice)
        if outcome.status in RETRY_LATER:
            return
        self._failure_notice_delivered = self._notice == FAILURE_NOTICE
        self._notice = None


def build_watchlist(settings: Settings, ports: Ports) -> WatchlistCapability:
    return WatchlistCapability(
        WatchlistFile(settings.watchlist_path), ports.state_dir / "watchlist.json", ports
    )


@dataclass(frozen=True)
class Decisions:
    dip: Decision
    buy: Decision | None

    def alerts(self) -> list[Alert]:
        found = [self.dip.alert, self.buy.alert if self.buy else None]
        return [alert for alert in found if alert is not None]


type LevelUpdate = Callable[[LevelState, Decision | None], LevelState]


def _ticker_sent(ticker: TickerState, decisions: Decisions) -> TickerState:
    return _ticker_applying(ticker, decisions, _state_after_send)


def _ticker_unsent(ticker: TickerState, decisions: Decisions) -> TickerState:
    """A level whose decision carries an alert changes only once that alert was sent."""
    return _ticker_applying(ticker, decisions, _state_before_send)


def _ticker_applying(ticker: TickerState, decisions: Decisions, update: LevelUpdate) -> TickerState:
    buy = update(ticker.buy, decisions.buy) if ticker.buy is not None else None
    return replace(ticker, dip=update(ticker.dip, decisions.dip), buy=buy)


def _state_after_send(current: LevelState, decision: Decision | None) -> LevelState:
    return current if decision is None else decision.state


def _state_before_send(current: LevelState, decision: Decision | None) -> LevelState:
    if decision is None or decision.alert is not None:
        return current
    return decision.state


def _explanation_fields(explanation: Explanation | None) -> tuple[str | None, tuple[str, ...]]:
    if explanation is None:
        return None, ()
    return explanation.text, explanation.sources


def _log_outcome(symbol: str, status: SendStatus, detail: str) -> None:
    if status == SendStatus.SENT:
        log.info("Alert sent for %s", symbol)
        return
    if status == SendStatus.UNKNOWN:
        log.warning("Alert for %s may not have arrived (%s); not retrying", symbol, detail)
        return
    log.error("Alert for %s could not be delivered (%s)", symbol, detail)
