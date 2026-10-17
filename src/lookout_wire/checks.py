"""Live checks against real services, with a short OK / NOT OK report for each."""

import sys
from collections import Counter
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http import HTTPStatus
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import httpx

from lookout_wire.adapters.alpaca import AlpacaCalendar, AlpacaKeys, AlpacaPriceSource
from lookout_wire.adapters.whatsapp import error_fields
from lookout_wire.capabilities.watchlist.config import Entry, WatchlistFile
from lookout_wire.core.loop import utc_now
from lookout_wire.core.ports import (
    CalendarError,
    MarketCalendar,
    PriceError,
    PriceSource,
    Session,
)
from lookout_wire.core.settings import Settings, SettingsError, load_settings

NEW_YORK = ZoneInfo("America/New_York")
TEST_SYMBOL = "AAPL"
REPLY_WINDOW = timedelta(hours=24)
WHATSAPP_TIMEOUT = 15.0
DEFAULT_SEND_TEXT = "lookout-wire proactive check"
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


def check_watchlist(path: Path) -> CheckResult:
    name = "Watchlist"
    if not path.exists():
        return CheckResult(
            name, False, [f"{path} not found. Create it: cp watchlist.example.yaml {path}"]
        )
    parsed = WatchlistFile(path).read()
    problems = [*parsed.errors, *_duplicates(parsed.entries)]
    if problems:
        return CheckResult(name, False, problems)
    return CheckResult(name, True, [_summary(parsed.entries), f"file: {path}"])


def _duplicates(entries: list[Entry]) -> list[str]:
    counts = Counter(entry.symbol for entry in entries)
    return [
        f"{symbol} is listed {count} times; only the last entry is used"
        for symbol, count in counts.items()
        if count > 1
    ]


def _summary(entries: list[Entry]) -> str:
    both = sum(1 for entry in entries if entry.buy is not None)
    dip_only = len(entries) - both
    counts = f"{both} with a Dip & Buy level, {dip_only} with a Dip level only"
    return f"{len(entries)} tickers: {counts}"


def whatsapp_client(
    token: str, base_url: str, transport: httpx.BaseTransport | None = None
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=WHATSAPP_TIMEOUT,
        headers={"Authorization": f"Bearer {token}"},
        transport=transport,
    )


def check_whatsapp_id(client: httpx.Client, now: datetime) -> CheckResult:
    name = "WhatsApp agent"
    try:
        response = client.get("/updates", params={"offset": 0, "limit": 100, "timeout": 0})
    except httpx.HTTPError as error:
        return CheckResult(name, False, [f"request failed: {type(error).__name__}"])
    if response.status_code == HTTPStatus.NO_CONTENT:
        return CheckResult(
            name,
            False,
            ["No messages yet. Send any message to your agent in WhatsApp, then run this again."],
        )
    if not response.is_success:
        return CheckResult(name, False, [_whatsapp_error(response)])
    return _describe_updates(name, response.json(), now)


def check_whatsapp_send(client: httpx.Client, recipient: str, text: str) -> CheckResult:
    name = "WhatsApp send"
    if not recipient.startswith("user:"):
        return CheckResult(
            name, False, [f"'{recipient}' is not an id; it should look like user:123."]
        )
    body = {
        "messaging_product": "whatsapp",
        "to": recipient,
        "type": "text",
        "text": {"body": text},
    }
    try:
        response = client.post("/messages", json=body)
    except httpx.HTTPError as error:
        return CheckResult(name, False, [f"request failed: {type(error).__name__}"])
    if not response.is_success:
        return CheckResult(name, False, [_whatsapp_error(response)])
    return CheckResult(
        name,
        True,
        [
            f"Accepted for {recipient}, message id {_sent_id(response)}",
            "Check that it arrived in WhatsApp.",
        ],
    )


