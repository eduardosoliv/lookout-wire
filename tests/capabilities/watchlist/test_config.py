from lookout_wire.capabilities.watchlist.config import Entry, WatchlistFile

VALID = """
tickers:
  - symbol: aapl
    dip: 150
  - symbol: NVDA
    dip: 150
    buy: 130
"""


def write(tmp_path, text):
    path = tmp_path / "watchlist.yaml"
    path.write_text(text)
    return path


def test_valid_file_parses_with_upper_cased_symbols(tmp_path):
    result = WatchlistFile(write(tmp_path, VALID)).read()

    assert result.entries == [Entry("AAPL", dip=150), Entry("NVDA", dip=150, buy=130)]
    assert result.errors == []
    assert result.invalid_symbols == set()


def test_buy_above_dip_is_reported_and_other_tickers_still_load(tmp_path):
    text = """
tickers:
  - symbol: AAPL
    dip: 150
    buy: 160
  - symbol: MSFT
    dip: 380
"""
    result = WatchlistFile(write(tmp_path, text)).read()

    assert result.entries == [Entry("MSFT", dip=380)]
    assert result.invalid_symbols == {"AAPL"}
    assert len(result.errors) == 1
    assert "AAPL" in result.errors[0]


def test_unparseable_file_returns_the_last_good_watchlist(tmp_path):
    path = write(tmp_path, VALID)
    watchlist = WatchlistFile(path)
    good = watchlist.read()

    path.write_text("tickers: [unclosed")
    result = watchlist.read()

    assert result.entries == good.entries
    assert len(result.errors) == 1


def test_wrong_shape_counts_as_parse_error(tmp_path):
    path = write(tmp_path, VALID)
    watchlist = WatchlistFile(path)
    good = watchlist.read()

    path.write_text("- just a list")
    result = watchlist.read()

    assert result.entries == good.entries
    assert len(result.errors) == 1


def test_missing_file_with_no_good_copy_returns_nothing(tmp_path):
    result = WatchlistFile(tmp_path / "absent.yaml").read()

    assert result.entries == []
    assert len(result.errors) == 1


def test_padded_symbol_is_stripped(tmp_path):
    result = WatchlistFile(write(tmp_path, "tickers:\n  - symbol: ' aapl '\n    dip: 150\n")).read()

    assert result.entries == [Entry("AAPL", dip=150)]


def test_optional_company_name_is_parsed(tmp_path):
    path = tmp_path / "watchlist.yaml"
    path.write_text("tickers:\n  - symbol: AVGO\n    name: Broadcom Inc.\n    dip: 370\n")

    result = WatchlistFile(path).read()

    assert result.entries == [Entry("AVGO", dip=370, name="Broadcom Inc.")]
