"""Maps capability names to factories. The mapping itself lives in main."""

from collections.abc import Mapping, Sequence

from lookout_wire.core.capability import Capability, CapabilityFactory


class UnknownCapabilityError(Exception):
    pass


class Registry:
    def __init__(self, factories: Mapping[str, CapabilityFactory]):
        self._factories = dict(factories)

    def validate(self, names: Sequence[str]) -> None:
        unknown = [name for name in names if name not in self._factories]
        if not unknown:
            return
        known = ", ".join(sorted(self._factories)) or "none"
        raise UnknownCapabilityError(f"Unknown capabilities: {', '.join(unknown)} (known: {known})")

    def build(self, names: Sequence[str], settings, ports) -> list[Capability]:
        self.validate(names)
        return [self._factories[name](settings, ports) for name in names]
