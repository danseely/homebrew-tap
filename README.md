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

`tag` and `tarball_url` are required inputs for a valid run. GitHub tarball
inputs are normalized to Homebrew-compatible `/archive/refs/tags/...tar.gz`
URLs before the formula is rewritten. The workflow:

1. Creates or resets the release branch from the tap default branch.
2. Downloads the upstream release tarball.
3. Runs `scripts/update-formula-from-release.py` to rewrite `Formula/agendum.rb`.
4. Exits cleanly without a PR if the formula already matches that release.
5. Pushes the branch and opens or updates the release PR.

That workflow only generates or updates a PR. It does not run Homebrew
validation and it does not bottle or publish.

PR CI is the only automated validation lane for the release PR. The relevant
checks are the `brew test-bot` workflow, which includes the updater script tests
and formula validation.

Publishing and bottling happen separately through the normal Homebrew PR
publish flow after the release PR is green.

If an upstream release is missed, rerun the workflow with `workflow_dispatch`
and the original release payload to reconcile it. The automation does not push
formula changes directly to `main`.

Formula mutation is repo-local on purpose. The updater script uses the release
tarball and its bundled `uv.lock` as the source of truth, while PR CI verifies
the rewritten formula. Publishing and bottling remain separate from release PR
generation.

## Maintainer runbook

Replay a missed release with the workflow’s existing `workflow_dispatch`
payload:

- `source_repo`
- `tag`
- `version`
- `release_url`
- `tarball_url`
- `published_at`

For local investigation, use Python 3.11+ to download the release tarball and run:

```bash
python3 scripts/update-formula-from-release.py \
  --formula Formula/agendum.rb \
  --version 0.2.0 \
  --tarball /tmp/agendum-v0.2.0.tar.gz \
  --tarball-url https://github.com/danseely/agendum/archive/refs/tags/v0.2.0.tar.gz
```

Use failure type to decide where to look:

- generation failure: the tarball could not be downloaded, hashed, parsed, or
  rewritten by `scripts/update-formula-from-release.py`
- PR CI failure: the release PR exists, but `brew test-bot` or the updater
  script tests failed

Generation failures are local to the release workflow and the updater script.
PR CI failures are normal pull request failures and should be debugged from the
checks on the PR.

Once PR CI passes, bottling and publish follow the separate Homebrew PR flow;
they are not part of the release generation workflow.

If the updater script produces no diff for a release that has already been
applied, the workflow exits successfully without pushing a branch or creating a
noisy no-op PR.
