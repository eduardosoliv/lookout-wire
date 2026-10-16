from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
import pytest

from lookout_wire import checks
from lookout_wire.checks import (
    check_alpaca,
    check_watchlist,
    check_whatsapp_id,
    check_whatsapp_send,
    render,
    whatsapp_client,
)
from lookout_wire.core.ports import CalendarError, PriceError, Session, Trade
from lookout_wire.core.settings import SettingsError
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


ME = "user:280521678532667"
WA_NOW = datetime(2026, 10, 16, 22, 0, tzinfo=UTC)


def updates(messages=(), statuses=()):
    value = {
        "messaging_product": "whatsapp",
        "contacts": [{"wa_id": ME}],
        "messages": list(messages),
        "statuses": list(statuses),
    }
    return {
        "object": "whatsapp_agent_platform",
        "entry": [{"id": "1", "changes": [{"field": "messages", "value": value}]}],
        "next_offset": 3,
    }


def client(handler):
    return whatsapp_client(
        "token", "https://api.whatsapp.test/agent/v1", transport=httpx.MockTransport(handler)
    )


def at(when: datetime) -> str:
    return str(int(when.timestamp()))


def test_id_check_prints_the_id_and_says_the_24_hour_check_is_ready():
    last = datetime(2026, 10, 15, 18, 0, tzinfo=UTC)
    body = updates(messages=[{"from": ME, "id": "wamid.1", "timestamp": at(last), "type": "text"}])

    result = check_whatsapp_id(client(lambda request: httpx.Response(200, json=body)), WA_NOW)
    text = render([result])

    assert result.ok
    assert "WhatsApp agent: OK" in text
    assert f"Your id: {ME}" in text
    assert "Last message from you: 2026-10-15 14:00 New York time" in text
    assert f"Ready for the 24-hour check: just whatsapp-send {ME}" in text


def test_id_check_says_when_the_24_hour_check_can_run():
    last = datetime(2026, 10, 16, 20, 0, tzinfo=UTC)
    body = updates(messages=[{"from": ME, "id": "wamid.1", "timestamp": at(last), "type": "text"}])

    text = render(
        [check_whatsapp_id(client(lambda request: httpx.Response(200, json=body)), WA_NOW)]
    )

    assert "Wait until 2026-10-17 16:00 New York time" in text


def test_id_check_finds_the_id_from_receipts_when_no_message_is_kept():
    body = updates(
        statuses=[
            {"id": "wamid.2", "status": "delivered", "recipient_id": ME, "timestamp": at(WA_NOW)}
        ]
    )

    text = render(
        [check_whatsapp_id(client(lambda request: httpx.Response(200, json=body)), WA_NOW)]
    )

    assert f"Your id: {ME}" in text
    assert "No message from you in the last 30 days" in text


def test_id_check_asks_for_a_message_when_no_id_is_known():
    body = updates()
    body["entry"][0]["changes"][0]["value"]["contacts"] = []

    result = check_whatsapp_id(client(lambda request: httpx.Response(200, json=body)), WA_NOW)

    assert not result.ok
    assert "Send any message to your agent in WhatsApp" in render([result])


def test_id_check_with_a_rejected_token_is_not_ok():
    error = {"error": {"code": 190, "message": "Malformed access token"}}

    result = check_whatsapp_id(client(lambda request: httpx.Response(401, json=error)), WA_NOW)
    text = render([result])

    assert not result.ok
    assert "HTTP 401" in text
    assert "Malformed access token" in text


def test_send_check_reports_the_message_id():
    sent = []

    def handle(request):
        sent.append(request.content)
        return httpx.Response(200, json={"messages": [{"id": "wamid.ABC"}]})

    result = check_whatsapp_send(client(handle), ME, "hello")

    assert result.ok
    assert "WhatsApp send: OK" in render([result])
    assert "wamid.ABC" in render([result])
    assert b'"to":"user:280521678532667"' in sent[0].replace(b" ", b"")
    assert b'"body":"hello"' in sent[0].replace(b" ", b"")


def test_send_check_shows_the_whatsapp_error():
    error = {"error": {"code": 131005, "message": "Recipient is not the agent's creator"}}

    result = check_whatsapp_send(
        client(lambda request: httpx.Response(403, json=error)), "user:1", "hello"
    )
    text = render([result])

    assert not result.ok
    assert "HTTP 403" in text
    assert "code 131005" in text
    assert "Recipient is not the agent's creator" in text


