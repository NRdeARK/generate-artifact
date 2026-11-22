---
name: solution-artifact
description: Turn project work into ONE living, self-contained HTML page (also exportable to PDF) with an in-page tab bar, publishable through a pluggable adapter. Two kinds. (1) INTENT artifact - what / why / before state / constraints / outcome success. (2) SOLUTION artifact - 5 tabs (use cases & coverage, decisions, concepts, UC details, components) with C4 component registry, traceability check and EN/TH toggle. Use only when the user asks for an intent artifact or a solution artifact by name (e.g. "intent artifact", "make the intent page", "solution artifact", "solution design artifact", "use cases / decisions / components tabs"), or invokes /solution-artifact. Do not use for generic "make an artifact / page / publish this / share link" requests.
---

# solution-artifact (router)

Works in any agent runtime. It drafts and validates data, renders **one** self-contained page `<dir>/build/index.html` (inline CSS/JS/SVG, offline; tabs switch in-page via `#intent`, `#tab1`..`#tab5`), and publishes it through an **adapter** (`adapters/README.md`). Pages start private to the user.
Spec files next to this one: `intent.md` (intent section), `solution.md` (all 5 tabs and cross-cutting rules). Read the one you route to before drafting.
Script: `scripts/artifact_sync.py` (path relative to this file; `validate`, `render`, `open`, `export-pdf`, `i18n-diff`). Python 3.9+, stdlib only, never calls the network.

## 1. Route
| User wants | Go to |
|---|---|
| What / why / constraints for a project, or "intent" | **Intent flow** (section 3) |
| Design after an approved intent: use cases, decisions, concepts, flows, components | **Solution flow** (section 4) |
| A one-off page not tied to a project | The selected adapter's generic publish (`adapters/`) |

If unclear, ask which one. Never draft a solution before the intent is approved.

## 2. Where data lives
`docs/artifact/` in the project repo (fallback: `<workspace>/projects/<name>/artifact/`, with its own `git init`): `intent.md`, `registry.json`, `usecases.json`, `decisions.json`, `concepts.json`, `i18n/th.json`, `diagrams/*.svg`, `artifacts.json`.
Field shapes: copy `examples/sample-project/docs/artifact/`. English is the source; Thai is keyed by ID with a source hash.

## 3. Intent flow
1. Write `intent.md` (What, Why, Before state, Constraints, Outcome success). Short. No open questions.
2. Draw the overview (swimlane by default; sequence only for a specific, understood flow) with the `diagram-design` skill if available, otherwise stop and ask the user. Save as `diagrams/intent.svg`.
3. Run the **publish loop** (section 5).
4. User reviews the page and approves **in chat**. Only then start the solution flow.

