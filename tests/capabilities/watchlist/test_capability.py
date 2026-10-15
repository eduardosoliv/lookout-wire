from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import pytest

from lookout_wire.capabilities.watchlist.capability import HEALTH_THRESHOLD, WatchlistCapability
from lookout_wire.capabilities.watchlist.config import WatchlistFile
from lookout_wire.capabilities.watchlist.rules import (
    Alert,
    AlertKind,
    Kind,
    LevelState,
    TickerState,
)
from lookout_wire.capabilities.watchlist.state import (
    Marker,
    Phase,
    WatchlistState,
    load_state,
    save_state,
)
from lookout_wire.core.capability import FatalError
from lookout_wire.core.ports import (
    CalendarError,
    Explanation,
    Ports,
    PriceError,
    SendOutcome,
    SendStatus,
    Session,
    Trade,
)
from tests.fakes import FakeCalendar, FakeClock, FakeExplainer, FakeMessenger, FakePriceSource

NY = ZoneInfo("America/New_York")
TODAY = date(2026, 10, 15)
YESTERDAY = date(2026, 10, 14)
OPEN = Session(
    True, TODAY, datetime(2026, 10, 15, 9, 30, tzinfo=NY), datetime(2026, 10, 15, 16, 0, tzinfo=NY)
)
NOW = datetime(2026, 10, 15, 14, 0, tzinfo=UTC)
EXPLANATION = Explanation("Shares fell after earnings.", ("https://news.test/a",))


def trade(price, when=NOW):
    return Trade(price=price, timestamp=when)


class World:
    def __init__(self, tmp_path, yaml="tickers:\n  - symbol: AAPL\n    dip: 150\n"):
        self.watchlist_path = tmp_path / "watchlist.yaml"
        self.watchlist_path.write_text(yaml)
        self.state_path = tmp_path / "state" / "watchlist.json"
        self.prices = FakePriceSource()
        self.calendar = FakeCalendar(OPEN)
        self.messenger = FakeMessenger()
        self.explainer = FakeExplainer()
        self.clock = FakeClock(NOW)

    def ports(self):
        return Ports(
            self.prices,
            self.calendar,
            self.messenger,
            self.explainer,
            self.state_path.parent,
            self.clock,
        )

    def capability(self):
        return WatchlistCapability(
            WatchlistFile(self.watchlist_path), self.state_path, self.ports()
        )

    def level(self, symbol="AAPL", kind="dip"):
        return getattr(load_state(self.state_path).tickers[symbol], kind)

    def markers(self):
        return load_state(self.state_path).markers


@pytest.fixture
def world(tmp_path):
    return World(tmp_path)


def test_covers_ae1_crossing_sends_one_message_and_persists_disarmed(world):
    world.prices.trades = {"AAPL": trade(149.8)}

    world.capability().run(NOW)

    assert world.messenger.sent == ["*AAPL* $149.80\n⚠️ Dip 0.13% below $150.00"]
    level = world.level()
    assert (level.armed, level.baseline, level.last_message_day) == (False, 149.8, TODAY)
    assert world.markers() == {}


def test_covers_ae2_both_levels_crossing_send_one_message(tmp_path):
    world = World(tmp_path, "tickers:\n  - symbol: NVDA\n    dip: 150\n    buy: 130\n")
    world.prices.trades = {"NVDA": trade(128.0)}

    world.capability().run(NOW)

    assert len(world.messenger.sent) == 1
    assert "🔥 Buy" in world.messenger.sent[0] and "⚠️ Dip" not in world.messenger.sent[0]


