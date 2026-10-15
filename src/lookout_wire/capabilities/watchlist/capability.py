"""The watchlist capability: one tick checks every ticker and sends alerts for crossed levels."""

import logging
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path

from lookout_wire.capabilities.watchlist.config import WatchlistFile
from lookout_wire.capabilities.watchlist.message import build_question, render
from lookout_wire.capabilities.watchlist.rules import (
    Alert,
    Decision,
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
        self._state = resolve_markers(load_state(state_path))
        save_state(state_path, self._state)
        self._failures = 0

    def run(self, now: datetime) -> None:
        self._reconcile()
        session = self._session(now)
        if session is None or not session.is_open:
            self._save()
            return
        trades = self._trades()
        if trades is None:
            self._save()
            return
        for ticker in list(self._state.tickers.values()):
            self._check(ticker, trades.get(ticker.symbol), session, now)
        self._save()

    def _reconcile(self) -> None:
        parsed = self._watchlist.read()
        for error in parsed.errors:
            log.warning("Watchlist: %s", error)
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
        symbols = list(self._state.tickers)
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
        decisions = [evaluate(level, trade.price, session.trading_day) for level in ticker.levels()]
        alerts = [d.alert for d in decisions if d.alert is not None]
        if not alerts:
            self._set(_ticker_with(ticker, decisions, sent=True), marker=None)
            return
        if not self._ports.messenger.ready(now):
            self._set(_ticker_with(ticker, decisions, sent=False), self._marker(ticker.symbol))
            return
        self._alert(ticker, decisions, alerts, trade.price, session.trading_day)

    def _alert(
        self,
        ticker: TickerState,
        decisions: list[Decision],
        alerts: list[Alert],
        price: float,
        day: date,
    ) -> None:
        explanation = self._explanation(ticker.symbol, price, alerts, day)
        marker = Marker(Phase.IN_FLIGHT, day, tuple(alerts), *_explanation_fields(explanation))
        unsent = _ticker_with(ticker, decisions, sent=False)
        if not self._write_marker(unsent, marker):
            return
        try:
            outcome = self._ports.messenger.send(render(ticker.symbol, price, alerts, explanation))
        except FatalError:
            self._set(unsent, replace(marker, phase=Phase.NOT_SENT))
            self._save()
            raise
        if outcome.status in RETRY_LATER:
            log.warning("Alert for %s not sent (%s); retrying", ticker.symbol, outcome.detail)
            self._set(unsent, replace(marker, phase=Phase.NOT_SENT))
            return
        _log_outcome(ticker.symbol, outcome.status, outcome.detail)
        self._set(_ticker_with(ticker, decisions, sent=True), marker=None)

    def _explanation(
        self, symbol: str, price: float, alerts: list[Alert], day: date
    ) -> Explanation | None:
        kept = self._marker(symbol)
        if kept is not None and kept.trading_day == day and kept.explanation_text:
            return Explanation(kept.explanation_text, kept.explanation_links)
        return self._ports.explainer.explain(build_question(symbol, price, alerts, day))

    def _write_marker(self, ticker: TickerState, marker: Marker) -> bool:
        previous = self._state
        self._set(ticker, marker)
        try:
            save_state(self._state_path, self._state)
        except OSError as error:
            log.error(
                "Could not record pending alert for %s, skipping send: %s", ticker.symbol, error
            )
            self._state = previous
            return False
        return True

    def _marker(self, symbol: str) -> Marker | None:
        return self._state.markers.get(symbol)

    def _set(self, ticker: TickerState, marker: Marker | None) -> None:
        tickers = {**self._state.tickers, ticker.symbol: ticker}
        markers = {s: m for s, m in self._state.markers.items() if s != ticker.symbol}
        if marker is not None:
            markers[ticker.symbol] = marker
        self._state = WatchlistState(tickers, markers)

    def _save(self) -> None:
        save_state(self._state_path, self._state)

    def _record_failure(self) -> None:
        self._failures += 1
        if self._failures == HEALTH_THRESHOLD:
            self._ports.messenger.send(FAILURE_NOTICE)

    def _record_success(self) -> None:
        if self._failures >= HEALTH_THRESHOLD:
            self._ports.messenger.send(RECOVERY_NOTICE)
        self._failures = 0


def build_watchlist(settings, ports: Ports) -> WatchlistCapability:
    return WatchlistCapability(
        WatchlistFile(settings.watchlist_path), ports.state_dir / "watchlist.json", ports
    )


def _ticker_with(ticker: TickerState, decisions: list[Decision], sent: bool) -> TickerState:
    """Applies each level's decision. Levels that alerted only change when the alert was sent."""
    levels = [
        d.state if sent or d.alert is None else original
        for d, original in zip(decisions, ticker.levels(), strict=True)
    ]
    buy = levels[1] if len(levels) > 1 else None
    return replace(ticker, dip=levels[0], buy=buy)


def _explanation_fields(explanation: Explanation | None) -> tuple[str | None, tuple[str, ...]]:
    if explanation is None:
        return None, ()
    return explanation.text, explanation.links


def _log_outcome(symbol: str, status: SendStatus, detail: str) -> None:
    if status == SendStatus.SENT:
        log.info("Alert sent for %s", symbol)
        return
    if status == SendStatus.UNKNOWN:
        log.warning("Alert for %s may not have arrived (%s); not retrying", symbol, detail)
        return
    log.error("Alert for %s could not be delivered (%s)", symbol, detail)
