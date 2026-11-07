# Dependency updates

Renovate opens pull requests for three kinds of dependency:

- GitHub Actions in `.github/workflows`, kept pinned to commit SHAs;
- tools in `mise.toml`, with `mise.lock` regenerated in the same PR;
- Python packages in `pyproject.toml` and `uv.lock`. Renovate only updates `uv.lock` within the
  ranges in `pyproject.toml`, and keeps `cryptography` below 49, the last version with an
  x86_64 macOS wheel.

`renovate.json` holds the configuration.

## What merges on its own

Renovate opens a PR only once a release has been public for 7 days, and marks it with a
`renovate/stability-days` status.

Minor and patch updates merge automatically through GitHub auto-merge once CI passes. Major
updates wait for you to review and merge them.

Automerged updates include runtime packages, mise tools and workflow changes. They reach
`daily.yml`, which runs with your Coinbase keys on the self-hosted runner, without anyone
reading them first. The 7-day wait and the CI check are the only gates.

## How Renovate runs

`renovate.yml` runs Renovate every Monday at 05:17 UTC on a GitHub-hosted runner, never on the
self-hosted one, because Renovate runs lock commands such as `mise lock` and `uv lock`.

It does not use a stored token. Each run uses the GitHub App's private key to create a token that
is valid for 1 hour and only for this repository, and revokes it at the end. The private key is a
secret in the `renovate` environment, which only `main` can use.

To debug a run, start it manually with debug logging:

```bash
gh workflow run renovate.yml -f log_level=debug
```

## Setting up the GitHub App

Do this once.

1. Create a GitHub App at <https://github.com/settings/apps/new>:
   - Clear **Webhook > Active**.
   - Repository permissions:
     - **Read and write**: Contents, Pull requests, Workflows, Commit statuses.
     - **Read-only**: Checks, Dependabot alerts, Metadata.
     - **No access**: everything else.
   - **Where can this GitHub App be installed?**: Only on this account.
2. On the App page, copy the **Client ID** and generate a private key.
3. Install the App on this repository only (**Only select repositories**).
4. In the repository settings, create an environment named `renovate`, and under
   **Deployment branches and tags** allow only `main`.
5. In the `renovate` environment, add:
   - variable `RENOVATE_APP_CLIENT_ID`: the Client ID;
   - secret `RENOVATE_APP_PRIVATE_KEY`: the contents of the private key file.
6. Delete the local copy of the private key.
7. In the repository settings, enable **Allow auto-merge**.
8. Run the `Renovate` workflow manually and check the log.

Each permission has a job. Contents, Pull requests and Workflows let Renovate push branches and
open PRs, including PRs that change workflow files. Commit statuses lets it post
`renovate/stability-days`; without it, Renovate creates the branch and then stops before opening
the PR. Checks lets it read CI results. Dependabot alerts lets it see known vulnerabilities.

GitHub's Dependabot security alerts are separate from Renovate. Keep them on.