## 4. Solution flow
Build only the tabs the work needs. A tab whose data file is absent is shown **disabled (greyed, not clickable), never hidden**.
- Tab 1 `usecases.json` (outcomes, UCs, crud) · Tab 2 `decisions.json` · Tab 3 `concepts.json` · Tab 4 `usecases.json` (flows, touchpoints) · Tab 5 `registry.json` (per-component `change`). Component IDs come from the registry, C4 L1-L3.
- **Diagrams:** every diagram via the `diagram-design` skill if available (otherwise stop and ask the user): registry component names and levels, theme colors (status colors for changed/new/removed), English labels, every arrow labelled. Export self-contained SVG (no scripts, no external refs) to `diagrams/<name>.svg` using the file names the data files reference. **Never hand-draw placeholders**; a missing file shows a visible placeholder box and validate warns. Reference: `examples/sample-project/docs/artifact/diagrams/`. Inline SVG is sanitized (`unsafe-svg`); keep text 11px or larger.
- **Tab 4: ONE sequence diagram per UC** (`uc.diagram`), written **from reading the actual code**; `source` is `code`, `draft` or `mixed`. The SVG holds the good path plus every bad/edge path as an `alt` fragment: `<g data-fragment="<flow id>" data-kind="bad|edge" data-block="1" data-y data-h display="none">` per `flows[]` entry (ids match), good-path messages as `data-block` rows, lifelines `data-lifeline`, legend `data-legend`, root `data-reflow data-top data-w data-legend-h`. Fixture: `diagrams/uc-01.svg`. Aim for 2+ bad and 2+ edge scenarios on a main UC; `touchpoints` has one row per scenario. Put `data-step="<label>"` on each message group and list added/changed steps in `step_changes` (`{"2": "added", "3b": "changed"}`).
- **Tab 5 per component:** each changed/new/removed component needs `change.diagrams.before` and `.after` (component, direct neighbours, labelled flows); a db/store also `extra` = ER schema diff, a queue `extra` = topology; optional `flowchart: [{title, diagram}]`. **C4 L3 lives inside one container**: L3 components set `parent: <container id>`, the container carries `views.L3 {before, after}`; the overview switch offers L1 and L2 only (`views.L3` at registry level is an error). `change.files` entries are `{path, change: added|modified|removed|renamed, from?}`; db/store changes carry `change.data_dictionary.tables[]` (per column `name, change, type, nullable, default, meaning`, `values`, `samples`, `example`, `sample_rows`).
- **Tab 3 concepts** carry `plain` (one sentence for a non-engineer), `before`/`now`/`why`, optional `analogy`, plus technical `summary` and `terms`.
- Tab 2 is **read-only** (no choose buttons): decisions happen in chat or comments; update `decisions.json` (chosen/deferred/rejected + reason, deferred needs `revisit_when`) and republish.
- Layout facts the renderer already handles (do not re-implement): sticky auto-hiding top bar, phone-first flipped tables with a Flip/Cards toggle, `.table-wrap` scroll boxes, EN/TH toggle. `tests/browser/cdp_check.mjs` checks 390px and 360px.

## 5. Publish loop (one page, one commit)
1. Edit data files.
2. **Thai:** `python scripts/artifact_sync.py i18n-diff <dir>` lists missing/stale entries as JSON. Hand only those to a fast, cheap translator sub-agent, which writes `i18n/th.json` as `{key: {"source_hash": <from diff>, "text": {path: thai}}}`. Keep IDs, component names, code, paths untranslated.
3. `python scripts/artifact_sync.py validate <dir>`: fix every ERROR (exit 1); show or fix warnings.
4. `python scripts/artifact_sync.py render <dir> [--theme anthropic]` writes `<dir>/build/index.html` (`--theme` takes a name in `themes/` or a path to a .css file). The footer and changelogs read `git log`, and render runs before the commit, so the page shows the previous commit; this is accepted.
5. **Publish** with the selected adapter (selection order in `adapters/README.md`: `artifacts.json` `"adapter"`, then a detected hosting adapter, then `local`). Title is the project name.
6. Write `adapter` and `publish.<adapter> = {id, url}` into `artifacts.json` (no version field).
7. **ONE commit** with data + `artifacts.json` together; mention IDs like `UC-02` in the message so change tracks fill in.
8. Reply with the URL (or `local` path and `artifact_sync.py open`) in one line. Do not paste page content.
Legacy `artifacts.json` `tabs.*` entries (old per-tab artifacts) are never edited or deleted: list them to the user once as "old per-tab artifacts, left as-is; delete manually if wanted". `validate` reports them as warning `legacy-tabs`.

## 6. Sharing warning (every time)
The one page contains Tab 4 and Tab 5 code details (flows, schemas, file paths). Before the user shares it, remind them to check for secrets and internal hostnames. The agent never changes sharing settings.

## 7. PDF export and end of life
`python scripts/artifact_sync.py export-pdf <dir> [--lang th]` writes `build/artifact-print.html` (cover + every present section, expanded) and converts with headless Edge/Chrome if found; otherwise print it from a browser.
When the user says the project is done, **offer** (never automatic): an `[archive]` note in their notes vault, and which production signal would start a new intent.

## Never
- Pages that impersonate a real person, company or brand, or fake records.
- Publishing a file you haven't read. Deleting a published artifact unless asked. Changing sharing settings.

## Runtime notes
- Claude: the `diagram-design` skill is `diagram-design:diagram-design`; the translator sub-agent can be Sonnet or Haiku. The `claude-ai` adapter needs the `claude-artifacts` tools.
- Other runtimes (`agy`, `codex`, `gemini`): use the `local` adapter unless an adapter for the runtime exists.
