# AGENTS.md

Guidance for coding agents working in this repository. It overrides any `AGENTS.md` in a parent
folder: this is a standalone Python project, not the Rails application those files describe.

## What this is

lookout-wire is a personal WhatsApp agent for financial alerts. One always-on Python process on
the owner's laptop polls Alpaca prices every 2 minutes (configurable) during US market hours,
compares them to a hand-edited `watchlist.yaml`, and sends WhatsApp messages through the WhatsApp
Agent Platform, optionally with a Gemini search-grounded explanation. The watchlist is the first
capability; more will be added as separate packages.

The plan that defines the product rules and design decisions is
`docs/plans/2026-10-15-1635-feat-whatsapp-watchlist-alerts-plan.md`. Read its Product Contract
before changing alert behavior. Decisions marked `session-settled` there were made by the owner,
and so were the changes listed in its "Product Contract preservation" note. Do not change them
without asking.

The README's "External services" section records how WhatsApp, Alpaca, and Gemini behave in
practice, including quirks that tests cannot show. Read it before changing an adapter.

## Stack and commands

Python 3.14, managed with uv. Tasks run through `just`:

| Command | Use |
|---|---|
| `just check` | Lint, format check, and all tests. Must pass before every commit; the pre-commit hook in `.githooks/` enforces it |
| `just test -k <pattern>` | Run a subset of tests |
| `just coverage` | Coverage report with uncovered lines; check new code is covered |
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

## Coding standards

The owner wants code that is simple to read and maintain. The rules that matter most while writing
code are below. `coding-standards.md` has every rule with its limits and examples; read it before a
larger change, and the pre-commit reviewer checks against it.

- **Low complexity:** early returns instead of nested conditionals; at most two levels of nesting
  and five decision points per function.
- **Short code:** a function body around 20 lines; over 30 is a finding unless it is an entry
  point that only wires parts together. A class has one reason to change; over about 200 lines is a
  finding. Split a function before it needs a comment to explain a block.
- **Few parameters:** at most four; group values that travel together into a frozen dataclass.
  Adapter constructors may take more when every caller passes them by keyword. No boolean flags
  that switch between two behaviors.
- **Plain code:** explicit over clever; no abstraction without a current caller.
- **No duplication:** one rule, calculation, or constant lives in one place.
- **No feature envy:** logic sits next to the data it uses; `core` never imports adapters or
  capabilities.
- **No magic values:** meaningful literals become named UPPER_CASE constants; owner-tunable values
  go in `core/settings.py`.
- **No mutable shared state:** frozen dataclasses, constants never mutated, and time, sleep, and
  I/O injected.
- **Consistent names:** `symbol` in code, "ticker" in owner-facing text; follow the existing name
  for an idea before inventing one.
- **No dead code:** no unused code, commented-out code, or code kept for later.
- **Comments:** only for a reason the code cannot show, such as a vendor quirk.
- **Tests:** no network and no sleeping; use `tests/fakes.py`; write the test first; assert on
  results, not internal calls; name tests for the behavior they check, never with plan codes like
  AE17.
- **Formatting and lint:** `just fix`; ruff settings live in `pyproject.toml` (line length 100)
  and enforce most of the limits above. A `# noqa` needs a reason after `--`.

## Messages and timing

The owner chose the alert wording and pacing; change them only when asked.

- Format (KTD11 in the plan): `*AVGO* (Broadcom Inc.) $344.29`, then one line for the deepest
  level that fired, such as `🔥 Buy 1.63% below $350.00`, then the explanation, then
  `Sources: reuters.com, cnbc.com`. `*...*` is WhatsApp bold.
- WhatsApp text cannot attach a link to a word, so sources are names, never URLs.
- A message must stay under 4096 characters; the explanation is cut first.
- The loop runs every 120 seconds by default, and sends are spaced 10 seconds apart.

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
- The owner's permission settings block agents from reading any `.env*` file, including
  `.env.example`. Ask the owner to make changes to `.env.example`.
- `watchlist.yaml` and `state/` are the owner's private data and are gitignored. Do not commit
  them.

## Git

Before every commit:

1. Run `just check` (the pre-commit hook runs it again) and stage the files by path, never with
   `git add .`.
2. Run the `coding-standards-reviewer` subagent (`.claude/agents/`, runs on Sonnet) on the staged
   change.
3. Fix every must-fix finding, stage again, and rerun the reviewer until it returns `PASS`. A
   consider finding may be declined; give the reason in your report to the owner.

- Commit messages use conventional commits (`feat:`, `fix:`, `docs:`, `chore:`, `refactor:`).
- Never bypass the pre-commit hook with `--no-verify`; fix what it reports.
- Never push or create a remote without the owner asking.
