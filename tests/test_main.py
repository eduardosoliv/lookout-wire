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


def test_covers_f1_pairing_runs_before_the_capability_is_built(tmp_path, monkeypatch):
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
