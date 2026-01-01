# Crypto DCA Bot

Automated Dollar-Cost Averaging (DCA) bot for Binance Spot trading. Places scheduled limit BUY orders at 99.9% of the current ask price via GitHub Actions.

## Test Locally

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync

# Set credentials
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Dry run (no actual order)
uv run python -m src.main --spend-eur 50 --dry-run

# Testnet (fake money)
uv run python -m src.main \
  --base-url https://testnet.binance.vision \
  --symbol BTCUSDT \
  --spend-eur 50
```

Run `uv run python -m src.main --help` for all options.
