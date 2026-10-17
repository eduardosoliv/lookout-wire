import pytest

from lookout_wire.core import state
from lookout_wire.core.state import StateFileError, read_json, write_json


def test_document_round_trips(tmp_path):
    path = tmp_path / "state" / "doc.json"

    write_json(path, {"a": 1, "b": [1.5, "x"]})

    assert read_json(path) == {"a": 1, "b": [1.5, "x"]}


def test_missing_file_reads_as_none(tmp_path):
    assert read_json(tmp_path / "absent.json") is None


def test_failed_rename_leaves_previous_file_intact(tmp_path, monkeypatch):
    path = tmp_path / "doc.json"
    write_json(path, {"version": 1})

    def fail(*args):
        raise OSError("disk gone")

    monkeypatch.setattr(state.os, "replace", fail)
    with pytest.raises(OSError, match="disk gone"):
        write_json(path, {"version": 2})
    monkeypatch.undo()

    assert read_json(path) == {"version": 1}


def test_loader_removes_a_leftover_temp_file(tmp_path):
    path = tmp_path / "doc.json"
    write_json(path, {"version": 1})
    leftover = state.temp_path(path)
    leftover.write_text("half written")

    assert read_json(path) == {"version": 1}
    assert not leftover.exists()


def test_corrupt_document_raises_an_error_naming_the_path(tmp_path):
    path = tmp_path / "doc.json"
    path.write_text("{not json")

    with pytest.raises(StateFileError, match="doc.json"):
        read_json(path)
