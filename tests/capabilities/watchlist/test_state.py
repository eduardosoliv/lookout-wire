from datetime import date

import pytest

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
    resolve_markers,
    save_state,
)
from lookout_wire.core.state import StateFileError, temp_path

MARKER_DAY = date(2026, 10, 14)

DIP_ALERT = Alert(Kind.DIP, 150, AlertKind.CROSSED, 128)
BUY_ALERT = Alert(Kind.BUY, 130, AlertKind.NEW_LOW, 128)


def nvda(dip: LevelState | None = None, buy: LevelState | None = None) -> TickerState:
    return TickerState(
        symbol="NVDA",
        dip=dip or LevelState(kind=Kind.DIP, level=150),
        buy=buy or LevelState(kind=Kind.BUY, level=130, armed=False, baseline=129.5),
    )


def marker(phase: Phase) -> Marker:
    return Marker(
        phase=phase,
        trading_day=MARKER_DAY,
        alerts=(DIP_ALERT, BUY_ALERT),
        explanation_text="Earnings missed.",
        explanation_links=("https://example.com/a",),
    )


def test_state_round_trips_every_field(tmp_path):
    path = tmp_path / "watchlist.json"
    state = WatchlistState(
        tickers={
            "NVDA": nvda(
                dip=LevelState(
                    Kind.DIP, 150, armed=False, baseline=149.8, last_message_day=MARKER_DAY
                )
            ),
            "MSFT": TickerState(symbol="MSFT", dip=LevelState(kind=Kind.DIP, level=380)),
        },
        markers={"NVDA": marker(Phase.NOT_SENT)},
    )

    save_state(path, state)

    assert load_state(path) == state


def test_missing_file_loads_empty(tmp_path):
    assert load_state(tmp_path / "absent.json") == WatchlistState(tickers={}, markers={})


def test_corrupt_file_raises(tmp_path):
    path = tmp_path / "watchlist.json"
    path.write_text("{broken")

    with pytest.raises(StateFileError):
        load_state(path)


def test_repeated_writes_leave_no_temp_file(tmp_path):
    path = tmp_path / "watchlist.json"
    state = WatchlistState(tickers={"NVDA": nvda()}, markers={})

    for _ in range(1000):
        save_state(path, state)

    assert not temp_path(path).exists()


def test_in_flight_marker_resolves_both_levels_as_sent_on_marker_day():
    state = WatchlistState(tickers={"NVDA": nvda()}, markers={"NVDA": marker(Phase.IN_FLIGHT)})

    resolved = resolve_markers(state)

    assert resolved.markers == {}
    assert resolved.tickers["NVDA"].dip == LevelState(
        Kind.DIP, 150, armed=False, baseline=128, last_message_day=MARKER_DAY
    )
    assert resolved.tickers["NVDA"].buy == LevelState(
        Kind.BUY, 130, armed=False, baseline=128, last_message_day=MARKER_DAY
    )


def test_not_sent_marker_is_left_untouched_with_its_explanation():
    state = WatchlistState(tickers={"NVDA": nvda()}, markers={"NVDA": marker(Phase.NOT_SENT)})

    assert resolve_markers(state) == state


def test_marker_for_a_removed_ticker_is_dropped():
    state = WatchlistState(tickers={}, markers={"NVDA": marker(Phase.IN_FLIGHT)})

    assert resolve_markers(state) == WatchlistState(tickers={}, markers={})
