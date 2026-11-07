# Development

## Tools

[mise](https://mise.jdx.dev) installs Python, uv and golang-migrate. `mise.toml` sets the
versions, and `mise.lock` records the exact version and checksum of every download. CI installs
from the same two files.

```bash
sudo pacman -S mise        # Arch Linux; see https://mise.jdx.dev for other systems
mise trust
mise install --locked      # install what mise.lock records, verifying each download
uv sync
```

`mise.toml` sets `UV_PYTHON_DOWNLOADS=never` and `UV_PYTHON_PREFERENCE=only-system`, so uv uses
the Python that mise installed instead of downloading its own. If mise is not activated in your
shell, prefix commands with `mise exec --`, for example `mise exec -- uv sync`.

To change a tool version, edit `mise.toml`, run `mise lock`, and commit both files. Renovate
usually does this for you (see [dependency updates](dependency-updates.md)).

## Running the bot

Every run needs a Postgres database with the migrations applied and a row in
`crypto_dca.users`, including a dry run:

```bash
export DATABASE_URL="postgresql://user:password@localhost:5432/crypto_dca"
export USER_ID="uuid-of-a-row-in-crypto_dca.users"   # or pass --user-id
migrate -path migrations -database "$DATABASE_URL" up
```

Run `uv run python -m src.main --help` for all options.

### Coinbase

The default exchange. The bot uses the Coinbase **Advanced Trade** API
(`api.coinbase.com/api/v3/brokerage`) with CDP keys, which differ from the older Exchange API:

- Exchange API keys are a key, a secret and a passphrase, signed with base64 HMAC.
- CDP keys are a key ID plus an Ed25519 (or legacy ECDSA) private key. Every request carries a
  JWT bound to its own method and path. There is no passphrase.

Retail accounts can only issue CDP keys. Create one at <https://portal.cdp.coinbase.com>;
Ed25519 is the recommended algorithm. The bot accepts the secret as the base64 string the portal
shows, or as a PEM block.

```bash
export COINBASE_API_KEY="your_key_id"
export COINBASE_API_SECRET="your_base64_ed25519_secret"   # or a PEM private key

uv run python -m src.main --exchange coinbase --symbol BTC-EUR --spend-eur 50
```

There is no usable sandbox. The Advanced Trade sandbox returns canned responses and serves no
market data, so a small live order is the only real end-to-end check.

If the key has an IP allowlist, Coinbase matches the address family your connection actually
uses. Over IPv6, an IPv4 entry never matches.

### Binance

Pass `--exchange binance`, because the default is Coinbase.

```bash
export BINANCE_API_KEY="your_api_key"
export BINANCE_API_SECRET="your_api_secret"

# Dry run
uv run python -m src.main --exchange binance --spend-eur 50 --dry-run

# Testnet (fake money). This spends 50 USDT, the quote asset of BTC-USDT.
uv run python -m src.main --exchange binance \
  --base-url https://testnet.binance.vision --symbol BTC-USDT --spend-eur 50
```

### Dry run

`--dry-run` calculates the order but does not place or record it. It still needs the database
and the exchange keys, and it still applies the weekly check: if a purchase already closed this
week, the dry run exits with "Nothing to do this week" and checks nothing else.

## Tests

```bash
uv sync --group test   # plain `uv sync` does not install the test group
uv run pytest
uv run ruff check && uv run pyright
```

The repository tests use [testcontainers](https://testcontainers.com/) and need a running
Docker daemon (`colima start` if you use colima). The exchange contract tests need neither
Docker nor credentials.

## Migrations

Migrations live in `migrations/` and are applied with
[golang-migrate](https://github.com/golang-migrate/migrate), locally with the command above or
in production with the manual `migrate.yml` workflow (see [CI](ci.md)).
