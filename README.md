# lookout-wire

A personal WhatsApp agent for financial alerts. One process runs on your laptop, checks the
watchlist every 10 seconds during US market hours, and messages you in WhatsApp when a stock
trades below a price you chose. With a Gemini key it adds a short explanation of why the stock
is down, with source links.

## Setup

Needs [uv](https://docs.astral.sh/uv/) and [just](https://github.com/casey/just) (`brew install just`).

```bash
just install
cp .env.example .env                       # fill in your keys
cp watchlist.example.yaml watchlist.yaml   # edit your tickers
just run
```

On first start the app asks you to send any message to your agent in WhatsApp. It learns your
id from that message, replies with a greeting, and starts watching prices. Stop it with Ctrl-C.

## Commands

Run `just` to list every recipe.

| Command | What it does |
|---|---|
| `just install` | Install dependencies into `.venv` |
| `just run` | Start the agent |
| `just check` | Lint, format check, and tests: run before every commit |
| `just test` | Run the tests; extra arguments go to pytest, e.g. `just test -k rules` |
| `just fix` | Fix what ruff can fix, then format |
| `just lint` / `just format` | Lint only / format only |
| `just check-alpaca` | Live check 1, below |
| `just whatsapp-id` / `just whatsapp-send ID` | Live check 2, below |

Tests never touch the network. The live-check recipes read your keys from `.env`; nothing else
in the justfile does.

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

Result: not run yet.

### 2. WhatsApp delivers a message you did not reply to within a day

Send any message to your agent in WhatsApp, then read your id:

```bash
just whatsapp-id
```

Copy the `from` value (it looks like `user:123...`). At least 24 hours later, without messaging
the agent again, send:

```bash
just whatsapp-send user:PASTE_ID
```

The message must arrive. If it is rejected, push alerts cannot work and the design needs a
rethink.

Result: not run yet.

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

Result: not run yet.

## How it works

- `src/lookout_wire/main.py` loads settings, pairs with WhatsApp, and runs the loop.
- `src/lookout_wire/core/` holds the loop, settings, port interfaces, and the state-file helper.
- `src/lookout_wire/adapters/` has one module per external system: Alpaca, WhatsApp, Gemini.
- `src/lookout_wire/capabilities/watchlist/` is the first capability: config, level rules,
  alert state, message text, and the capability that ties them together.

To add a capability, create a package under `capabilities/`, give it a factory taking settings
and ports, register it in `CAPABILITIES` in `main.py`, and enable it with
`LOOKOUT_ENABLED_CAPABILITIES='["watchlist", "your_name"]'`. The loop calls its `run(now)` every
tick; the capability decides for itself whether there is work to do.

## Alert rules

- A level alerts the first time the price trades below it.
- After that it alerts again only when the price trades below the lowest price you were already
  told about, at most once per level per trading day.
- When the price recovers to the level, the level re-arms silently.
- Changing a level in `watchlist.yaml` resets it.
- Prices count only during the regular US session, from trades inside it.
