"""Live checks against real services, with a short OK / NOT OK report for each."""

import sys
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from lookout_wire.adapters.alpaca import AlpacaCalendar, AlpacaPriceSource
from lookout_wire.core.loop import utc_now
from lookout_wire.core.ports import (
    CalendarError,
    MarketCalendar,
    PriceError,
    PriceSource,
    Session,
)
from lookout_wire.core.settings import SettingsError, load_settings

NEW_YORK = ZoneInfo("America/New_York")
TEST_SYMBOL = "AAPL"
LIVE_KEYS_HINT = (
    "If these are live keys, set LOOKOUT_ALPACA_TRADING_URL=https://api.alpaca.markets in .env."
)


@dataclass(frozen=True)
class CheckResult:
    name: str
    ok: bool
    details: list[str] = field(default_factory=list)


def check_alpaca(prices: PriceSource, calendar: MarketCalendar, now: datetime) -> list[CheckResult]:
    return [_check_prices(prices), _check_clock(calendar, now)]


def render(results: Sequence[CheckResult]) -> str:
    lines = []
    for result in results:
        lines.append(f"{result.name}: {'OK' if result.ok else 'NOT OK'}")
        lines.extend(f"  {detail}" for detail in result.details)
    return "\n".join(lines)


def _check_prices(prices: PriceSource) -> CheckResult:
    name = "Alpaca prices"
    try:
        trades = prices.latest_trades([TEST_SYMBOL])
    except PriceError as error:
        return CheckResult(name, False, [str(error)])
    trade = trades.get(TEST_SYMBOL)
    if trade is None:
        return CheckResult(name, False, [f"no latest trade for {TEST_SYMBOL}"])
    when = trade.timestamp.astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M:%S")
    return CheckResult(
        name, True, [f"{TEST_SYMBOL} last trade {trade.price:.2f} at {when} New York time"]
    )


def _check_clock(calendar: MarketCalendar, now: datetime) -> CheckResult:
    name = "Alpaca market clock"
    try:
        session = calendar.session(now)
    except CalendarError as error:
        return CheckResult(name, False, [str(error), LIVE_KEYS_HINT])
    return CheckResult(name, True, [_describe_session(session)])


def _describe_session(session: Session) -> str:
    day = session.trading_day.isoformat()
    if session.open is None or session.close is None:
        return f"No session on {day} (weekend or holiday)."
    state = "open" if session.is_open else "closed"
    hours = f"{session.open:%H:%M} to {session.close:%H:%M}"
    return f"Market {state}. Trading day {day}, session {hours} New York time."


def main(argv: Sequence[str] = sys.argv[1:]) -> int:
    try:
        settings = load_settings()
    except SettingsError as error:
        print(f"Settings: NOT OK\n  {error}")
        return 1
    key_id = settings.alpaca_key_id.get_secret_value()
    secret = settings.alpaca_secret_key.get_secret_value()
    timeout = settings.price_timeout_seconds
    prices = AlpacaPriceSource(key_id, secret, settings.alpaca_data_url, timeout)
    calendar = AlpacaCalendar(key_id, secret, settings.alpaca_trading_url, timeout)
    results = check_alpaca(prices, calendar, utc_now())
    print(render(results))
    return 0 if all(result.ok for result in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
