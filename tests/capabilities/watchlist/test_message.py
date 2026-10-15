from datetime import date

from lookout_wire.capabilities.watchlist.message import MAX_LENGTH, build_question, render
from lookout_wire.capabilities.watchlist.rules import Alert, AlertKind, Kind
from lookout_wire.core.ports import Explanation

DIP = Alert(Kind.DIP, 150.0, AlertKind.CROSSED, 149.8)
BUY = Alert(Kind.BUY, 130.0, AlertKind.NEW_LOW, 128.0)


def test_header_names_ticker_price_level_and_percentage_below():
    text = render("AAPL", 149.8, [DIP], None)

    assert text == "AAPL 149.80\nDip 150.00 crossed, 0.13% below"


def test_both_levels_get_their_own_line_with_kind_label():
    text = render("NVDA", 128.0, [DIP, BUY], None)

    assert "Dip 150.00 crossed" in text
    assert "Buy 130.00 new low, 1.54% below" in text


def test_explanation_and_links_follow_the_header():
    explanation = Explanation(
        "Shares fell after earnings.", ("https://a.test/1", "https://b.test/2")
    )

    text = render("AAPL", 149.8, [DIP], explanation)

    assert text.endswith("Shares fell after earnings.\n\nhttps://a.test/1\nhttps://b.test/2")


def test_long_explanation_is_cut_so_the_message_fits_and_header_survives():
    explanation = Explanation("x" * 5000, ("https://a.test/1",))

    text = render("AAPL", 149.8, [DIP], explanation)

    assert len(text) <= MAX_LENGTH
    assert text.startswith("AAPL 149.80\nDip 150.00 crossed")
    assert text.endswith("https://a.test/1")


def test_question_names_ticker_percentage_and_date():
    question = build_question("AAPL", 149.8, [DIP], date(2026, 10, 15))

    assert "AAPL" in question
    assert "0.13%" in question
    assert "2026-10-15" in question
