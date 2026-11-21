# generate-artifact

A multi-agent plugin (**Antigravity**, **Claude Code**, **Codex**, and any `.agents/skills` runtime) that turns project work into two living solution artifacts (self-contained HTML/PDF, plus optional **private** `claude.ai` hosted pages when running in Claude):

- an **intent** page: what / why / before state / constraints / outcome success;
- a **5-tab solution** page: Use cases & coverage, Decisions, Concepts, UC details, Components.

It ships one skill (`solution-artifact`), HTML templates, a swappable theme (Anthropic by default), EN/TH i18n and a stdlib-only Python script that validates traceability, renders self-contained HTML pages and exports a combined PDF. Optional `claude.ai` publishing is done by Claude when `claude-artifacts` tools are available; the script never touches the network.

## Install

### Antigravity (`agy`)
```
agy plugin install https://github.com/NRdeARK/generate-artifact
```

### Claude Code
```
claude plugin marketplace add NRdeARK/generate-artifact
claude plugin install generate-artifact@generate-artifact
```

### Codex / Generic `.agents/skills`
Clone the repo and symlink or copy `skills/solution-artifact` into `~/.agents/skills/solution-artifact` (or `~/.codex/skills/solution-artifact`).

Requires Python 3.9+ (stdlib only) and git. PDF export uses headless Edge or Chrome if found.

## Use

Ask your agent: "generate the intent artifact for `<project>`", then, after you approve it in chat, "generate the solution artifact". The agent follows `skills/solution-artifact/SKILL.md`: write data files, translate Thai changes, `validate`, commit, `render` to `docs/artifact/build/<tab>.html`, and (in Claude) publish and record URLs in `artifacts.json`.

Script (run from the plugin, point it at a project's `docs/artifact` folder):

```
python skills/solution-artifact/scripts/artifact_sync.py validate   <dir> [--strict]
python skills/solution-artifact/scripts/artifact_sync.py render     <dir> [--tabs intent tab1 ...] [--theme NAME|FILE.css] [--dry-run]
python skills/solution-artifact/scripts/artifact_sync.py export-pdf <dir> [--lang th] [--theme NAME|FILE.css] [--dry-run]
python skills/solution-artifact/scripts/artifact_sync.py i18n-diff  <dir>
```

Tests: `python -m unittest discover tests` (the real-browser tests, `tests/browser/cdp_check.mjs`, need Edge or Chrome and Node 22+; they skip otherwise; set `CDP_SHOTS_DIR` to keep the phone/touch/Thai screenshots)

## Folder layout

```
.antigravity-plugin/      plugin.json (Antigravity native manifest)
.claude-plugin/           plugin.json, marketplace.json (Claude Code manifest)
skills/solution-artifact/
  SKILL.md                router: intent vs solution, publish loop, sharing warning
  intent.md, solution.md  the spec for each artifact
  templates/              HTML structure + base.css/base.js (no colors, no fonts)
  themes/anthropic.css    design tokens (swap for another theme)
  i18n/en.json, th.json   UI strings
  scripts/artifact_sync.py
  examples/sample-project/docs/artifact/   fixture ("Status Board")
tests/                    unittest suite
```

Per project (`docs/artifact/`): `intent.md`, `registry.json`, `usecases.json`, `decisions.json`, `concepts.json`, `i18n/th.json`, `diagrams/*.svg`, `artifacts.json`. See the fixture for field shapes.

## Notes

- Diagrams are produced with the `diagram-design` skill (`diagram-design:diagram-design` in Claude) and stay English-only.
- Tabs without data are shown disabled in the nav, never hidden.
- Tab 4 and Tab 5 contain code details: check for secrets before sharing a page.
- Version history is git; footers and changelogs are generated from `git log`.

Screenshots: TODO.

## License

MIT
