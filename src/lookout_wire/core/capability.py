"""The contract every capability implements."""

from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:
    from lookout_wire.core.ports import Ports
    from lookout_wire.core.settings import Settings


class FatalError(Exception):
    """Stops the process. Raised when continuing cannot help, such as a revoked token."""


class Capability(Protocol):
    """Called once per loop tick. Decides for itself whether there is work to do."""

    name: str

    def run(self, now: datetime) -> None: ...


type CapabilityFactory = Callable[["Settings", "Ports"], Capability]
