import pytest

from lookout_wire.core.registry import Registry, UnknownCapabilityError


class Named:
    def __init__(self, name):
        self.name = name

    def run(self, now):
        pass


def test_builds_enabled_capabilities_in_the_order_given():
    registry = Registry({"a": lambda s, p: Named("a"), "b": lambda s, p: Named("b")})

    built = registry.build(["b", "a"], settings=None, ports=None)

    assert [c.name for c in built] == ["b", "a"]


def test_unknown_name_fails_with_a_clear_error():
    registry = Registry({"watchlist": lambda s, p: Named("watchlist")})

    with pytest.raises(UnknownCapabilityError, match="digest"):
        registry.validate(["watchlist", "digest"])
