# CI and scheduled runs

## Workflows

| Workflow | Trigger | Runner | Purpose |
| --- | --- | --- | --- |
| `ci.yml` | every push | GitHub-hosted | Tests, ruff and pyright |
| `daily.yml` | daily at 08:00 UTC, or manual | self-hosted | Places the weekly buys |
| `migrate.yml` | manual | self-hosted | Applies database migrations |
| `renovate.yml` | Mondays at 05:17 UTC, or manual | GitHub-hosted | Opens dependency update PRs |

Only the two workflows that need your secrets and database use the self-hosted runner. CI runs
on GitHub's machines because it also tests Renovate branches, whose code nobody has reviewed yet.
Without a self-hosted runner, `daily.yml` and `migrate.yml` stay queued and never start. The
runner must be version 2.327.1 or newer to run the Node 24 actions.

Every workflow limits the built-in `GITHUB_TOKEN` to `contents: read`. The Renovate workflow
gives it no permissions at all.

## The daily run

The run starts every day, but the [weekly check](bot.md#the-weekly-check) means each coin buys at
most once per ISO week, normally on Monday's run.

Each coin is one entry in a build matrix and runs as its own job. With `fail-fast: false`, a
failure for one coin does not cancel the other. Jobs run one at a time (`max-parallel: 1`)
because there is a single runner, which also keeps them off the database at the same moment.

To add a coin, add a matrix entry and create its spend secret:

```yaml
matrix:
  include:
    - symbol: BTC-EUR
      spend_secret: SPEND_EUR_BTC
    - symbol: ETH-EUR
      spend_secret: SPEND_EUR_ETH
```

To test without buying, start the workflow manually. Its `dry_run` input defaults to `true`:

```bash
gh workflow run daily.yml -f dry_run=true
```

### Secrets

| Name | Purpose |
| --- | --- |
| `COINBASE_API_KEY` | CDP key ID |
| `COINBASE_API_SECRET` | base64 Ed25519 secret, or a PEM private key |
| `DATABASE_URL` | Postgres connection string |
| `USER_ID` | UUID of the `crypto_dca.users` row that orders are booked against |
| `SPEND_EUR_BTC` | EUR to spend each week on BTC-EUR |
| `SPEND_EUR_ETH` | EUR to spend each week on ETH-EUR |

The spend amounts are secrets rather than workflow values because the repository is public. A
missing spend secret falls back to 50 EUR, so set each one explicitly.

### Variables

All optional.

| Name | Default |
| --- | --- |
| `EXCHANGE` | `coinbase` |
| `PRICE_MULTIPLIER` | `0.999` |
| `TIME_IN_FORCE` | `GTC` |
| `LOG_LEVEL` | `INFO` |
| `BASE_URL` | the endpoint of the selected exchange |

## Migrations

`migrate.yml` runs only when started manually. It applies every pending migration in
`migrations/` to `DATABASE_URL` and prints the resulting version. Run it before the next daily
run whenever a change adds a migration.

## Supply chain

Every tool and action is pinned and verified:

- Actions are pinned to full commit SHAs, with the version in a comment.
- `jdx/mise-action` installs a pinned mise version and verifies the binary against mise's
  minisign-signed checksums.
- mise installs Python, uv and golang-migrate from `mise.lock` with `MISE_LOCKED=1`, so a
  download whose checksum differs from the lockfile fails the job. No shared cache is used.
- The Renovate image is pinned by tag and digest.

Renovate keeps these pins current; see [dependency updates](dependency-updates.md).

## Branch rules

The ruleset "main: CI must pass" requires the `test` check from `ci.yml` before anything merges
into `main`. Repository admins can bypass it, which keeps direct pushes to `main` working. The
Renovate App cannot bypass it.
