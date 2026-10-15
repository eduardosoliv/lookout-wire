from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

from lookout_wire.core.ports import Session, Trade, in_session

NY = ZoneInfo("America/New_York")
SESSION = Session(
    is_open=True,
    trading_day=date(2026, 10, 15),
    open=datetime(2026, 10, 15, 9, 30, tzinfo=NY),
    close=datetime(2026, 10, 15, 16, 0, tzinfo=NY),
)


def trade_at(hour, minute):
    return Trade(price=100.0, timestamp=datetime(2026, 10, 15, hour, minute, tzinfo=UTC))


def test_trade_inside_the_session_counts():
    assert in_session(trade_at(14, 0), SESSION)  # 10:00 New York


def test_trade_before_the_open_does_not_count():
    assert not in_session(trade_at(13, 0), SESSION)  # 09:00 New York


def test_trade_after_the_close_does_not_count():
    assert not in_session(trade_at(20, 30), SESSION)  # 16:30 New York


def test_no_trade_counts_on_a_day_without_a_session():
    closed = Session(is_open=False, trading_day=date(2026, 10, 17))

    assert not in_session(trade_at(14, 0), closed)
