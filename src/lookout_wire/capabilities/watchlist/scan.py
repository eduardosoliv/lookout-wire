"""One-shot scan: message every ticker trading below a level now, ignoring market hours and
earlier alerts. It never reads or writes alert state, so the agent's own alerts are unaffected."""

import time
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum, auto

from lookout_wire.capabilities.watchlist.config import Entry, WatchlistFile
from lookout_wire.capabilities.watchlist.message import build_question, heading, render, title
from lookout_wire.capabilities.watchlist.rules import Alert, AlertKind, Kind
from lookout_wire.core.ports import Ports, SendOutcome, SendStatus, Trade

SEND_ATTEMPTS = 3
RETRY_LATER = (SendStatus.RATE_LIMITED, SendStatus.NOT_ACCEPTED)
DELIVERED = (SendStatus.SENT, SendStatus.UNKNOWN)


class Result(Enum):
    SENT = auto()
    ABOVE = auto()
    NO_PRICE = auto()
    FAILED = auto()


@dataclass
class ScanReport:
    sent: list[str] = field(default_factory=list)
    above: list[str] = field(default_factory=list)
    no_price: list[str] = field(default_factory=list)
    failed: list[str] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)

    def add(self, symbol: str, result: Result) -> None:
        lists = {
            Result.SENT: self.sent,
            Result.ABOVE: self.above,
            Result.NO_PRICE: self.no_price,
            Result.FAILED: self.failed,
        }
        lists[result].append(symbol)


def scan(
    watchlist: WatchlistFile, ports: Ports, sleep: Callable[[float], None] = time.sleep
) -> ScanReport:
    parsed = watchlist.read()
    report = ScanReport(problems=list(parsed.errors))
    trades = ports.prices.latest_trades([entry.symbol for entry in parsed.entries])
    for entry in parsed.entries:
        report.add(entry.symbol, _scan_entry(entry, trades.get(entry.symbol), ports, sleep))
    return report


def _scan_entry(
    entry: Entry, trade: Trade | None, ports: Ports, sleep: Callable[[float], None]
) -> Result:
    if trade is None:
        return Result.NO_PRICE
    alerts = _alerts(entry, trade.price)
    if not alerts:
        return Result.ABOVE
    day = trade.timestamp.date()
    question = build_question(title(entry.symbol, entry.name), trade.price, alerts, day)
    explanation = ports.explainer.explain(question)
    text = render(heading(entry.symbol, entry.name), trade.price, alerts, explanation)
    outcome = _send(ports, text, sleep)
    return Result.SENT if outcome.status in DELIVERED else Result.FAILED


def _alerts(entry: Entry, price: float) -> list[Alert]:
    levels = [(Kind.DIP, entry.dip), (Kind.BUY, entry.buy)]
    return [
        Alert(kind, level, AlertKind.CROSSED, price)
        for kind, level in levels
        if level is not None and price < level
    ]


def _send(ports: Ports, text: str, sleep: Callable[[float], None]) -> SendOutcome:
    outcome = SendOutcome(SendStatus.NOT_ACCEPTED)
    for _ in range(SEND_ATTEMPTS):
        _wait_until_ready(ports, sleep)
        outcome = ports.messenger.send(text)
        if outcome.status not in RETRY_LATER:
            return outcome
    return outcome


def _wait_until_ready(ports: Ports, sleep: Callable[[float], None]) -> None:
    while not ports.messenger.ready(ports.clock()):
        sleep(1.0)


def describe(report: ScanReport) -> str:
    lines = [f"Watchlist: {problem}" for problem in report.problems]
    lines.append(_line("Sent", report.sent))
    lines.append(_line("Above their levels, nothing sent", report.above))
    if report.no_price:
        lines.append(_line("No price from Alpaca", report.no_price))
    if report.failed:
        lines.append(_line("Could not send", report.failed))
    return "\n".join(lines)


def _line(label: str, symbols: list[str]) -> str:
    listed = f": {', '.join(symbols)}" if symbols else ""
    return f"{label} ({len(symbols)}){listed}"
