from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from lookout_wire.checks import check_alpaca, render
from lookout_wire.core.ports import CalendarError, PriceError, Session, Trade
from tests.fakes import FakeCalendar, FakePriceSource

NY = ZoneInfo("America/New_York")
NOW = datetime(2026, 10, 15, 22, 11, tzinfo=UTC)
CLOSED = Session(
    False,
    date(2026, 10, 15),
    datetime(2026, 10, 15, 9, 30, tzinfo=NY),
    datetime(2026, 10, 15, 16, 0, tzinfo=NY),
)
TRADE = Trade(price=341.91, timestamp=datetime(2026, 10, 15, 19, 59, 59, tzinfo=UTC))


def test_both_checks_pass_with_readable_details():
    results = check_alpaca(FakePriceSource({"AAPL": TRADE}), FakeCalendar(CLOSED), NOW)

    text = render(results)

    assert [r.ok for r in results] == [True, True]
    assert "Alpaca prices: OK" in text
    assert "AAPL last trade 341.91 at 2026-10-15 15:59:59 New York time" in text
    assert "Alpaca market clock: OK" in text
    assert "Market closed. Trading day 2026-10-15, session 09:30 to 16:00 New York time" in text


def test_failed_price_fetch_is_not_ok_with_the_reason():
    results = check_alpaca(
        FakePriceSource(error=PriceError("snapshot request failed: HTTP 403")),
        FakeCalendar(CLOSED),
        NOW,
    )

    text = render(results)

    assert results[0].ok is False
    assert "Alpaca prices: NOT OK" in text
    assert "HTTP 403" in text


def test_missing_trade_is_not_ok():
    results = check_alpaca(FakePriceSource({}), FakeCalendar(CLOSED), NOW)

    assert results[0].ok is False
    assert "no latest trade for AAPL" in render(results)


def test_failed_clock_is_not_ok_with_a_hint_about_live_keys():
    results = check_alpaca(
        FakePriceSource({"AAPL": TRADE}),
        FakeCalendar(error=CalendarError("/v2/clock request failed: HTTP 401")),
        NOW,
    )

    text = render(results)

    assert results[1].ok is False
    assert "Alpaca market clock: NOT OK" in text
    assert "LOOKOUT_ALPACA_TRADING_URL" in text


def test_day_without_a_session_says_so():
    holiday = Session(False, date(2026, 10, 17))

    text = render(check_alpaca(FakePriceSource({"AAPL": TRADE}), FakeCalendar(holiday), NOW))

    assert "No session on 2026-10-17" in text
