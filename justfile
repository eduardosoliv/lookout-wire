# lookout-wire tasks. Run `just` to list them.

# List the available recipes
default:
    @just --list

# Install dependencies into .venv and switch on the git hooks
install:
    uv sync
    git config core.hooksPath .githooks

# Start the agent (pairs with WhatsApp on first run)
run:
    uv run lookout-wire

# Message every ticker below its Dip or Buy level right now, market open or not, then exit
scan:
    uv run lookout-wire scan

# Run the tests; extra arguments go to pytest, e.g. `just test -k rules`
test *args:
    uv run pytest {{ args }}

# Measure test coverage; lists the lines no test runs. `just coverage html` also writes htmlcov/
coverage report="term":
    uv run pytest --cov=lookout_wire --cov-branch --cov-report=term-missing:skip-covered {{ if report == "html" { "--cov-report=html" } else { "" } }}

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

# Check that watchlist.yaml (or another file) is well formed; needs no keys
check-watchlist path="":
    uv run python -m lookout_wire.checks watchlist {{ path }}

# Live check 1: your Alpaca keys serve prices and the market clock
check-alpaca:
    uv run python -m lookout_wire.checks alpaca

# Live check 2, step 1: after messaging your agent, show your id and when the 24-hour check can run
whatsapp-id:
    uv run python -m lookout_wire.checks whatsapp-id

# Live check 2, step 2: send a message to your id, e.g. `just whatsapp-send user:123 "hello"`
whatsapp-send id *text:
    uv run python -m lookout_wire.checks whatsapp-send {{ quote(id) }} {{ quote(text) }}
