# Crypto DCA Bot

Automated Dollar-Cost Averaging (DCA) bot for Binance and Coinbase Spot trading. Places
scheduled limit BUY orders at 99.9% of the current ask price via GitHub Actions.

## Symbol format

Symbols are canonically `BASE-QUOTE` (`BTC-EUR`, `LINK-USDC`). The separator is required:
an unseparated pair cannot be split back into base and quote without a table of quote
assets, which silently mis-splits any asset missing from it. Each exchange client renders
the canonical form into its own notation — Binance strips the dash, Coinbase keeps it.

## Test Locally

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

### Binance

```bash
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Dry run (no actual order)
uv run python -m src.main --spend-eur 50 --dry-run

# Testnet (fake money)
uv run python -m src.main \
  --base-url https://testnet.binance.vision \
  --symbol BTC-USDT \
  --spend-eur 50
```

### Coinbase

Targets the Coinbase **Exchange** API. Its sandbox hosts a subset of the real order books,
so resting orders genuinely rest and fill — unlike the Advanced Trade sandbox, which
returns static canned responses and serves no market data at all.

Sandbox keys are issued separately at <https://public.sandbox.exchange.coinbase.com> and
are not interchangeable with production credentials.

```bash
export COINBASE_API_KEY="your_api_key"
export COINBASE_API_SECRET="your_base64_secret"
export COINBASE_PASSPHRASE="your_passphrase"

# Sandbox (fake money, real order book)
uv run python -m src.main \
  --exchange coinbase \
  --base-url https://api-public.sandbox.exchange.coinbase.com \
  --symbol BTC-EUR \
  --spend-eur 50
```

Sandbox prices are fictional and do not track production, so it validates plumbing — auth,
place, poll, cancel — not fill economics.

Run `uv run python -m src.main --help` for all options.

## Tests

```bash
uv run pytest
```

Repository tests use [testcontainers](https://testcontainers.com/) and need a running
Docker daemon (`colima start` if you use colima). The exchange contract tests need neither
Docker nor credentials.

## Migrations

Applied with [golang-migrate](https://github.com/golang-migrate/migrate) via the
`migrate.yml` workflow:

```bash
migrate -path migrations -database "$DATABASE_URL" up
```
