import logging
from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from lookout_wire.adapters.alpaca import AlpacaCalendar, AlpacaPriceSource
from lookout_wire.core.ports import CalendarError, PriceError, Session, Trade

NEW_YORK = ZoneInfo("America/New_York")
DATA_URL = "https://data.example"
TRADING_URL = "https://trading.example"
NOW = datetime(2026, 10, 15, 15, 0, tzinfo=UTC)


def price_source(handler) -> AlpacaPriceSource:
    return AlpacaPriceSource("key", "secret", DATA_URL, 5.0, transport=httpx.MockTransport(handler))


def calendar(handler) -> AlpacaCalendar:
    return AlpacaCalendar("key", "secret", TRADING_URL, 5.0, transport=httpx.MockTransport(handler))


def trading_handler(clock: dict, days: list, calls: list):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v2/clock":
            return httpx.Response(200, json=clock)
        return httpx.Response(200, json=days)

    return handle


def calendar_calls(calls: list) -> list:
    return [request for request in calls if request.url.path == "/v2/calendar"]


def test_snapshot_for_two_symbols_returns_two_trades_in_utc():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "AAPL": {"latestTrade": {"p": 190.5, "t": "2026-10-15T14:30:01.123456789Z"}},
                "MSFT": {"latestTrade": {"p": 410.25, "t": "2026-10-15T10:31:00-04:00"}},
            },
        )

    trades = price_source(handle).latest_trades(["AAPL", "MSFT"])

    assert trades == {
        "AAPL": Trade(190.5, datetime(2026, 10, 15, 14, 30, 1, 123456, tzinfo=UTC)),
        "MSFT": Trade(410.25, datetime(2026, 10, 15, 14, 31, tzinfo=UTC)),
    }
    assert trades["MSFT"].timestamp.tzinfo is UTC
    request = requests[0]
    assert request.url.path == "/v2/stocks/snapshots"
    assert request.url.params["symbols"] == "AAPL,MSFT"
    assert request.url.params["feed"] == "iex"
    assert request.headers["APCA-API-KEY-ID"] == "key"
    assert request.headers["APCA-API-SECRET-KEY"] == "secret"


def test_symbol_missing_or_without_latest_trade_is_omitted_and_logged(caplog):
    def handle(request):
        return httpx.Response(
            200,
            json={
                "AAPL": {"latestTrade": {"p": 190.5, "t": "2026-10-15T14:30:01Z"}},
                "MSFT": {"latestQuote": {}},
            },
        )

    with caplog.at_level(logging.WARNING):
        trades = price_source(handle).latest_trades(["AAPL", "MSFT", "NVDA"])

    assert list(trades) == ["AAPL"]
    assert "MSFT" in caplog.text
    assert "NVDA" in caplog.text


def test_read_timeout_raises_price_error():
    def handle(request):
        raise httpx.ReadTimeout("slow", request=request)

    with pytest.raises(PriceError):
        price_source(handle).latest_trades(["AAPL"])


def test_non_2xx_raises_price_error():
    with pytest.raises(PriceError):
        price_source(lambda request: httpx.Response(403)).latest_trades(["AAPL"])


def test_empty_symbol_list_makes_no_request():
    def handle(request):
        raise AssertionError("no request expected")

    assert price_source(handle).latest_trades([]) == {}


def test_closed_clock_yields_closed_session():
    clock = {"timestamp": "2026-10-15T17:00:00.000-04:00", "is_open": False}
    days = [{"date": "2026-10-15", "open": "09:30", "close": "16:00"}]

    session = calendar(trading_handler(clock, days, [])).session(NOW)

    assert session.is_open is False


def test_open_session_has_new_york_bounds_and_auth_headers():
    clock = {"timestamp": "2026-10-15T11:00:00.000-04:00", "is_open": True}
    days = [{"date": "2026-10-15", "open": "09:30", "close": "16:00"}]
    calls = []

    session = calendar(trading_handler(clock, days, calls)).session(NOW)

    assert session == Session(
        is_open=True,
        trading_day=date(2026, 10, 15),
        open=datetime(2026, 10, 15, 9, 30, tzinfo=NEW_YORK),
        close=datetime(2026, 10, 15, 16, 0, tzinfo=NEW_YORK),
    )
    request = calendar_calls(calls)[0]
    assert request.url.params["start"] == "2026-10-15"
    assert request.url.params["end"] == "2026-10-15"
    assert request.headers["APCA-API-KEY-ID"] == "key"
    assert request.headers["APCA-API-SECRET-KEY"] == "secret"


def test_early_close_ends_session_at_13_00_eastern():
    clock = {"timestamp": "2026-11-27T10:00:00.000-05:00", "is_open": True}
    days = [{"date": "2026-11-27", "open": "09:30", "close": "13:00"}]

    session = calendar(trading_handler(clock, days, [])).session(NOW)

    assert session.close == datetime(2026, 11, 27, 13, 0, tzinfo=NEW_YORK)


def test_trading_day_at_23_30_utc_is_the_eastern_date():
    clock = {"timestamp": "2026-10-15T23:30:00Z", "is_open": False}
    days = [{"date": "2026-10-15", "open": "09:30", "close": "16:00"}]
    calls = []

    session = calendar(trading_handler(clock, days, calls)).session(NOW)

    assert session.trading_day == date(2026, 10, 15)
    assert calendar_calls(calls)[0].url.params["start"] == "2026-10-15"


def test_second_call_on_same_trading_day_uses_cached_calendar():
    clock = {"timestamp": "2026-10-15T11:00:00.000-04:00", "is_open": True}
    days = [{"date": "2026-10-15", "open": "09:30", "close": "16:00"}]
    calls = []
    source = calendar(trading_handler(clock, days, calls))

    first = source.session(NOW)
    second = source.session(NOW)

    assert first == second
    assert len(calendar_calls(calls)) == 1
    assert len(calls) == 3


def test_empty_calendar_on_saturday_yields_closed_session_and_is_cached():
    clock = {"timestamp": "2026-10-17T11:00:00.000-04:00", "is_open": False}
    calls = []
    source = calendar(trading_handler(clock, [], calls))

    first = source.session(NOW)
    second = source.session(NOW)

    assert first == Session(is_open=False, trading_day=date(2026, 10, 17))
    assert second == first
    assert len(calendar_calls(calls)) == 1


def test_clock_timeout_raises_calendar_error():
    def handle(request):
        raise httpx.ConnectTimeout("slow", request=request)

    with pytest.raises(CalendarError):
        calendar(handle).session(NOW)


def test_calendar_non_2xx_raises_calendar_error():
    def handle(request):
        if request.url.path == "/v2/clock":
            return httpx.Response(
                200, json={"timestamp": "2026-10-15T11:00:00-04:00", "is_open": True}
            )
        return httpx.Response(500)

    with pytest.raises(CalendarError):
        calendar(handle).session(NOW)


def test_snapshot_body_that_is_not_json_raises_price_error():
    source = price_source(lambda request: httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(PriceError):
        source.latest_trades(["AAPL"])


def test_clock_body_that_is_not_json_raises_calendar_error():
    cal = calendar(lambda request: httpx.Response(200, text="<html>maintenance</html>"))

    with pytest.raises(CalendarError):
        cal.session(datetime(2026, 10, 15, 14, 0, tzinfo=UTC))
