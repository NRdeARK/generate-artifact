# generate-artifact

A multi-agent plugin (**Antigravity**, **Claude Code**, **Codex**, and any `.agents/skills` runtime) that turns project work into ONE living, self-contained HTML page (also exportable to PDF) with an in-page tab bar:

- an **intent** tab: what / why / before state / constraints / outcome success;
- a **5-tab solution**: Use cases & coverage, Decisions, Concepts, UC details, Components.

It ships one skill (`solution-artifact`), HTML templates, a swappable theme (Anthropic by default), EN/TH i18n and a stdlib-only Python script that validates traceability, renders the single page and exports a combined PDF. Publishing goes through a swappable adapter (`claude-ai` for private claude.ai hosting, or `local`); the script never touches the network.

## What changed in 0.2.0

- **Single page + adapters.** `render` writes one file, `build/index.html`: tabs switch in-page with hash routing (`#intent`, `#tab1`..`#tab5`), a tab without data is shown disabled, `--tabs` is gone, and there are no cross-page links. Publishing moved out of `SKILL.md` into `skills/solution-artifact/adapters/` (contract in its `README.md`); `artifacts.json` records `adapter` + `publish.<adapter> = {id, url}` with no version. Old `tabs.*` entries are left untouched (`validate` shows info `legacy-tabs`; delete those artifacts manually). New command: `open`.
- **Run modes.** *Step mode* is the default: one stage at a time (Intent, Tab 1..Tab 5), each validated, committed, rendered and published, then the agent stops until you approve in chat. Progress is kept in `docs/artifact/progress.json` (`{"mode": "step|auto", "approved": [...], "skipped": [...]}`, checked by `validate`: unknown or duplicate stages, out-of-order or data-less approvals), so a new session resumes at the next stage. Say "auto mode", "just do it" or "run all stages" to run every stage without pausing (each stage is still committed and published as a checkpoint). Approvals are their own commit (`approve <stage>`) and the publish record is its own commit (`record publish <stage>`); commits are never amended. There is no review mode.
- **Thai on every publish.** Each stage translates only its new or stale strings (`i18n-diff`, Haiku translator) into `i18n/th.json` before the stage commit, so every published page has a complete EN/TH toggle; `validate` fails on `th-missing` / `th-stale` (`th-orphan` warns) otherwise. One approval per stage covers both languages. Saying "translate" re-checks all strings. **PDF export** takes `--lang en|th|both` (`both` writes `artifact-en.pdf` and `artifact-th.pdf`; Thai is refused while translations are missing); the agent asks which you want.
- **Stricter Tab 4 checks.** New warnings: `uc-too-large`, `step-undeclared`, `step-not-in-diagram`, `step-flow-mismatch`, `infra-participant-missing`, `label-format`, `label-too-detailed`, `arrow-no-callsite`, `callsite-missing`, and `steps-missing` (a `code`/`mixed` UC must list `steps[]`, each with a real `call: "path:line"`).
- **Fonts by adapter.** The Anthropic theme ships subset woff2 files (Source Serif 4, Inter, IBM Plex Sans Thai, Noto Serif Thai; SIL OFL, see `themes/fonts/OFL.txt`). `render --fonts google` (claude-ai adapter) loads them from Google Fonts; `render --fonts local` (default, local adapter) inlines them into the one HTML file, so `build/index.html` works on its own. In `google` mode, offline or blocked: system fonts. Theme `url()`s are checked (only `fonts/<file>.woff2` inside the theme or Google Fonts). `artifact_sync.py` stays stdlib-only.

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
python skills/solution-artifact/scripts/artifact_sync.py export-pdf <dir> [--lang en|th|both] [--fonts local|google] [--theme NAME|FILE.css] [--dry-run]
python skills/solution-artifact/scripts/artifact_sync.py i18n-diff  <dir>
```

Tests: `python -m unittest discover tests` (the real-browser tests, `tests/browser/cdp_check.mjs`, need Edge or Chrome and Node 22+; they skip otherwise; set `CDP_SHOTS_DIR` to keep the phone/touch/Thai screenshots)

## Folder layout

```
.antigravity-plugin/      plugin.json (Antigravity native manifest)
.claude-plugin/           plugin.json, marketplace.json (Claude Code manifest)
skills/solution-artifact/
  SKILL.md                router: intent vs solution, run modes, stage loop (EN + TH per stage), sharing warning
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