def test_send_check_rejects_an_id_without_the_user_prefix():
    result = check_whatsapp_send(
        client(lambda request: httpx.Response(200)), "message=hello", "hello"
    )

    assert not result.ok
    assert "should look like user:123" in render([result])


def test_send_check_names_a_network_failure():
    def handle(request):
        raise httpx.ConnectError("refused")

    result = check_whatsapp_send(client(handle), ME, "hello")

    assert not result.ok
    assert "ConnectError" in render([result])


def write(tmp_path, text):
    path = tmp_path / "watchlist.yaml"
    path.write_text(text)
    return path


def test_watchlist_check_counts_tickers(tmp_path):
    path = write(
        tmp_path,
        "tickers:\n  - symbol: AAPL\n    dip: 235\n    buy: 200\n  - symbol: MSFT\n    dip: 440\n",
    )

    result = check_watchlist(path)
    text = render([result])

    assert result.ok
    assert "Watchlist: OK" in text
    assert "2 tickers: 1 with a Dip & Buy level, 1 with a Dip level only" in text


def test_watchlist_check_reports_buy_not_below_dip(tmp_path):
    path = write(tmp_path, "tickers:\n  - symbol: AAPL\n    dip: 200\n    buy: 235\n")

    result = check_watchlist(path)

    assert not result.ok
    assert "AAPL: buy 235.0 must be below dip 200.0" in render([result])


def test_watchlist_check_reports_a_symbol_listed_twice(tmp_path):
    path = write(
        tmp_path, "tickers:\n  - symbol: AAPL\n    dip: 235\n  - symbol: aapl\n    dip: 230\n"
    )

    result = check_watchlist(path)

    assert not result.ok
    assert "AAPL is listed 2 times; only the last entry is used" in render([result])


def test_watchlist_check_reports_a_broken_file(tmp_path):
    path = write(tmp_path, "tickers: [ : broken")

    result = check_watchlist(path)

    assert not result.ok
    assert "could not be parsed" in render([result])


def test_watchlist_check_reports_a_missing_file(tmp_path):
    result = check_watchlist(tmp_path / "missing.yaml")

    assert not result.ok
    assert "cp watchlist.example.yaml" in render([result])


def test_watchlist_check_names_the_ticker_and_field_with_a_bad_value(tmp_path):
    path = write(tmp_path, "tickers:\n  - symbol: AAPL\n    dip: abc\n")

    text = render([check_watchlist(path)])

    assert "ticker 1, dip: Input should be a valid number" in text
    assert "_Document" not in text
    assert "errors.pydantic.dev" not in text


def no_settings():
    raise SettingsError("Invalid settings: LOOKOUT_WHATSAPP_TOKEN: Field required")


def test_watchlist_command_works_without_keys(tmp_path, monkeypatch, capsys):
    path = write(tmp_path, "tickers:\n  - symbol: AAPL\n    dip: 235\n")
    monkeypatch.setattr(checks, "load_settings", no_settings)

    assert checks.main(["watchlist", str(path)]) == 0
    assert "Watchlist: OK" in capsys.readouterr().out


def test_watchlist_command_falls_back_to_the_default_path_without_keys(
    tmp_path, monkeypatch, capsys
):
    write(tmp_path, "tickers:\n  - symbol: AAPL\n    dip: 235\n")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(checks, "load_settings", no_settings)

    assert checks.main(["watchlist"]) == 0
    assert "Watchlist: OK" in capsys.readouterr().out


def test_live_command_without_settings_is_not_ok(monkeypatch, capsys):
    monkeypatch.setattr(checks, "load_settings", no_settings)

    assert checks.main(["whatsapp-id"]) == 1
    out = capsys.readouterr().out
    assert "Settings: NOT OK" in out
    assert "LOOKOUT_WHATSAPP_TOKEN" in out


@pytest.mark.parametrize("argv", [["nonsense"], ["whatsapp-send"]])
def test_unknown_or_incomplete_command_prints_usage(monkeypatch, capsys, argv):
    from lookout_wire.core.settings import Settings

    fake = Settings(alpaca_key_id="k", alpaca_secret_key="s", whatsapp_token="t", _env_file=None)
    monkeypatch.setattr(checks, "load_settings", lambda: fake)

    assert checks.main(argv) == 1
    out = capsys.readouterr().out
    assert "Usage: NOT OK" in out
    assert "whatsapp-send ID [TEXT]" in out
