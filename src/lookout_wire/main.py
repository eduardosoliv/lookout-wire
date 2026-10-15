"""Entry point: load settings, build enabled capabilities, run the loop until stopped."""

import logging
import threading

from lookout_wire.core.capability import Capability, CapabilityFactory, FatalError
from lookout_wire.core.log import configure_logging
from lookout_wire.core.loop import Loop, install_signal_handlers
from lookout_wire.core.ports import Ports
from lookout_wire.core.registry import Registry, UnknownCapabilityError
from lookout_wire.core.settings import Settings, SettingsError, load_settings

log = logging.getLogger("lookout_wire")

CAPABILITIES: dict[str, CapabilityFactory] = {}


def main() -> None:
    configure_logging()
    try:
        settings = load_settings()
    except SettingsError as error:
        log.error("%s", error)
        raise SystemExit(2) from None

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


def build_capabilities(settings: Settings, stop: threading.Event) -> list[Capability]:
    registry = Registry(CAPABILITIES)
    registry.validate(settings.enabled_capabilities)
    if not settings.enabled_capabilities:
        return []
    ports = build_ports(settings, stop)
    return registry.build(settings.enabled_capabilities, settings, ports)


def build_ports(settings: Settings, stop: threading.Event) -> Ports:
    raise FatalError("No adapters are wired yet")
