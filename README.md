# lookout-wire

A personal WhatsApp agent for financial alerts. One process runs on your laptop, checks the
watchlist every 10 seconds during US market hours, and messages you in WhatsApp when a stock
trades below a price you chose. With a Gemini key it adds a short explanation of why the stock
is down, with source links.

## Setup

```bash
uv sync
cp .env.example .env                       # fill in your keys
cp watchlist.example.yaml watchlist.yaml   # edit your tickers
uv run lookout-wire
```

On first start the app asks you to send any message to your agent in WhatsApp. It learns your
id from that message, replies with a greeting, and starts watching prices.

## Development

```bash
uv run pytest
uv run ruff check .
uv run ruff format .
```

Tests never touch the network.

## Live checks

These need your real keys and cannot run in tests. Load the keys into your shell first:

```bash
set -a; source .env; set +a
```

### 1. Alpaca keys serve prices and the market clock

```bash
curl -s -H "APCA-API-KEY-ID: $LOOKOUT_ALPACA_KEY_ID" -H "APCA-API-SECRET-KEY: $LOOKOUT_ALPACA_SECRET_KEY" \
  "https://data.alpaca.markets/v2/stocks/snapshots?symbols=AAPL&feed=iex" | head -c 300; echo
curl -s -H "APCA-API-KEY-ID: $LOOKOUT_ALPACA_KEY_ID" -H "APCA-API-SECRET-KEY: $LOOKOUT_ALPACA_SECRET_KEY" \
  "${LOOKOUT_ALPACA_TRADING_URL:-https://paper-api.alpaca.markets}/v2/clock"; echo
```

Both must return JSON, the first with a `latestTrade` for AAPL. If the clock call fails with
live keys, set `LOOKOUT_ALPACA_TRADING_URL=https://api.alpaca.markets`.

Result: not run yet.

### 2. WhatsApp delivers a message you did not reply to within a day

Send any message to your agent in WhatsApp, then read your id:

```bash
curl -s -H "Authorization: Bearer $LOOKOUT_WHATSAPP_TOKEN" \
  "https://api.whatsapp.com/agent/v1/updates?offset=0&limit=50&timeout=5"
```

Copy the `from` value (it looks like `user:123...`). At least 24 hours later, without messaging
the agent again, send:

```bash
curl -s -X POST "https://api.whatsapp.com/agent/v1/messages" \
  -H "Authorization: Bearer $LOOKOUT_WHATSAPP_TOKEN" -H "Content-Type: application/json" \
  -d '{"messaging_product":"whatsapp","to":"user:PASTE_ID","type":"text","text":{"body":"lookout-wire proactive check"}}'
```

The message must arrive. If it is rejected, push alerts cannot work and the design needs a
rethink.

Result: not run yet.

### 3. Pairing, explanation, and a real alert

During US market hours, put a ticker in `watchlist.yaml` with a `dip` above its current price,
then run:

```bash
uv run lookout-wire
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
