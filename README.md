# Crypto DCA Bot

Automated Dollar-Cost Averaging (DCA) bot for Binance and Coinbase spot trading. The bot places
scheduled limit BUY orders at 99.9% of the current ask price. GitHub Actions runs it on a
self-hosted runner.

## Symbol format

The canonical symbol form is `BASE-QUOTE` (`BTC-EUR`, `LINK-USDC`). The bot requires the separator.
Without it, the bot cannot split a pair into base and quote without a table of quote assets.
Such a table silently splits any asset that is not in it incorrectly. Each exchange client
converts the canonical form to its own notation. The Binance client removes the dash. The
Coinbase client keeps it.

## Test locally

```bash
# Install uv (if not already installed)
curl -LsSf https://astral.sh/uv/install.sh | sh

# Install dependencies
uv sync
```

Every run needs a Postgres database and a user row, including a dry run. Set these two values
before you run the bot:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/crypto_dca"
export USER_ID="uuid-of-a-row-in-crypto_dca.users"
```

The bot reads `USER_ID` from the environment. You can also pass it as `--user-id`. Apply the
migrations first (see [Migrations](#migrations)).

### Binance

```bash
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Dry run (no actual order)
uv run python -m src.main --exchange binance --spend-eur 50 --dry-run

# Testnet (fake money)
uv run python -m src.main \
  --exchange binance \
  --base-url https://testnet.binance.vision \
  --symbol BTC-USDT \
  --spend-eur 50
```

The default exchange is `coinbase`, so the Binance commands must pass `--exchange binance`.

`--spend-eur` is an amount in the quote asset of the pair, not always in EUR. The testnet
example above spends 50 USDT.

### Coinbase

The bot uses the Coinbase **Advanced Trade** API (`api.coinbase.com/api/v3/brokerage`) with CDP
keys. This API is different from the Exchange API:

- The Exchange API uses a key, a secret and a passphrase, signed with base64 HMAC.
- CDP keys are a key ID plus an Ed25519 (or legacy ECDSA) private key. Each request carries a
  JWT for its own method and path.

Retail accounts can only issue CDP keys. There is no passphrase.

Create a key at <https://portal.cdp.coinbase.com>. Ed25519 is the recommended algorithm. The
bot accepts the secret as the base64 string that the portal shows, or as a PEM block.

```bash
export COINBASE_API_KEY="your_key_id"
export COINBASE_API_SECRET="your_base64_ed25519_secret"   # or a PEM private key

uv run python -m src.main \
  --exchange coinbase \
  --symbol BTC-EUR \
  --spend-eur 50
```

There is no usable sandbox. The Advanced Trade sandbox returns canned responses and serves no
market data, so it cannot drive this bot. The only real verification is a small live order.

### Dry run

`--dry-run` calculates an order but does not place it. It does not record anything in the
database. It still needs the database and the exchange keys. It also applies the weekly check.
If an order already filled this week, the dry run exits with "Nothing to do this week"
and checks nothing else.

### IP allowlists

If you add an IP allowlist to a Coinbase key, Coinbase compares it with the address family that
your connection uses. With IPv6 connectivity, an IPv4 entry never matches.

Run `uv run python -m src.main --help` for all options.

## Scheduled runs

`.github/workflows/daily.yml` runs every day at 08:00 UTC. All workflows in this repository use
`runs-on: self-hosted`. Without a self-hosted runner, the jobs stay in the queue and never start.

The weekly check looks for a `FILLED` order in the current ISO week (Monday to Sunday, UTC) for
`(user_id, symbol, side)`. Each coin is independent. If the check finds such an order, the run
exits with no new order.

The bot saves every order that it places, but only a `FILLED` order closes the week:

- A `CANCELLED` order (for example, after the maximum number of reprices) does not close the
  week. The next daily run tries again.
- A `PARTIALLY_FILLED` order does not close the week. The next daily run places a new order for
  the full spend amount. A week can therefore buy more than the weekly spend.

If the weekly check cannot read the database, the bot logs
`Weekly check failed ... Proceeding with order` and places the order. A database read error can
therefore cause a second order in the same week.

Each coin is one entry in a build matrix, and each entry is a separate job. The workflow sets
`fail-fast: false`, so a failure for one coin does not cancel the job for the other coin.

To add a coin, add a matrix entry and create its spend secret:

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
| `COINBASE_API_KEY` | CDP key ID |
| `COINBASE_API_SECRET` | base64 Ed25519 secret, or a PEM private key |
| `DATABASE_URL` | Postgres connection string |
| `USER_ID` | UUID of the row in `crypto_dca.users` that the bot books orders against |
| `SPEND_EUR_BTC` | EUR to spend each week on BTC-EUR |
| `SPEND_EUR_ETH` | EUR to spend each week on ETH-EUR |

The spend amounts are secrets, not workflow values, because this repository is public. If a
spend secret is missing, the workflow spends 50 EUR. Set each spend secret explicitly.

### Variables

All variables are optional. Each one uses the default shown if you do not set it.

| Name | Default |
| --- | --- |
| `EXCHANGE` | `coinbase` |
| `PRICE_MULTIPLIER` | `0.999` |
| `TIME_IN_FORCE` | `GTC` |
| `LOG_LEVEL` | `INFO` |
| `BASE_URL` | the endpoint of the selected exchange |

`USER_ID` must match an existing user row. A stale value makes the run fail, so the bot does not
lose orders silently. If an order reaches the exchange but the bot cannot save it, the run exits
with a non-zero code. This matters because the weekly check reads the saved row. Without it, the
bot could buy again on a later run.

## Tests

```bash
uv sync --group test
uv run pytest
```

The test dependencies are in the `test` dependency group, and `uv sync` alone does not install
them.

The repository tests use [testcontainers](https://testcontainers.com/) and need a running Docker
daemon (`colima start` if you use colima). The exchange contract tests need neither Docker nor
credentials.

## Migrations

The `migrate.yml` workflow applies the migrations with
[golang-migrate](https://github.com/golang-migrate/migrate). You start it manually. To apply them
locally:

```bash
migrate -path migrations -database "$DATABASE_URL" up
```
