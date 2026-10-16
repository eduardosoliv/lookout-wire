import threading

from lookout_wire import main
from lookout_wire.adapters.whatsapp import WhatsAppMessenger
from lookout_wire.core.settings import Settings


def settings(tmp_path):
    (tmp_path / "watchlist.yaml").write_text("tickers:\n  - symbol: AAPL\n    dip: 150\n")
    return Settings(
        alpaca_key_id="k",
        alpaca_secret_key="s",
        whatsapp_token="t",
        watchlist_path=tmp_path / "watchlist.yaml",
        state_dir=tmp_path / "state",
        _env_file=None,
    )


def test_pairing_runs_before_the_capability_is_built(tmp_path, monkeypatch):
    events = []
    monkeypatch.setattr(WhatsAppMessenger, "ensure_paired", lambda self: events.append("paired"))
    original = main.CAPABILITIES["watchlist"]

    def build(settings, ports):
        events.append("built")
        return original(settings, ports)

    monkeypatch.setitem(main.CAPABILITIES, "watchlist", build)

    capabilities = main.build_capabilities(settings(tmp_path), threading.Event())

    assert events == ["paired", "built"]
    assert [c.name for c in capabilities] == ["watchlist"]


def test_missing_settings_exit_with_code_2(monkeypatch):
    import pytest

    from lookout_wire.core.settings import SettingsError

    def fail():
        raise SettingsError("Invalid settings: LOOKOUT_WHATSAPP_TOKEN: Field required")

    monkeypatch.setattr(main, "load_settings", fail)
    monkeypatch.setattr(main, "configure_logging", lambda: None)

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 2


def test_fatal_error_while_building_exits_with_code_1(tmp_path, monkeypatch):
    import pytest

    from lookout_wire.core.capability import FatalError

    def fatal(settings, stop):
        raise FatalError("token revoked")

    monkeypatch.setattr(main, "load_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "install_signal_handlers", lambda stop: None)
    monkeypatch.setattr(main, "build_capabilities", fatal)

    with pytest.raises(SystemExit) as exit_info:
        main.main()

    assert exit_info.value.code == 1


def test_scan_command_runs_one_scan_and_prints_the_summary(tmp_path, monkeypatch, capsys):
    from datetime import UTC, datetime

    from lookout_wire.core.ports import Ports, Trade
    from tests.fakes import FakeCalendar, FakeClock, FakeExplainer, FakeMessenger, FakePriceSource

    now = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)
    messenger = FakeMessenger()
    prices = FakePriceSource({"AAPL": Trade(149.0, now)})
    ports = Ports(
        prices, FakeCalendar(), messenger, FakeExplainer(), tmp_path / "state", FakeClock(now)
    )
    monkeypatch.setattr(main, "load_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "build_ports", lambda settings, stop: ports)

    main.main(["scan"])

    assert len(messenger.sent) == 1
    assert "Sent (1): AAPL" in capsys.readouterr().out


def test_fatal_error_while_running_exits_with_code_1(tmp_path, monkeypatch):
    import pytest

    from lookout_wire.core.capability import FatalError

    class Revoked:
        name = "revoked"

        def run(self, now):
            raise FatalError("token revoked")

    monkeypatch.setattr(main, "load_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "install_signal_handlers", lambda stop: None)
    monkeypatch.setattr(main, "build_capabilities", lambda settings, stop: [Revoked()])

    with pytest.raises(SystemExit) as exit_info:
        main.main([])

    assert exit_info.value.code == 1


def test_scan_without_prices_exits_with_code_1(tmp_path, monkeypatch):
    import pytest

    from lookout_wire.core.ports import PriceError

    def no_prices(settings, stop):
        raise PriceError("Alpaca returned HTTP 401")

    monkeypatch.setattr(main, "load_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "build_ports", no_prices)

    with pytest.raises(SystemExit) as exit_info:
        main.main(["scan"])

    assert exit_info.value.code == 1


def test_scan_with_a_failed_send_exits_with_code_1(tmp_path, monkeypatch, capsys):
    from datetime import UTC, datetime

    import pytest

    from lookout_wire.core.ports import Ports, SendOutcome, SendStatus, Trade
    from tests.fakes import FakeCalendar, FakeClock, FakeExplainer, FakeMessenger, FakePriceSource

    now = datetime(2026, 10, 15, 22, 30, tzinfo=UTC)
    messenger = FakeMessenger()
    messenger.outcomes.extend([SendOutcome(SendStatus.NOT_ACCEPTED)] * 5)
    prices = FakePriceSource({"AAPL": Trade(149.0, now)})
    ports = Ports(
        prices, FakeCalendar(), messenger, FakeExplainer(), tmp_path / "state", FakeClock(now)
    )
    monkeypatch.setattr(main, "load_settings", lambda: settings(tmp_path))
    monkeypatch.setattr(main, "configure_logging", lambda: None)
    monkeypatch.setattr(main, "build_ports", lambda settings, stop: ports)

    with pytest.raises(SystemExit) as exit_info:
        main.main(["scan"])

    assert exit_info.value.code == 1
    assert "Could not send (1): AAPL" in capsys.readouterr().out
