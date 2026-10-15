# AGENTS.md

Guidance for coding agents working in this repository. It overrides any `AGENTS.md` in a parent
folder: this is a standalone Python project, not the Rails application those files describe.

## What this is

lookout-wire is a personal WhatsApp agent for financial alerts. One always-on Python process on
the owner's laptop polls Alpaca prices every 2 minutes (configurable) during US market hours, compares them to a
hand-edited `watchlist.yaml`, and sends WhatsApp messages through the WhatsApp Agent Platform,
optionally with a Gemini search-grounded explanation. The watchlist is the first capability; more
will be added as separate packages.

The plan that defines the product rules and design decisions is
`docs/plans/2026-10-15-1635-feat-whatsapp-watchlist-alerts-plan.md`. Read its Product Contract
before changing alert behavior. Decisions marked `session-settled` there were made by the owner;
do not change them without asking.

## Stack and commands

Python 3.14, managed with uv. Tasks run through `just`:

| Command | Use |
|---|---|
| `just check` | Lint, format check, and all tests. Must pass before every commit |
| `just test -k <pattern>` | Run a subset of tests |
| `just fix` | Auto-fix lint, then format |
| `just check-watchlist` | Validate `watchlist.yaml` |
| `just run` / `just scan` | Start the agent / one-shot scan. These use the owner's real accounts |

Never run `just run`, `just scan`, `just check-alpaca`, or the `just whatsapp-*` recipes unless
the owner asks: they use real keys and send real WhatsApp messages.

## Layout

- `src/lookout_wire/core/`: loop, settings, port protocols and value types, registry, atomic
  state-file helper. Core never imports a capability or an adapter.
- `src/lookout_wire/adapters/`: one module per external system (Alpaca, WhatsApp, Gemini), each
  implementing a port from `core/ports.py`. All HTTP goes through httpx with explicit timeouts.
- `src/lookout_wire/capabilities/<name>/`: one package per capability, with tests under
  `tests/capabilities/<name>/`. A capability exposes `name` and `run(now)` and decides for itself
  whether there is work on each tick.
- `src/lookout_wire/main.py`: builds adapters, pairs WhatsApp, registers capabilities in
  `CAPABILITIES`, runs the loop.
- `src/lookout_wire/checks.py`: the live and watchlist checks behind the `just` recipes.
- `tests/fakes.py`: in-memory fakes for every port. Use them; do not mock internals.

To add a capability: create the package, give it a factory `(settings, ports) -> Capability`,
register it in `CAPABILITIES` in `main.py`, and add tests. The main loop should not need changes.

## Code style

The owner wants code that is simple to read and maintain:

- Early returns instead of nested conditionals.
- Short functions and small classes. Split a function before it needs a comment to explain it.
- Plain, explicit code over clever code. No abstraction without a current use.
- Comments only for a reason the code cannot show, such as a vendor quirk.
- Ruff settings live in `pyproject.toml` (line length 100). Run `just fix` rather than
  formatting by hand.

## Tests

- Tests must never open a network connection; `tests/conftest.py` enforces this. Use
  `httpx.MockTransport` for HTTP and injected stubs for SDKs.
- Never sleep in tests. Inject a clock (`tests/fakes.FakeClock`) or a sleep function.
- Write the test first and watch it fail before writing the code.
- Assert on what the code returns, stores, or sends, not on calls between internal parts.
- Acceptance examples in the plan (AE1 to AE20) each have a test; keep the `Covers AE<N>` names.

## Alert-state safety

The product must never send the same alert twice and never lose a pending alert silently. Before
changing `capabilities/watchlist/capability.py`, `state.py`, or `rules.py`, read KTD6 to KTD8 in
the plan. In short:

- State files are written only through `core.state.write_json` (temp file, fsync, rename).
- A per-message marker is written before each send; `in_flight` counts as sent on restart,
  `not_sent` is retried.
- A watchlist that fails to parse must never change saved state.

## Secrets

- Keys live in `.env`, which is gitignored. Never read, print, or commit `.env`, and never log
  settings objects, request headers, or URLs with query strings.
- `watchlist.yaml` and `state/` are the owner's private data and are gitignored. Do not commit
  them.

## Git

- Commit messages use conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).
- Stage files by path; never `git add .`.
- Never push or create a remote without the owner asking.
