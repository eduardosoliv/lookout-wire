"""Alpaca prices, market clock, and calendar over httpx.

Uses httpx directly because the alpaca-py SDK sets no request timeout, and one stalled
socket would freeze the loop.
"""

import logging
import re
from collections.abc import Sequence
from datetime import UTC, date, datetime, time
from zoneinfo import ZoneInfo

import httpx

from lookout_wire.core.ports import CalendarError, PriceError, Session, Trade

log = logging.getLogger(__name__)

NEW_YORK = ZoneInfo("America/New_York")
EXTRA_FRACTION_DIGITS = re.compile(r"(\.\d{6})\d+")


def parse_timestamp(text: str) -> datetime:
    """Alpaca sends nanosecond fractions, which datetime.fromisoformat may reject."""
    return datetime.fromisoformat(EXTRA_FRACTION_DIGITS.sub(r"\1", text))


def build_client(
    key_id: str, secret_key: str, base_url: str, timeout_seconds: float, transport
) -> httpx.Client:
    return httpx.Client(
        base_url=base_url,
        timeout=timeout_seconds,
        headers={"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret_key},
        transport=transport,
    )


class AlpacaPriceSource:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        data_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = build_client(key_id, secret_key, data_url, timeout_seconds, transport)

    def latest_trades(self, symbols: Sequence[str]) -> dict[str, Trade]:
        if not symbols:
            return {}
        snapshots = self._fetch_snapshots(symbols)
        trades = {}
        for symbol in symbols:
            trade = self._trade_from(snapshots.get(symbol))
            if trade is None:
                log.warning("No latest trade for %s", symbol)
                continue
            trades[symbol] = trade
        return trades

    def close(self) -> None:
        self._client.close()

    def _fetch_snapshots(self, symbols: Sequence[str]) -> dict:
        params = {"symbols": ",".join(symbols), "feed": "iex"}
        try:
            response = self._client.get("/v2/stocks/snapshots", params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise PriceError(f"snapshot request failed: {type(error).__name__}") from error

    @staticmethod
    def _trade_from(snapshot: dict | None) -> Trade | None:
        if not snapshot or not snapshot.get("latestTrade"):
            return None
        latest = snapshot["latestTrade"]
        return Trade(
            price=float(latest["p"]), timestamp=parse_timestamp(latest["t"]).astimezone(UTC)
        )


class AlpacaCalendar:
    def __init__(
        self,
        key_id: str,
        secret_key: str,
        trading_url: str,
        timeout_seconds: float,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        self._client = build_client(key_id, secret_key, trading_url, timeout_seconds, transport)
        self._bounds: dict[date, tuple[datetime | None, datetime | None]] = {}

    def session(self, now: datetime) -> Session:
        clock = self._get("/v2/clock")
        trading_day = parse_timestamp(clock["timestamp"]).astimezone(NEW_YORK).date()
        if trading_day not in self._bounds:
            self._bounds[trading_day] = self._fetch_bounds(trading_day)
        open_, close = self._bounds[trading_day]
        return Session(
            is_open=bool(clock["is_open"]), trading_day=trading_day, open=open_, close=close
        )

    def close(self) -> None:
        self._client.close()

    def _fetch_bounds(self, day: date) -> tuple[datetime | None, datetime | None]:
        days = self._get("/v2/calendar", params={"start": day.isoformat(), "end": day.isoformat()})
        if not days:
            return None, None
        entry = days[0]
        return at_new_york(day, entry["open"]), at_new_york(day, entry["close"])

    def _get(self, path: str, params: dict | None = None):
        try:
            response = self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except (httpx.HTTPError, ValueError) as error:
            raise CalendarError(f"{path} request failed: {type(error).__name__}") from error


def at_new_york(day: date, hours_minutes: str) -> datetime:
    return datetime.combine(day, time.fromisoformat(hours_minutes), tzinfo=NEW_YORK)
