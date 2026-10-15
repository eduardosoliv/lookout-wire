# lookout-wire

A personal WhatsApp agent for financial alerts. One process runs on your laptop, checks the
watchlist every 10 seconds during US market hours, and messages you in WhatsApp when a stock
trades below a price you chose. With a Gemini key it adds a short explanation of why the stock
is down, with source links.

## Setup

Needs [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just) (`brew install just`).

```bash
just install
cp .env.example .env                       # fill in your keys; the Gemini key is optional
cp watchlist.example.yaml watchlist.yaml   # edit your tickers
just check-watchlist
just run
```

On first start the app asks you to send any message to your agent in WhatsApp. It learns your
id from that message, replies with a greeting, and starts watching prices. It remembers the
pairing, so later starts skip this step. Stop it with Ctrl-C.

## Commands

Run `just` to list every recipe.

| Command | What it does |
|---|---|
| `just install` | Install dependencies into `.venv` |
| `just run` | Start the agent |
| `just scan` | Message every ticker below its Dip or Buy level right now, market open or not, then exit |
| `just check-watchlist` | Check `watchlist.yaml` is well formed: valid numbers, Buy below Dip, no symbol twice. Needs no keys |
| `just check` | Lint, format check, and tests: run before every commit |
| `just test` | Run the tests; extra arguments go to pytest, e.g. `just test -k rules` |
| `just fix` | Fix what ruff can fix, then format |
| `just lint` / `just format` | Lint only / format only |
| `just check-alpaca` | Live check 1, below |
| `just whatsapp-id` / `just whatsapp-send ID` | Live check 2, below |

Tests never touch the network. The live-check recipes read your keys from `.env`; nothing else
in the justfile does.

## Watchlist

`watchlist.yaml` lists the tickers to watch. It is gitignored, so your levels stay on your laptop.

```yaml
tickers:
  - symbol: AVGO
    name: "Broadcom Inc."   # optional, shown in messages with the ticker
    dip: 370                # first alert when the price trades below this
    buy: 350                # optional, must be below dip
```

The agent re-reads the file every cycle, so edits apply within 10 seconds without a restart.
Changing a level resets it. A file that fails to parse is ignored until you fix it, and the agent
keeps using the last good version. Run `just check-watchlist` after editing.

## Messages

Each alert names the company and ticker, the price, and the deepest level crossed:

```
Broadcom Inc. (AVGO) $344.29
🔥 Buy 1.63% below $350.00
```

- ⚠️ marks the Dip level and 🔥 the Buy level. When both fire, only Buy is shown.
- A later, deeper fall adds ", new low" to the line.
- With a Gemini key, a short explanation and up to three source links follow.

## Alert rules

- A level alerts the first time the price trades below it.
- After that it alerts again only when the price trades below the lowest price you were already
  told about, at most once per level per trading day.
- When the price recovers to the level, the level re-arms silently.
- Changing a level in `watchlist.yaml` resets it.
- Prices count only during the regular US session (9:30 to 16:00 New York time), from trades
  inside it. Outside the session the agent checks nothing, so a quiet log then is normal.
- If market data fails for six cycles in a row, you get one message saying alerts are paused, and
  another when it recovers.

## Scan now

`just scan` reads the watchlist, fetches the latest trade for every ticker whether the market is
open or not, and sends one message per ticker below its Dip or Buy level, then prints a summary:

```
Sent (2): NVDA, MSFT
Above their levels, nothing sent (43): AAPL, ...
```

It ignores alerts already sent and never changes the agent's saved alert state, so the agent's
own alerts still fire normally. Outside market hours the latest trade can be an after-hours
price. Sends are spaced about 5 seconds apart to respect WhatsApp's limit.

## Live checks

These need your real keys and cannot run in tests.

### 1. Alpaca keys serve prices and the market clock

```bash
just check-alpaca
```

It reports OK or NOT OK for prices (AAPL's last trade from the data host) and for the market
clock (from the trading host), with details under each, and exits non-zero if either fails. If
only the clock fails and you use live keys, set
`LOOKOUT_ALPACA_TRADING_URL=https://api.alpaca.markets` in `.env`.

Result: OK on 2026-10-15. Both the snapshot and the clock returned data.

### 2. WhatsApp delivers a message you did not reply to within a day

Send any message to your agent in WhatsApp, then run:

```bash
just whatsapp-id
```

It prints your id (it looks like `user:123...`), when you last messaged the agent, and whether the
24-hour check can run yet. Once it says ready, without messaging the agent again, send:

```bash
just whatsapp-send user:YOUR_ID
just whatsapp-send user:YOUR_ID "any text you like"
```

The message must arrive. If it is rejected, push alerts cannot work and the design needs a
rethink.

Result: not run yet. Sends inside the reply window worked on 2026-10-15; the 24-hour case is
still open.

### 3. Pairing, explanation, and a real alert

During US market hours, put a ticker in `watchlist.yaml` with a `dip` above its current price,
then run:

```bash
just run
```

1. Send any message to your agent when the log asks; a greeting comes back.
2. Within a minute you get one alert with the price, the level, and, with a Gemini key, a short
   explanation and links.
3. Later cycles send nothing more for that level today.

Record here whether the explanation links were Google redirect URLs or publisher URLs.

Result: pairing OK on 2026-10-15, greeting received. A real alert during market hours is still to
come.

## How it works

- `src/lookout_wire/main.py` loads settings, pairs with WhatsApp, and runs the loop, or a one-shot
  scan with `lookout-wire scan`.
- `src/lookout_wire/core/` holds the loop, settings, port interfaces, and the state-file helper.
- `src/lookout_wire/adapters/` has one module per external system: Alpaca, WhatsApp, Gemini.
- `src/lookout_wire/capabilities/watchlist/` is the first capability: config, level rules,
  alert state, message text, the scan, and the capability that ties them together.
- `src/lookout_wire/checks.py` runs the live and watchlist checks behind the `just check-*` and
  `just whatsapp-*` recipes.
- Alert state and the WhatsApp pairing live in `state/`, which is gitignored.

To add a capability, create a package under `capabilities/`, give it a factory taking settings
and ports, register it in `CAPABILITIES` in `main.py`, and enable it with
`LOOKOUT_ENABLED_CAPABILITIES='["watchlist", "your_name"]'`. The loop calls its `run(now)` every
tick; the capability decides for itself whether there is work to do.
