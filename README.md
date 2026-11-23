# open-lienzo

A local, git-versioned copy of your claude.ai artifacts, and a viewer for it. A Cognikas open tool, based on
**lienzo** by Willy Sotomayor ([original README, in Spanish](README.es.md)).

| Piece | What it does |
|---|---|
| **Viewer** (`viewer/`) | Serves your artifacts folder on `127.0.0.1`: a searchable index across accounts with tabs per client and app, an editable Design canvas with local comments, a Slides viewer, and pages whose `claude.use('db')` runs against a local db |
| **Sync** (`sync/` + the agent plugin) | Tracks local ↔ claude.ai per account: find, status, pull, push, db, history, de-duplication and client/app tags. A Claude Code session does the network with its `Artifact` and `ArtifactData` tools; the scripts keep the state |

Python standard library only, plus static HTML/JS. No build step, no database: your artifacts stay plain files in
your own git repo, which is what gives history, diffs, and lets agents read and edit them directly.

## Quick start (Docker, any OS)

```sh
git clone https://github.com/cognikas/open-lienzo && cd open-lienzo
cp .env.example .env          # set LIENZO_DATA to your artifacts repo, and LIENZO_AUTHOR
docker compose up -d          # or build locally: docker compose up -d --build
```

Open http://127.0.0.1:8765/. The scripts run in the same image:

```sh
docker compose run --rm lienzo sync status --root /data/<account> --account <account>
docker compose run --rm lienzo entities --repo /data --apply
docker compose run --rm lienzo dedupe --repo /data --apply
docker compose run --rm lienzo fonts          # cache Google Fonts for offline viewing
docker compose run --rm lienzo install-hook   # secret scan before each commit in your artifacts repo
```

Your artifacts repo is mounted at `/data`. Put `accounts.json` there (copy `sync/accounts.example.json`; account
folders are relative to it). The image runs as a non-root user with a read-only filesystem; on Linux set
`LIENZO_UID`/`LIENZO_GID` in `.env` to the owner of your artifacts folder. In Git Bash on Windows, set
`MSYS_NO_PATHCONV=1` first, or it rewrites `/data/...` into a Windows path (PowerShell is fine as is).

Artifacts are a full backup of your claude.ai work and can carry tokens or keys, so keep that repo private and
install the hook: it scans what each commit adds with Trivy's secret scanner (a local `trivy`, or the pinned
`aquasec/trivy` image through Docker) and stops the commit on a finding. Without Docker:
`cp hooks/artifacts-pre-commit <artifacts repo>/.git/hooks/pre-commit`.

## From source

```sh
python viewer/serve.py --root /path/to/artifacts --author "Your name"    # Windows: python or py, not python3
python sync/sync.py status --root /path/to/artifacts/<account> --account <account>
python -m unittest discover -s tests
```

## Agent plugin: Claude Code and Codex

One skill, packaged for both ([plugin/](plugin/README.md)):

```sh
claude plugin marketplace add cognikas/open-lienzo && claude plugin install open-lienzo@open-lienzo
codex plugin marketplace add cognikas/open-lienzo      # then /plugins inside Codex
```

Claude Code gets everything (pull, push, db). Codex has no `Artifact` tools, so it works locally (find, status,
viewer, edits) and leaves pushes pending for a Claude Code session.

## What is not in this repo or its image

- **Your artifacts and accounts**: they live in your own repo (`LIENZO_DATA`).
- **Anthropic's Design runtime** (`dc-runtime.js`): it is Anthropic's code. Each user fetches it from one of their own
  Design artifacts into `<data>/_runtime/design-<version>/artifact-type/` (the skill does it on the first pull).
  Without it the canvas, notes and comments work, but boards render blank.

## Security model

The API has **no authentication**. It only accepts requests whose `Host` and `Origin` are `127.0.0.1`/`localhost`
(blocks DNS rebinding and CSRF), and writes require `Content-Type: application/json`. So the port must stay on
loopback: `compose.yaml` publishes `127.0.0.1:8765` only. The container drops all capabilities, has
`no-new-privileges` and a read-only root filesystem; it writes only to `/data` and a tmpfs `/tmp`.

## Build and release

| Step | Tooling |
|---|---|
| Commit | Husky: compile check + unit tests (`pre-commit`), Conventional Commits via commitlint (`commit-msg`), Trivy fs scan (`pre-push`, if installed). `npm install` once to enable the hooks |
| CI | Unit tests on Linux, Windows (UTF-8 mode off) and macOS; tests inside the image; Trivy image + repo scans to code scanning, failing on CRITICAL |
| Release | A maintainer sets the version in `package.json` and both plugin manifests, then publishes `gh release create vX.Y.Z --title vX.Y.Z --notes-file <notes>`. The release workflow checks the tag matches those versions, pushes a multi-arch image (amd64, arm64) with SBOM and provenance to `ghcr.io/cognikas/open-lienzo`, gates it with Trivy, and attaches the plugin zip and source tarball |

Every GitHub Action is pinned by commit SHA. Trivy's own actions had their tags hijacked in March 2026
(GHSA-69fq-xp46-6x23); the pinned `trivy-action` is an immutable release from after that.

## License

[MIT](LICENSE), © 2026 Cognikas. Original author: Willy Sotomayor (lienzo, 2026-11-15).

open-lienzo is an independent tool by Cognikas. It is not made, endorsed or supported by Anthropic; Claude and
claude.ai are Anthropic's trademarks. It works with your own claude.ai account through Claude Code's tools and
redistributes none of Anthropic's code.
