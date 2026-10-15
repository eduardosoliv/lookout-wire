from datetime import date

from lookout_wire.capabilities.watchlist.message import MAX_LENGTH, build_question, render, title
from lookout_wire.capabilities.watchlist.rules import Alert, AlertKind, Kind
from lookout_wire.core.ports import Explanation

DIP = Alert(Kind.DIP, 150.0, AlertKind.CROSSED, 149.8)
BUY = Alert(Kind.BUY, 130.0, AlertKind.CROSSED, 128.0)


def test_dip_alert_reads_emoji_level_percent_below_and_dollar_level():
    text = render("AAPL", 149.8, [DIP], None)

    assert text == "AAPL $149.80\n⚠️ Dip 0.13% below $150.00"


def test_when_both_levels_fire_only_buy_is_shown():
    text = render("NVDA", 128.0, [DIP, BUY], None)

    assert text == "NVDA $128.00\n🔥 Buy 1.54% below $130.00"


def test_new_low_is_marked():
    new_low = Alert(Kind.DIP, 150.0, AlertKind.NEW_LOW, 147.0)

    text = render("AAPL", 147.0, [new_low], None)

    assert text == "AAPL $147.00\n⚠️ Dip 2.00% below $150.00, new low"


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
    assert text.startswith("AAPL $149.80\n⚠️ Dip 0.13% below $150.00")
    assert text.endswith("https://a.test/1")


def test_question_names_ticker_dollar_price_percentage_and_date():
    question = build_question("AAPL", 149.8, [DIP], date(2026, 10, 15))

    assert "AAPL" in question
    assert "$149.80" in question
    assert "0.13%" in question
    assert "2026-10-15" in question


def test_question_uses_the_shown_level_when_both_fire():
    question = build_question("NVDA", 128.0, [DIP, BUY], date(2026, 10, 15))

    assert "$130.00" in question
    assert "$150.00" not in question


def test_header_shows_company_name_with_ticker_when_known():
    text = render(title("AVGO", "Broadcom Inc."), 344.29, [BUY], None)

    assert text.splitlines()[0] == "Broadcom Inc. (AVGO) $344.29"


def test_header_shows_only_the_ticker_without_a_name():
    assert title("AVGO", None) == "AVGO"


def test_question_names_the_company():
    question = build_question(title("AVGO", "Broadcom Inc."), 344.29, [BUY], date(2026, 10, 15))

    assert "Broadcom Inc. (AVGO)" in question
