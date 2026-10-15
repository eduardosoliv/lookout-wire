"""Entry point: load settings, build enabled capabilities, run the loop until stopped."""

import logging
import sys
import threading
from collections.abc import Sequence

from lookout_wire.adapters.alpaca import AlpacaCalendar, AlpacaPriceSource
from lookout_wire.adapters.gemini import build_explainer
from lookout_wire.adapters.whatsapp import WhatsAppMessenger
from lookout_wire.capabilities.watchlist.capability import build_watchlist
from lookout_wire.capabilities.watchlist.config import WatchlistFile
from lookout_wire.capabilities.watchlist.scan import describe, scan
from lookout_wire.core.capability import Capability, CapabilityFactory, FatalError
from lookout_wire.core.log import configure_logging
from lookout_wire.core.loop import Loop, install_signal_handlers, utc_now
from lookout_wire.core.ports import Ports, PriceError
from lookout_wire.core.registry import Registry, UnknownCapabilityError
from lookout_wire.core.settings import Settings, SettingsError, load_settings

log = logging.getLogger("lookout_wire")

CAPABILITIES: dict[str, CapabilityFactory] = {"watchlist": build_watchlist}


def main(argv: Sequence[str] | None = None) -> None:
    args = sys.argv[1:] if argv is None else list(argv)
    configure_logging()
    try:
        settings = load_settings()
    except SettingsError as error:
        log.error("%s", error)
        raise SystemExit(2) from None
    if args == ["scan"]:
        run_scan(settings)
        return

    stop = threading.Event()
    install_signal_handlers(stop)
    try:
        capabilities = build_capabilities(settings, stop)
    except UnknownCapabilityError as error:
        log.error("%s", error)
        raise SystemExit(2) from None
    except FatalError as error:
        log.error("Stopping: %s", error)
        raise SystemExit(1) from None

    try:
        log.info("Started with capabilities: %s", [c.name for c in capabilities] or "none")
        Loop(capabilities, settings.interval_seconds, stop).run()
    except FatalError as error:
        log.error("Stopping: %s", error)
        raise SystemExit(1) from None
    log.info("Stopped")


def run_scan(settings: Settings) -> None:
    """Messages every ticker below a level right now, then exits. Alert state is untouched."""
    try:
        ports = build_ports(settings, threading.Event())
        report = scan(WatchlistFile(settings.watchlist_path), ports)
    except (FatalError, PriceError) as error:
        log.error("Scan stopped: %s", error)
        raise SystemExit(1) from None
    print(describe(report))
    if report.failed:
        raise SystemExit(1)


def build_capabilities(settings: Settings, stop: threading.Event) -> list[Capability]:
    registry = Registry(CAPABILITIES)
    registry.validate(settings.enabled_capabilities)
    if not settings.enabled_capabilities:
        return []
    ports = build_ports(settings, stop)
    return registry.build(settings.enabled_capabilities, settings, ports)


def build_ports(settings: Settings, stop: threading.Event) -> Ports:
    """Builds every adapter and pairs with WhatsApp before any price is evaluated."""
    messenger = WhatsAppMessenger(
        token=settings.whatsapp_token.get_secret_value(),
        base_url=settings.whatsapp_base_url,
        state_path=settings.state_dir / "whatsapp.json",
        stop=stop,
        connect_timeout=settings.send_connect_timeout_seconds,
        read_timeout=settings.send_read_timeout_seconds,
        poll_read_timeout=settings.poll_read_timeout_seconds,
    )
    messenger.ensure_paired()
    key_id = settings.alpaca_key_id.get_secret_value()
    secret = settings.alpaca_secret_key.get_secret_value()
    timeout = settings.price_timeout_seconds
    return Ports(
        prices=AlpacaPriceSource(key_id, secret, settings.alpaca_data_url, timeout),
        calendar=AlpacaCalendar(key_id, secret, settings.alpaca_trading_url, timeout),
        messenger=messenger,
        explainer=build_explainer(
            _gemini_key(settings), settings.gemini_model, settings.explain_timeout_seconds
        ),
        state_dir=settings.state_dir,
        clock=utc_now,
    )


def _gemini_key(settings: Settings) -> str | None:
    if settings.gemini_api_key is None:
        return None
    return settings.gemini_api_key.get_secret_value()
