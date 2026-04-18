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

1. Refreshes Python resources with Homebrew tooling.
2. Recomputes the formula sha256 from the release tarball URL.
3. Updates `Formula/agendum.rb`.
4. Runs `brew install --build-from-source`, `brew test agendum`, and
   `brew audit --strict --online`.
5. Pushes a release branch in this repo and opens or updates the PR.

If an upstream release is missed, rerun the workflow with `workflow_dispatch`
and the original release payload to reconcile it. The automation does not push
formula changes directly to `main`.

When `brew update-python-resources` cannot resolve the package by name, the
workflow falls back to the `uv.lock` bundled in the upstream release tarball and
regenerates the resource blocks from that lockfile.
