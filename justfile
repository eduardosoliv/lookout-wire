# lookout-wire tasks. Run `just` to list them.

trading_url := "https://paper-api.alpaca.markets"

# List the available recipes
default:
    @just --list

# Install dependencies into .venv
install:
    uv sync

# Start the agent (pairs with WhatsApp on first run)
run:
    uv run lookout-wire

# Run the tests; extra arguments go to pytest, e.g. `just test -k rules`
test *args:
    uv run pytest {{ args }}

# Report lint problems
lint:
    uv run ruff check .

# Format the code
format:
    uv run ruff format .

# Fix what ruff can fix, then format
fix:
    uv run ruff check --fix .
    uv run ruff format .

# Everything that must pass before a commit: lint, format check, tests
check: lint
    uv run ruff format --check .
    uv run pytest

# Live check 1: your Alpaca keys serve prices and the market clock
check-alpaca:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; source .env; set +a
    auth=(-H "APCA-API-KEY-ID: $LOOKOUT_ALPACA_KEY_ID" -H "APCA-API-SECRET-KEY: $LOOKOUT_ALPACA_SECRET_KEY")
    echo "Snapshot (data host):"
    curl -sf "${auth[@]}" "https://data.alpaca.markets/v2/stocks/snapshots?symbols=AAPL&feed=iex" | head -c 300; echo
    echo "Clock (trading host):"
    curl -sf "${auth[@]}" "${LOOKOUT_ALPACA_TRADING_URL:-{{ trading_url }}}/v2/clock"; echo

# Live check 2, step 1: after messaging your agent, show recent messages and your id
whatsapp-id:
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; source .env; set +a
    curl -sf -H "Authorization: Bearer $LOOKOUT_WHATSAPP_TOKEN" \
      "https://api.whatsapp.com/agent/v1/updates?offset=0&limit=50&timeout=5"; echo

# Live check 2, step 2: send a message to an id such as user:123 (run 24 hours later)
whatsapp-send id message="lookout-wire proactive check":
    #!/usr/bin/env bash
    set -euo pipefail
    set -a; source .env; set +a
    curl -sf -X POST "https://api.whatsapp.com/agent/v1/messages" \
      -H "Authorization: Bearer $LOOKOUT_WHATSAPP_TOKEN" -H "Content-Type: application/json" \
      -d '{"messaging_product":"whatsapp","to":"{{ id }}","type":"text","text":{"body":"{{ message }}"}}'; echo
