# open-lienzo agent plugin

One skill, `open-lienzo` ([skills/open-lienzo/SKILL.md](skills/open-lienzo/SKILL.md)), packaged for two agents:

| Agent | Manifest | Install | What it can do |
|---|---|---|---|
| Claude Code | `.claude-plugin/plugin.json` (marketplace: repo root `.claude-plugin/marketplace.json`) | `claude plugin marketplace add cognikas/open-lienzo` then `claude plugin install open-lienzo@open-lienzo` | Everything: pull, push and db through the `Artifact` and `ArtifactData` tools, plus the local commands |
| Codex | `plugin.json` (marketplace: repo root `.agents/plugins/marketplace.json`) | `codex plugin marketplace add cognikas/open-lienzo`, then `/plugins` in Codex | Local only: find, status, history, new, the viewer, editing local copies. Pushes stay pending for a Claude Code session |

The skill runs the scripts through Docker (`docker compose run --rm lienzo …`) when it is available, and falls back to
the local Python otherwise. See the repo [README](../README.md) for the image and the viewer.
