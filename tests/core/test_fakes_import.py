from tests.fakes import FakeClock


def test_shared_fakes_are_importable_from_any_test_folder():
    assert FakeClock is not None
