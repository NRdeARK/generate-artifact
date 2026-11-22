# generate-artifact

A multi-agent plugin (**Antigravity**, **Claude Code**, **Codex**, and any `.agents/skills` runtime) that turns project work into ONE living, self-contained HTML page (also exportable to PDF) with an in-page tab bar:

- an **intent** tab: what / why / before state / constraints / outcome success;
- a **5-tab solution**: Use cases & coverage, Decisions, Concepts, UC details, Components.

It ships one skill (`solution-artifact`), HTML templates, a swappable theme (Anthropic by default), EN/TH i18n and a stdlib-only Python script that validates traceability, renders the single page and exports a combined PDF. Publishing goes through a swappable adapter (`claude-ai` for private claude.ai hosting, or `local`); the script never touches the network.

**What changed: single page + adapters.** `render` now writes one file, `build/index.html`, instead of six pages: tabs switch in-page with hash routing (`#intent`, `#tab1`..`#tab5`), a tab without data is shown disabled, `--tabs` is gone, and there are no cross-page links. Publishing moved out of `SKILL.md` into `skills/solution-artifact/adapters/` (contract in its `README.md`); `artifacts.json` now records `adapter` + `publish.<adapter> = {id, url}` with no version. Old `tabs.*` entries are left untouched (`validate` warns `legacy-tabs`; delete those artifacts manually). New command: `open`. The built-in theme no longer imports web fonts, so the page works offline.

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

Ask your agent: "generate the intent artifact for `<project>`", then, after you approve it in chat, "generate the solution artifact". The agent follows `skills/solution-artifact/SKILL.md`: write data files, translate Thai changes, `validate`, `render` to `docs/artifact/build/index.html`, publish through the selected adapter, record it in `artifacts.json`, and make one commit.

Script (run from the plugin, point it at a project's `docs/artifact` folder):

```
python skills/solution-artifact/scripts/artifact_sync.py validate   <dir> [--strict]
python skills/solution-artifact/scripts/artifact_sync.py render     <dir> [--theme NAME|FILE.css] [--dry-run]
python skills/solution-artifact/scripts/artifact_sync.py open       <dir> [--no-open]   # prints the file:// URI of build/index.html, opens the browser
python skills/solution-artifact/scripts/artifact_sync.py export-pdf <dir> [--lang th] [--theme NAME|FILE.css] [--dry-run]
python skills/solution-artifact/scripts/artifact_sync.py i18n-diff  <dir>
```

Tests: `python -m unittest discover tests` (the real-browser tests, `tests/browser/cdp_check.mjs`, need Edge or Chrome and Node 22+; they skip otherwise; set `CDP_SHOTS_DIR` to keep the phone/touch/Thai screenshots)

## Folder layout

```
.antigravity-plugin/      plugin.json (Antigravity native manifest)
.claude-plugin/           plugin.json, marketplace.json (Claude Code manifest)
skills/solution-artifact/
  SKILL.md                router: intent vs solution, publish loop (one commit), sharing warning
  intent.md, solution.md  the spec for each artifact
  templates/              HTML structure + base.css/base.js (no colors, no fonts)
  themes/anthropic.css    design tokens (swap for another theme)
  i18n/en.json, th.json   UI strings
  adapters/               publish adapters: README.md (contract), claude-ai.md, local.md
  scripts/artifact_sync.py
  examples/sample-project/docs/artifact/   fixture ("Status Board")
tests/                    unittest suite
```

Per project (`docs/artifact/`): `intent.md`, `registry.json`, `usecases.json`, `decisions.json`, `concepts.json`, `i18n/th.json`, `diagrams/*.svg`, `artifacts.json`. See the fixture for field shapes.

## Notes

- Diagrams are produced with the `diagram-design` skill and stay English-only.
- Tabs without data are shown disabled in the tab bar, never hidden.
- The page contains Tab 4 and Tab 5 code details: check for secrets before sharing it.
- Version history is git; footers and changelogs are generated from `git log`.

Screenshots: TODO.

## License

MIT