def test_explanation_is_attached_when_available(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.explainer.result = EXPLANATION

    world.capability().run(NOW)

    assert world.messenger.sent[0].endswith("Shares fell after earnings.\n\nhttps://news.test/a")
    assert "AAPL" in world.explainer.questions[0]


def test_covers_ae9_failed_explanation_still_sends_the_alert(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.explainer.result = None

    world.capability().run(NOW)

    assert world.messenger.sent == ["*AAPL* $149.80\n⚠️ Dip 0.13% below $150.00"]


def test_covers_ae10_closed_session_fetches_nothing_and_sends_nothing(world):
    world.calendar.current = Session(False, TODAY, OPEN.open, OPEN.close)
    world.prices.trades = {"AAPL": trade(149.8)}

    world.capability().run(NOW)

    assert world.prices.calls == []
    assert world.explainer.questions == []
    assert world.messenger.sent == []


def test_covers_ae17_trade_before_the_open_is_skipped(world):
    world.prices.trades = {"AAPL": trade(149.8, datetime(2026, 10, 15, 13, 0, tzinfo=UTC))}

    world.capability().run(NOW)

    assert world.messenger.sent == []
    assert world.level().armed


def test_covers_ae7_restart_after_alert_sends_nothing(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.capability().run(NOW)
    world.prices.trades = {"AAPL": trade(149.0)}

    world.capability().run(NOW)

    assert len(world.messenger.sent) == 1


def test_in_flight_marker_resolves_as_sent_with_the_marker_day(world):
    alert = Alert(Kind.DIP, 150.0, AlertKind.CROSSED, 149.8)
    ticker = TickerState("AAPL", LevelState(Kind.DIP, 150.0))
    marker = Marker(Phase.IN_FLIGHT, YESTERDAY, (alert,))
    save_state(world.state_path, WatchlistState({"AAPL": ticker}, {"AAPL": marker}))
    world.prices.trades = {"AAPL": trade(149.9)}

    world.capability().run(NOW)

    assert world.messenger.sent == []
    level = world.level()
    assert (level.armed, level.baseline, level.last_message_day) == (False, 149.8, YESTERDAY)


def test_not_sent_marker_is_retried_without_a_second_explainer_call(world):
    alert = Alert(Kind.DIP, 150.0, AlertKind.CROSSED, 149.8)
    ticker = TickerState("AAPL", LevelState(Kind.DIP, 150.0))
    marker = Marker(Phase.NOT_SENT, TODAY, (alert,), EXPLANATION.text, EXPLANATION.links)
    save_state(world.state_path, WatchlistState({"AAPL": ticker}, {"AAPL": marker}))
    world.prices.trades = {"AAPL": trade(149.8)}

    world.capability().run(NOW)

    assert world.explainer.questions == []
    assert len(world.messenger.sent) == 1
    assert "Shares fell after earnings." in world.messenger.sent[0]
    assert world.markers() == {}


def test_rate_limited_send_keeps_level_armed_and_marker_not_sent(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.explainer.result = EXPLANATION
    world.messenger.outcomes.append(SendOutcome(SendStatus.RATE_LIMITED))

    world.capability().run(NOW)

    assert world.level().armed
    marker = world.markers()["AAPL"]
    assert marker.phase == Phase.NOT_SENT
    assert marker.explanation_text == EXPLANATION.text


def test_restart_after_rate_limited_send_does_not_record_it_as_sent(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.RATE_LIMITED))
    world.capability().run(NOW)

    world.capability().run(NOW)

    assert len(world.messenger.sent) == 2
    assert not world.level().armed


def test_not_ready_messenger_defers_the_alert(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.messenger.is_ready = False

    world.capability().run(NOW)

    assert world.messenger.sent == []
    assert world.level().armed


def test_not_sent_marker_is_dropped_when_the_price_recovers(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.explainer.result = EXPLANATION
    world.messenger.outcomes.append(SendOutcome(SendStatus.NOT_ACCEPTED))
    capability = world.capability()
    capability.run(NOW)
    world.prices.trades = {"AAPL": trade(151.0)}

    capability.run(NOW)

    assert world.markers() == {}
    world.prices.trades = {"AAPL": trade(149.0)}
    capability.run(NOW)
    assert len(world.explainer.questions) == 2


def test_dip_and_buy_land_in_one_marker(tmp_path):
    world = World(tmp_path, "tickers:\n  - symbol: NVDA\n    dip: 150\n    buy: 130\n")
    world.prices.trades = {"NVDA": trade(128.0)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.RATE_LIMITED))

    world.capability().run(NOW)

    marker = world.markers()["NVDA"]
    assert {a.kind for a in marker.alerts} == {Kind.DIP, Kind.BUY}


def test_failed_marker_write_skips_the_send(world, monkeypatch):
    from lookout_wire.capabilities.watchlist import capability as module

    world.prices.trades = {"AAPL": trade(149.8)}
    capability = world.capability()

    def fail(*args):
        raise OSError("disk full")

    monkeypatch.setattr(module, "save_state", fail)
    with pytest.raises(OSError):
        capability.run(NOW)

    assert world.messenger.sent == []
    assert world.markers() == {}


def test_covers_ae15_rejected_recipient_records_the_level_as_sent(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.REJECTED_RECIPIENT))

    world.capability().run(NOW)

    assert not world.level().armed
    assert len(world.explainer.questions) == 1


def test_other_client_error_records_the_level_as_sent(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.OTHER_CLIENT_ERROR))

    world.capability().run(NOW)

    assert not world.level().armed


def test_covers_ae14_unknown_outcome_records_the_level_as_sent(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.UNKNOWN))

    world.capability().run(NOW)

    assert not world.level().armed
    assert world.markers() == {}


def test_covers_ae16_fatal_send_persists_not_sent_marker_and_propagates(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    world.explainer.result = EXPLANATION
    world.messenger.outcomes.append(FatalError("token revoked"))

    with pytest.raises(FatalError):
        world.capability().run(NOW)

    marker = world.markers()["AAPL"]
    assert marker.phase == Phase.NOT_SENT
    assert marker.explanation_text == EXPLANATION.text
    world.messenger.outcomes.clear()
    world.capability().run(NOW)
    assert len(world.messenger.sent) == 2
    assert len(world.explainer.questions) == 1


def test_covers_ae20_health_notice_after_consecutive_failures_and_recovery(world):
    world.prices.error = PriceError("down")
    capability = world.capability()

    for _ in range(HEALTH_THRESHOLD + 1):
        capability.run(NOW)

    assert len(world.messenger.sent) == 1
    assert "cannot read market data" in world.messenger.sent[0]

    world.prices.error = None
    world.prices.trades = {"AAPL": trade(151.0)}
    capability.run(NOW)

    assert len(world.messenger.sent) == 2
    assert "back" in world.messenger.sent[1]


def test_clock_failures_count_toward_the_health_notice(world):
    world.calendar.error = CalendarError("down")
    capability = world.capability()

    for _ in range(HEALTH_THRESHOLD):
        capability.run(NOW)

    assert len(world.messenger.sent) == 1


def test_covers_r3_unparseable_watchlist_keeps_state(world):
    world.prices.trades = {"AAPL": trade(149.8)}
    capability = world.capability()
    capability.run(NOW)
    world.watchlist_path.write_text("tickers: [ : broken")

    capability.run(NOW)

    assert not world.level().armed
    assert len(world.messenger.sent) == 1


def test_review_1_broken_watchlist_at_startup_keeps_saved_state(world):
    alert = Alert(Kind.DIP, 150.0, AlertKind.CROSSED, 149.8)
    fired = LevelState(Kind.DIP, 150.0, armed=False, baseline=149.8, last_message_day=TODAY)
    save_state(world.state_path, WatchlistState({"AAPL": TickerState("AAPL", fired)}, {}))
    world.watchlist_path.write_text("tickers: [ : broken")
    world.prices.trades = {"AAPL": trade(149.0)}
    capability = world.capability()

    capability.run(NOW)
    world.watchlist_path.write_text("tickers:\n  - symbol: AAPL\n    dip: 150\n")
    capability.run(NOW)

    assert world.messenger.sent == []
    assert world.level() == fired
    assert alert.price == fired.baseline


def test_review_5_ticker_with_config_error_is_not_evaluated(world):
    world.prices.trades = {"AAPL": trade(151.0)}
    capability = world.capability()
    capability.run(NOW)
    world.watchlist_path.write_text("tickers:\n  - symbol: AAPL\n    dip: 150\n    buy: 160\n")
    world.prices.trades = {"AAPL": trade(149.0)}

    capability.run(NOW)

    assert world.messenger.sent == []
    assert world.level().armed


def test_review_4_readable_clock_resets_the_health_counter(world):
    capability = world.capability()
    world.calendar.error = CalendarError("blip")
    for _ in range(HEALTH_THRESHOLD - 1):
        capability.run(NOW)
    world.calendar.error = None
    world.calendar.current = Session(False, TODAY, OPEN.open, OPEN.close)
    capability.run(NOW)
    world.calendar.error = CalendarError("blip")

    for _ in range(HEALTH_THRESHOLD - 1):
        capability.run(NOW)

    assert world.messenger.sent == []


def test_review_3_failure_notice_is_retried_until_accepted(world):
    world.prices.error = PriceError("down")
    world.messenger.outcomes.append(SendOutcome(SendStatus.NOT_ACCEPTED))
    capability = world.capability()

    for _ in range(HEALTH_THRESHOLD + 1):
        capability.run(NOW)

    assert len(world.messenger.sent) == 2
    assert all("cannot read market data" in text for text in world.messenger.sent)


def test_review_3_no_recovery_notice_when_failure_notice_never_arrived(world):
    world.prices.error = PriceError("down")
    world.messenger.is_ready = False
    capability = world.capability()
    for _ in range(HEALTH_THRESHOLD):
        capability.run(NOW)

    world.prices.error = None
    world.prices.trades = {"AAPL": trade(151.0)}
    world.messenger.is_ready = True
    capability.run(NOW)

    assert world.messenger.sent == []


def test_rate_limited_marker_is_saved_before_the_cycle_ends(world, monkeypatch):
    from lookout_wire.capabilities.watchlist import capability as module

    world.prices.trades = {"AAPL": trade(149.8)}
    world.messenger.outcomes.append(SendOutcome(SendStatus.RATE_LIMITED))
    capability = world.capability()
    saved = []
    original = module.save_state

    def record(path, state):
        saved.append({s: m.phase for s, m in state.markers.items()})
        original(path, state)

    monkeypatch.setattr(module, "save_state", record)
    capability._reconcile()
    capability._check_all(NOW)

    assert {"AAPL": Phase.NOT_SENT} in saved


def test_stale_day_not_sent_marker_fetches_a_fresh_explanation(world):
    alert = Alert(Kind.DIP, 150.0, AlertKind.CROSSED, 149.8)
    ticker = TickerState("AAPL", LevelState(Kind.DIP, 150.0))
    stale = Marker(Phase.NOT_SENT, YESTERDAY, (alert,), "Yesterday's story.", ())
    save_state(world.state_path, WatchlistState({"AAPL": ticker}, {"AAPL": stale}))
    world.prices.trades = {"AAPL": trade(149.8)}
    world.explainer.result = EXPLANATION

    world.capability().run(NOW)

    assert len(world.explainer.questions) == 1
    assert "Yesterday's story." not in world.messenger.sent[0]
    assert EXPLANATION.text in world.messenger.sent[0]


def test_one_ticker_without_a_trade_does_not_block_the_other(tmp_path):
    world = World(
        tmp_path, "tickers:\n  - symbol: AAPL\n    dip: 150\n  - symbol: MSFT\n    dip: 400\n"
    )
    world.prices.trades = {"AAPL": trade(149.8)}

    world.capability().run(NOW)

    assert len(world.messenger.sent) == 1
    assert world.messenger.sent[0].startswith("*AAPL*")
    assert world.level("MSFT").armed


def test_alert_header_uses_the_company_name_from_the_watchlist(tmp_path):
    world = World(tmp_path, "tickers:\n  - symbol: AAPL\n    name: Apple Inc.\n    dip: 150\n")
    world.prices.trades = {"AAPL": trade(149.8)}

    world.capability().run(NOW)

    assert world.messenger.sent[0].splitlines()[0] == "*AAPL* (Apple Inc.) $149.80"
