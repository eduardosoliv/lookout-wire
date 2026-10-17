# Coding standards

The rules every change in lookout-wire follows. The owner wants code that is simple to read and
maintain: a reader should understand a function on the first pass, without the plan open.

`AGENTS.md` carries a short summary of these rules; this file is the full version. Change both
together.

Ruff and `just check` enforce formatting, imports, and some lint. Everything else here is checked
in review: before each commit, the `coding-standards-reviewer` agent reviews the staged change
against this file (see `AGENTS.md`).

Limits below are review triggers, not targets. Code under a limit can still be too complex, and a
reason stated in the change can justify going over one.

## Readability

### Keep cognitive complexity low

A function should be explainable in one sentence. Each `if`, `elif`, loop, `try`, boolean chain,
and nested function adds to what the reader must hold at once.

- Use early returns and guard clauses instead of `else` after a `return` or `raise`.
- At most two levels of nesting inside a function body. A third level is a finding: extract the
  inner block into a named function.
- More than five decision points in one function is a finding: split it.
- Prefer one condition per `if`. Name a compound condition with a variable or a small function.

```python
# Avoid (an illustration, not real code)
def notice(self, failures):
    if failures > 0:
        if failures >= HEALTH_THRESHOLD:
            if not self._delivered:
                return FAILURE_NOTICE
    return None


# Prefer
def notice(self, failures):
    if failures < HEALTH_THRESHOLD or self._delivered:
        return None
    return FAILURE_NOTICE
```

### Keep functions and classes short

- A function body should fit in about 20 lines. Over 30 lines is a finding unless it is an entry
  point that only wires parts together, such as `main()`.
- Split a function before it needs a comment to explain a block inside it. The new function's name
  replaces the comment.
- A class has one reason to change. Over about 200 lines, or a group of methods that share data the
  rest of the class does not use, is a finding: look for the second class inside it.

### Use few parameters

- At most four parameters, not counting `self`. More is a finding: group values that travel
  together into a frozen dataclass, or pass the object that owns them.
- Adapter constructors that take configuration may exceed this when every caller passes arguments
  by keyword, as `WhatsAppMessenger` does.
- No boolean flag parameters that switch a function between two behaviors. Write two functions.

### Prefer plain code

- Plain, explicit code over clever code: no metaprogramming, no dense one-liners, no comprehension
  with more than one `for` or a nested condition.
- No abstraction without a current use. A base class, protocol, or helper needs at least one real
  caller today; a port in `core/ports.py` needs a real adapter.

## Structure

### No duplicated logic

- The same rule, calculation, or constant in two places is a finding. Put it in one function or
  constant and import it.
- Two code paths that differ only in a value should be one function taking that value.
- Tests may repeat setup when it keeps each test readable on its own; extract a helper or fixture
  once three or more tests share it.

### No feature envy

A method that mostly reads another object's fields belongs on that object, or in the module that
owns that data.

- If a function takes an object and uses three or more of its attributes to compute something about
  it, move the computation next to the data.
- Respect the layers: `core` never imports a capability or adapter, adapters never import a
  capability, and a capability reaches the outside world only through the ports in `Ports`.

## Values and state

### No magic numbers or strings

- A literal that carries meaning gets a named module-level constant in UPPER_CASE: limits,
  timeouts, retry counts, status codes, vendor error codes, and user-facing text.
  `NOT_ACCEPTED_CODE = 131016`, not `if code == 131016`.
- Durations compared with or added to datetimes are `timedelta` constants. Timeouts and sleep
  arguments are float seconds, still in a named constant.
- Allowed inline: `0`, `1`, `-1` as indexes or increments, empty strings and collections, and
  values whose meaning is obvious from one line, such as `round(x, 2)` for cents.
- A value the owner should be able to change belongs in `core/settings.py`, not in a constant.

### No mutable shared state

- Module-level names are constants: never reassigned or mutated after import. Use tuples and
  frozensets for collections. A module-level dict, such as `CAPABILITIES`, is allowed only when
  nothing mutates it at runtime.
- Value types are `@dataclass(frozen=True)`. Produce a changed copy with `dataclasses.replace`.
  A result that one function fills in as it goes, such as `ScanReport`, may be mutable.
- State lives in an object that owns it, such as a capability or adapter instance, and is passed
  explicitly. No globals, singletons, or class-level mutable attributes.
- Time, sleep, randomness, and I/O are injected (a clock, a sleep function, a transport), so tests
  control them.
- State files are written only through `core.state.write_json`.

## Naming

Use one name for one idea, everywhere: code, logs, messages, and tests.

| Idea | Name in code | Name in text the owner reads |
|---|---|---|
| A stock in the watchlist | `symbol` for the string, `Entry` for its configured line, `TickerState` for its saved state | ticker |
| The two price levels | `dip`, `buy`, `Kind.DIP`, `Kind.BUY` | Dip, Buy |
| The lowest price already notified | `baseline` | last notified low |
| A US market session date | `trading_day` | trading day |

- `ticker` appears in code only where it names a per-stock record, such as `TickerState`, or
  the `tickers:` key of `watchlist.yaml`. The stock's identifier is always `symbol`.
- Functions are verbs (`load_state`, `record_sent`), values and classes are nouns, booleans read as
  questions (`is_open`, `ready`).
- No abbreviations a reader must decode (`msg`, `cfg`, `px`), except well-known ones (`id`, `url`,
  `http`).
- Private helpers start with `_`. Constants are UPPER_CASE, classes PascalCase, everything else
  snake_case.
- Follow the existing name for an idea before inventing a new one; grep first.

## Dead code

- No unused functions, classes, parameters, constants, imports, or branches that cannot run.
- No commented-out code. Git keeps history.
- No code kept "for later". Delete it; add it back when there is a caller.
- When a change makes something unused, the same change removes it.

## Errors

- Catch the narrowest exception that can happen, at the boundary where it can be handled. Adapters
  turn vendor errors into the port's error types (`PriceError`, `CalendarError`) or a result
  (`SendOutcome`).
- Never swallow an exception silently. Log it with context, or turn it into a result the caller
  handles.
- Raise `FatalError` only for problems a restart cannot fix, such as an invalid token.
- Error and log text says what failed and what to do, and never includes secrets, request headers,
  or URLs with query strings.

## Comments

- Comments explain a reason the code cannot show, such as a vendor quirk or a safety rule. They
  never restate what the code does.
- Docstrings are one line saying what the function or class is for, when the name alone is not
  enough.
- Never put plan codes (AE, R, KTD, F, U numbers) or ticket IDs in code, comments, or test names.

## Tests

- Tests must never open a network connection; `tests/conftest.py` enforces this. Use
  `httpx.MockTransport` for HTTP and injected stubs for SDKs.
- Never sleep in tests. Inject a clock (`tests/fakes.FakeClock`) or a sleep function.
- Use the fakes in `tests/fakes.py` for ports. Do not mock internals.
- Write the test first and watch it fail before writing the code.
- Assert on what the code returns, stores, or sends, not on calls between internal parts.
- One behavior per test, arranged as setup, action, then assertions, separated by blank lines.
- Name each test for the behavior it checks, in plain words a reader understands without the plan:
  `test_trade_from_before_the_market_open_is_ignored`, not `test_covers_ae17_...`.
- Each acceptance example in the plan (AE1 to AE20) still has a test; find it by its behavior.
- New code is covered by tests; check with `just coverage`.

## Formatting

Ruff settings live in `pyproject.toml` (line length 100). Run `just fix` rather than formatting by
hand. `just check` must pass before every commit.
