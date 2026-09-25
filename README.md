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

Targets the Coinbase **Advanced Trade** API (`api.coinbase.com/api/v3/brokerage`) with CDP
keys. This is not the Exchange API: Exchange uses a key/secret/passphrase triple signed
with base64 HMAC, while CDP keys are a key id plus an Ed25519 (or legacy ECDSA) private
key, and every request carries a JWT bound to its own method and path. Retail accounts can
only issue CDP keys. There is no passphrase.

Create a key at <https://portal.cdp.coinbase.com>; Ed25519 is the recommended algorithm.
The secret is accepted either as the base64 string the portal shows or as a PEM block.

```bash
export COINBASE_API_KEY="your_key_id"
export COINBASE_API_SECRET="your_base64_ed25519_secret"   # or a PEM private key

uv run python -m src.main \
  --exchange coinbase \
  --symbol BTC-EUR \
  --spend-eur 50
```

There is no usable sandbox. The Advanced Trade sandbox returns canned responses and serves
no market data, so it cannot drive this bot — the only real verification is a small live
order. Use `--dry-run` to check configuration without placing one.

If a key is IP-allowlisted, note that Coinbase matches the address family your connection
actually uses: with IPv6 connectivity an IPv4 entry never matches.

Run `uv run python -m src.main --help` for all options.

## Scheduled runs

`.github/workflows/daily.yml` runs every day at 08:00 UTC and buys at most once per week
per coin — the weekly gate is keyed on `(user_id, symbol, side)`, so each coin is
independent. Coins are a build matrix, one job each, with `fail-fast: false` so one coin
failing does not cancel the other.

To add a coin: add a matrix entry and create its spend secret.

```yaml
matrix:
  include:
    - symbol: BTC-EUR
      spend_secret: SPEND_EUR_BTC
    - symbol: ETH-EUR
      spend_secret: SPEND_EUR_ETH
```

### Secrets

| Name | Purpose |
| --- | --- |
| `COINBASE_API_KEY` | CDP key id |
| `COINBASE_API_SECRET` | base64 Ed25519 secret, or a PEM private key |
| `DATABASE_URL` | Postgres connection string |
| `USER_ID` | UUID of the row in `crypto_dca.users` orders are booked against |
| `SPEND_EUR_BTC` | EUR to spend per week on BTC-EUR |
| `SPEND_EUR_ETH` | EUR to spend per week on ETH-EUR |

Spend amounts live in secrets rather than the workflow because this repository is public.
A missing spend secret falls back to 50 EUR, so set it explicitly.

### Variables

All optional; each falls back to the value shown.

| Name | Default |
| --- | --- |
| `EXCHANGE` | `coinbase` |
| `PRICE_MULTIPLIER` | `0.999` |
| `TIME_IN_FORCE` | `GTC` |
| `LOG_LEVEL` | `INFO` |
| `BASE_URL` | the venue's own endpoint |

`USER_ID` must match an existing user row. A stale value fails the run rather than
recording nothing: an order that reaches the exchange but is not saved exits non-zero,
because the weekly check reads the saved row and would otherwise buy again.

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
