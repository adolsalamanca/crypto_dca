# Crypto DCA Bot

Simple tool that buys a fixed amount of BTC and ETH every week either in Coinbase or Binance, using a limit order just
below the ask price. GitHub Actions runs it daily on a self-hosted runner. Postgres records every
order, so the bot buys each coin at most once per week

## Quick start

```bash
mise trust && mise install --locked     # Python, uv and golang-migrate, checksum-verified
uv sync

export DATABASE_URL="postgresql://user:password@localhost:5432/crypto_dca"
export USER_ID="uuid-of-a-row-in-crypto_dca.users"
export COINBASE_API_KEY="your_key_id"
export COINBASE_API_SECRET="your_base64_ed25519_secret"

migrate -path migrations -database "$DATABASE_URL" up
uv run python -m src.main --symbol BTC-EUR --spend-eur 50 --dry-run
```

`--dry-run` places no order. Drop it to buy for real: Coinbase has no usable sandbox.

## Docs

- [How the bot works](docs/bot.md): order placement, repricing and the weekly check.
- [Development](docs/development.md): tools, credentials for each exchange, tests and migrations.
- [CI and scheduled runs](docs/ci.md): workflows, secrets, adding a coin, and how tools and
  actions are pinned.
- [Dependency updates](docs/dependency-updates.md): Renovate, automerge and the GitHub App.
