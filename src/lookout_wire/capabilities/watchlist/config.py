"""The owner's hand-edited YAML watchlist."""

from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError


@dataclass(frozen=True)
class Entry:
    symbol: str
    dip: float
    buy: float | None = None


@dataclass(frozen=True)
class ParseResult:
    entries: list[Entry]
    errors: list[str]
    invalid_symbols: set[str]
    parsed: bool = True


class _Ticker(BaseModel):
    symbol: str
    dip: float
    buy: float | None = None


class _Document(BaseModel):
    tickers: list[_Ticker]


class WatchlistFile:
    """Re-read every cycle. A file that fails to parse falls back to the last good entries."""

    def __init__(self, path: Path) -> None:
        self._path = path
        self._last_good: list[Entry] = []

    def read(self) -> ParseResult:
        try:
            document = self._load()
        except (OSError, yaml.YAMLError, ValidationError) as error:
            message = f"Watchlist {self._path} could not be parsed: {_describe(error)}"
            return ParseResult(
                entries=list(self._last_good), errors=[message], invalid_symbols=set(), parsed=False
            )
        result = _validate(document)
        self._last_good = result.entries
        return result

    def _load(self) -> _Document:
        raw = yaml.safe_load(self._path.read_text(encoding="utf-8"))
        return _Document.model_validate(raw)


def _validate(document: _Document) -> ParseResult:
    entries, errors, invalid = [], [], set()
    for ticker in document.tickers:
        entry = Entry(symbol=ticker.symbol.strip().upper(), dip=ticker.dip, buy=ticker.buy)
        if entry.buy is not None and entry.buy >= entry.dip:
            errors.append(f"{entry.symbol}: buy {entry.buy} must be below dip {entry.dip}")
            invalid.add(entry.symbol)
            continue
        entries.append(entry)
    return ParseResult(entries=entries, errors=errors, invalid_symbols=invalid)


def _describe(error: Exception) -> str:
    if not isinstance(error, ValidationError):
        return str(error)
    return "; ".join(_describe_field(item) for item in error.errors())


def _describe_field(item) -> str:
    location = item["loc"]
    if len(location) >= 2 and location[0] == "tickers" and isinstance(location[1], int):
        where = f"ticker {location[1] + 1}"
        if len(location) > 2:
            where += f", {location[2]}"
        return f"{where}: {item['msg']}"
    return f"{'.'.join(str(part) for part in location) or 'file'}: {item['msg']}"