def _describe_updates(name: str, batch: dict[str, Any], now: datetime) -> CheckResult:
    values = [c.get("value", {}) for e in batch.get("entry", []) for c in e.get("changes", [])]
    owner = _owner_id(values)
    if owner is None:
        return CheckResult(
            name,
            False,
            ["No id found. Send any message to your agent in WhatsApp, then run this again."],
        )
    details = [f"Your id: {owner}"]
    last = _last_message_time(values, owner)
    if last is None:
        details.append("No message from you in the last 30 days.")
        details.append(f"Ready for the 24-hour check: just whatsapp-send {owner}")
        return CheckResult(name, True, details)
    details.append(f"Last message from you: {_new_york(last)} New York time")
    ready_at = last + REPLY_WINDOW
    if now >= ready_at:
        details.append(f"Ready for the 24-hour check: just whatsapp-send {owner}")
    else:
        details.append(f"Wait until {_new_york(ready_at)} New York time before the 24-hour check.")
    return CheckResult(name, True, details)


def _owner_id(values: list[dict[str, Any]]) -> str | None:
    for value in values:
        for message in value.get("messages", []):
            return message["from"]
        for contact in value.get("contacts", []):
            return contact["wa_id"]
        for status in value.get("statuses", []):
            return status["recipient_id"]
    return None


def _last_message_time(values: list[dict[str, Any]], owner: str) -> datetime | None:
    stamps = [
        int(message["timestamp"])
        for value in values
        for message in value.get("messages", [])
        if message.get("from") == owner
    ]
    if not stamps:
        return None
    return datetime.fromtimestamp(max(stamps), UTC)


def _new_york(when: datetime) -> str:
    return when.astimezone(NEW_YORK).strftime("%Y-%m-%d %H:%M")


def _whatsapp_error(response: httpx.Response) -> str:
    code, message = error_fields(response)
    text = f"HTTP {response.status_code}"
    if code is not None:
        text += f" code {code}"
    return f"{text}: {message}" if message else text


def _sent_id(response: httpx.Response) -> str:
    messages = response.json().get("messages") or [{}]
    return messages[0].get("id", "unknown")


def main(argv: Sequence[str] = sys.argv[1:]) -> int:
    command, *args = argv or ["alpaca"]
    results = _run_watchlist(args) if command == "watchlist" else _run_with_settings(command, args)
    print(render(results))
    return 0 if all(result.ok for result in results) else 1


def _run_watchlist(args: Sequence[str]) -> list[CheckResult]:
    path = Path(args[0]) if args else _configured_watchlist_path()
    return [check_watchlist(path)]


def _configured_watchlist_path() -> Path:
    """The path from settings when they load; the checker must still work without keys."""
    try:
        return load_settings().watchlist_path
    except SettingsError:
        return Settings.model_fields["watchlist_path"].default


def _run_with_settings(command: str, args: Sequence[str]) -> list[CheckResult]:
    try:
        settings = load_settings()
    except SettingsError as error:
        return [CheckResult("Settings", False, [str(error)])]
    return _run(command, args, settings)


def _run(command: str, args: Sequence[str], settings: Settings) -> list[CheckResult]:
    if command == "alpaca":
        return _run_alpaca(settings)
    client = whatsapp_client(settings.whatsapp_token.get_secret_value(), settings.whatsapp_base_url)
    if command == "whatsapp-id":
        return [check_whatsapp_id(client, utc_now())]
    if command == "whatsapp-send" and args:
        text = " ".join(args[1:]) or DEFAULT_SEND_TEXT
        return [check_whatsapp_send(client, args[0], text)]
    usage = "checks alpaca | watchlist [PATH] | whatsapp-id | whatsapp-send ID [TEXT]"
    return [CheckResult("Usage", False, [usage])]


def _run_alpaca(settings: Settings) -> list[CheckResult]:
    keys = AlpacaKeys(
        settings.alpaca_key_id.get_secret_value(), settings.alpaca_secret_key.get_secret_value()
    )
    timeout = settings.price_timeout_seconds
    prices = AlpacaPriceSource(keys, settings.alpaca_data_url, timeout)
    calendar = AlpacaCalendar(keys, settings.alpaca_trading_url, timeout)
    return check_alpaca(prices, calendar, utc_now())


if __name__ == "__main__":
    raise SystemExit(main())
