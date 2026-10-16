from datetime import date

from lookout_wire.capabilities.watchlist.config import Entry
from lookout_wire.capabilities.watchlist.rules import (
    Alert,
    AlertKind,
    Kind,
    LevelState,
    TickerState,
    evaluate,
    reconcile,
    record_sent,
)

YESTERDAY = date(2026, 10, 14)
TODAY = date(2026, 10, 15)
TOMORROW = date(2026, 10, 16)


def dip(**fields) -> LevelState:
    return LevelState(kind=Kind.DIP, level=150, **fields)


def fired(price: float, day: date) -> LevelState:
    return dip(armed=False, baseline=price, last_message_day=day)


def test_armed_dip_alerts_when_the_price_crosses_below():
    decision = evaluate(dip(), 149.80, TODAY)

    assert decision.alert == Alert(Kind.DIP, 150, AlertKind.CROSSED, 149.80)
    assert decision.state == fired(149.80, TODAY)


def test_dip_and_buy_both_fire_in_one_cycle():
    buy = LevelState(kind=Kind.BUY, level=130)

    dip_decision = evaluate(dip(), 128, TODAY)
    buy_decision = evaluate(buy, 128, TODAY)

    assert dip_decision.alert.alert == AlertKind.CROSSED
    assert buy_decision.alert.alert == AlertKind.CROSSED
    assert not dip_decision.state.armed
    assert not buy_decision.state.armed


def test_lower_price_on_the_same_day_sends_nothing():
    decision = evaluate(fired(149.80, TODAY), 147, TODAY)

    assert decision.alert is None
    assert decision.state == fired(149.80, TODAY)


def test_new_low_on_a_later_day_alerts_once_that_day():
    first = evaluate(fired(149.80, YESTERDAY), 147, TODAY)
    second = evaluate(first.state, 145, TODAY)

    assert first.alert == Alert(Kind.DIP, 150, AlertKind.NEW_LOW, 147)
    assert first.state == fired(147, TODAY)
    assert second.alert is None
    assert second.state == fired(147, TODAY)


def test_price_above_the_last_notified_low_sends_nothing():
    decision = evaluate(fired(149.80, YESTERDAY), 149.90, TODAY)

    assert decision.alert is None
    assert decision.state == fired(149.80, YESTERDAY)


def test_level_rearms_silently_then_alerts_on_the_next_cross():
    rearmed = evaluate(fired(149.80, YESTERDAY), 150.00, TODAY)
    crossed = evaluate(rearmed.state, 149.50, TODAY)

    assert rearmed.alert is None
    assert rearmed.state == dip(armed=True, baseline=149.80, last_message_day=YESTERDAY)
    assert crossed.alert == Alert(Kind.DIP, 150, AlertKind.CROSSED, 149.50)
    assert crossed.state == fired(149.50, TODAY)


def test_same_day_recross_is_silent_and_keeps_the_last_notified_low():
    rearmed = evaluate(fired(149.90, TODAY), 150.05, TODAY)
    capped = evaluate(rearmed.state, 149.85, TODAY)
    later = evaluate(capped.state, 149.87, TOMORROW)

    assert rearmed.alert is None
    assert capped.alert is None
    assert capped.state == fired(149.90, TODAY)
    assert later.alert == Alert(Kind.DIP, 150, AlertKind.NEW_LOW, 149.87)


def test_armed_level_above_price_is_unchanged():
    decision = evaluate(dip(), 150, TODAY)

    assert decision.alert is None
    assert decision.state == dip()


def test_buy_fires_while_dip_is_disarmed():
    states = {
        "NVDA": TickerState(
            symbol="NVDA",
            dip=fired(149.80, TODAY),
            buy=LevelState(kind=Kind.BUY, level=130),
        )
    }

    decisions = [evaluate(level, 129, TODAY) for level in states["NVDA"].levels()]

    assert [d.alert.kind for d in decisions if d.alert] == [Kind.BUY]


def test_record_sent_sets_baseline_and_day():
    alert = Alert(Kind.DIP, 150, AlertKind.CROSSED, 149.80)

    assert record_sent(dip(), alert, TODAY) == fired(149.80, TODAY)


def test_changed_level_resets_to_armed():
    states = {"AAPL": TickerState(symbol="AAPL", dip=fired(149.80, YESTERDAY))}

    states = reconcile(states, [Entry("AAPL", dip=145)], keep=set())
    level = states["AAPL"].dip
    quiet = evaluate(level, 149, TODAY)
    crossed = evaluate(quiet.state, 144, TODAY)

    assert level == LevelState(kind=Kind.DIP, level=145)
    assert quiet.alert is None
    assert quiet.state.armed
    assert crossed.alert.alert == AlertKind.CROSSED


def test_reconcile_adds_new_symbol_armed():
    states = reconcile({}, [Entry("NVDA", dip=150, buy=130)], keep=set())

    assert states == {
        "NVDA": TickerState(
            symbol="NVDA",
            dip=LevelState(kind=Kind.DIP, level=150),
            buy=LevelState(kind=Kind.BUY, level=130),
        )
    }


def test_reconcile_drops_removed_ticker():
    states = {"AAPL": TickerState(symbol="AAPL", dip=fired(149.80, TODAY))}

    assert reconcile(states, [], keep=set()) == {}


def test_reconcile_keeps_skipped_ticker_untouched():
    states = {"AAPL": TickerState(symbol="AAPL", dip=fired(149.80, TODAY))}

    assert reconcile(states, [], keep={"AAPL"}) == states


def test_reconcile_keeps_unchanged_levels():
    buy = LevelState(kind=Kind.BUY, level=130, armed=False, baseline=129, last_message_day=TODAY)
    states = {"NVDA": TickerState(symbol="NVDA", dip=fired(149.80, TODAY), buy=buy)}

    assert reconcile(states, [Entry("NVDA", dip=150, buy=130)], keep=set()) == states


def test_reconcile_removed_buy_drops_only_buy():
    buy = LevelState(kind=Kind.BUY, level=130, armed=False, baseline=129)
    states = {"NVDA": TickerState(symbol="NVDA", dip=fired(149.80, TODAY), buy=buy)}

    result = reconcile(states, [Entry("NVDA", dip=150)], keep=set())

    assert result["NVDA"] == TickerState(symbol="NVDA", dip=fired(149.80, TODAY))


def test_reconcile_added_buy_is_fresh_and_dip_kept():
    states = {"NVDA": TickerState(symbol="NVDA", dip=fired(149.80, TODAY))}

    result = reconcile(states, [Entry("NVDA", dip=150, buy=120)], keep=set())

    assert result["NVDA"].dip == fired(149.80, TODAY)
    assert result["NVDA"].buy == LevelState(kind=Kind.BUY, level=120)
