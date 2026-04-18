# Danseely Tap

## How do I install these formulae?

`brew install danseely/tap/<formula>`

Or `brew tap danseely/tap` and then `brew install <formula>`.

Or, in a `brew bundle` `Brewfile`:

```ruby
tap "danseely/tap"
brew "<formula>"
```

## Documentation

`brew help`, `man brew` or check [Homebrew's documentation](https://docs.brew.sh).

## Release automation

This tap has a workflow that listens for the upstream `agendum_release_published`
dispatch event and a matching manual `workflow_dispatch` entry point for
replays.

The dispatch payload uses these fields:

- `source_repo`
- `tag`
- `version`
- `release_url`
- `tarball_url`
- `published_at`

`tag` and `tarball_url` are required inputs for a valid run. The workflow:

1. Creates or resets the release branch from the tap default branch.
2. Downloads the upstream release tarball.
3. Runs `scripts/update-formula-from-release.py` to rewrite `Formula/agendum.rb`.
4. Exits cleanly without a PR if the formula already matches that release.
5. Runs `brew install --build-from-source`, `brew test agendum`, and
   `brew audit --strict --online`.
6. Pushes the release branch and opens or updates the PR.

If an upstream release is missed, rerun the workflow with `workflow_dispatch`
and the original release payload to reconcile it. The automation does not push
formula changes directly to `main`.

Formula mutation is repo-local on purpose. The updater script uses the release
tarball and its bundled `uv.lock` as the source of truth, while Homebrew is
used only to validate the rewritten formula.

## Maintainer runbook

Replay a missed release with the workflow’s existing `workflow_dispatch`
payload:

- `source_repo`
- `tag`
- `version`
- `release_url`
- `tarball_url`
- `published_at`

For local investigation, download the release tarball and run:

```bash
python3 scripts/update-formula-from-release.py \
  --formula Formula/agendum.rb \
  --version 0.2.0 \
  --tarball /tmp/agendum-v0.2.0.tar.gz \
  --tarball-url https://api.github.com/repos/danseely/agendum/tarball/v0.2.0
```

Failure classes map to these recovery points:

- tarball/checksum: release tarball could not be downloaded or hashed
- `uv.lock` parsing: the release tarball is missing `uv.lock` or it is malformed
- resource generation: a required dependency is missing metadata or an sdist
- install: `brew install --build-from-source` fails for the rewritten formula
- test: `brew test` fails the installed CLI self-check
- audit: `brew audit --strict --online` reports a blocking problem
- PR creation: the validated branch could not be pushed or the PR could not be created/updated

If the updater script produces no diff for a release that has already been
applied, the workflow exits successfully without pushing a branch or creating a
noisy no-op PR.
