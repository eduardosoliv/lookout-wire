from datetime import UTC, datetime

import pytest

from lookout_wire.capabilities.watchlist.config import WatchlistFile
from lookout_wire.capabilities.watchlist.scan import scan
from lookout_wire.core.capability import FatalError
from lookout_wire.core.ports import Explanation, Ports, SendOutcome, SendStatus, Trade
from tests.fakes import FakeCalendar, FakeClock, FakeExplainer, FakeMessenger, FakePriceSource

NOW = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)
YAML = """tickers:
  - symbol: NVDA
    dip: 200
    buy: 180
  - symbol: AAPL
    dip: 235
    buy: 200
  - symbol: MSFT
    dip: 440
"""


def trade(price):
    return Trade(price=price, timestamp=NOW)


class World:
    def __init__(self, tmp_path):
        self.path = tmp_path / "watchlist.yaml"
        self.path.write_text(YAML)
        self.state_dir = tmp_path / "state"
        self.prices = FakePriceSource()
        self.messenger = FakeMessenger()
        self.explainer = FakeExplainer()
        self.clock = FakeClock(NOW)
        self.sleeps: list[float] = []

    def run(self):
        ports = Ports(
            self.prices, FakeCalendar(), self.messenger, self.explainer, self.state_dir, self.clock
        )
        return scan(WatchlistFile(self.path), ports, sleep=self.sleep)

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        self.clock.advance(seconds=seconds)
        self.messenger.is_ready = True


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_sends_one_message_per_ticker_below_a_level_ignoring_the_market(world):
    world.prices.trades = {"NVDA": trade(178.5), "AAPL": trade(341.9), "MSFT": trade(430.0)}

    report = world.run()

    assert [text.splitlines()[0] for text in world.messenger.sent] == ["NVDA 178.50", "MSFT 430.00"]
    assert "⚠️ Dip 200.00" in world.messenger.sent[0] and "🔥 Buy 180.00" in world.messenger.sent[0]
    assert report.sent == ["NVDA", "MSFT"]
    assert report.above == ["AAPL"]


def test_scan_never_writes_alert_state(world):
    world.prices.trades = {"NVDA": trade(178.5)}

    world.run()

    assert not world.state_dir.exists()


def test_ticker_without_a_price_is_reported(world):
    world.prices.trades = {"NVDA": trade(250.0)}

    report = world.run()

    assert report.no_price == ["AAPL", "MSFT"]
    assert world.messenger.sent == []


def test_explanation_is_attached_when_available(world):
    world.prices.trades = {"MSFT": trade(430.0)}
    world.explainer.result = Explanation("Cloud growth slowed.", ("https://news.test/a",))

    world.run()

    assert world.messenger.sent[0].endswith("Cloud growth slowed.\n\nhttps://news.test/a")


def test_waits_until_the_messenger_is_ready(world):
    world.prices.trades = {"MSFT": trade(430.0)}
    world.messenger.is_ready = False

    world.run()

    assert world.sleeps
    assert len(world.messenger.sent) == 1


def test_rate_limited_send_is_retried(world):
    world.prices.trades = {"MSFT": trade(430.0)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.RATE_LIMITED))

    report = world.run()

    assert len(world.messenger.sent) == 2
    assert report.sent == ["MSFT"]


def test_send_that_keeps_failing_is_reported(world):
    world.prices.trades = {"MSFT": trade(430.0)}
    world.messenger.outcomes.extend([SendOutcome(SendStatus.NOT_ACCEPTED)] * 5)

    report = world.run()

    assert report.failed == ["MSFT"]


def test_fatal_error_stops_the_scan(world):
    world.prices.trades = {"NVDA": trade(178.5), "MSFT": trade(430.0)}
    world.messenger.outcomes.append(FatalError("token revoked"))

    with pytest.raises(FatalError):
        world.run()
