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

See the end of this file once the app is complete.
