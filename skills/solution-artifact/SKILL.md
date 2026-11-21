---
name: solution-artifact
description: Turn project work into living HTML/PDF solution artifact pages (and optionally publish to private claude.ai artifact pages when running in Claude). Two kinds. (1) INTENT artifact - what / why / before state / constraints / outcome success. (2) SOLUTION artifact - 5 tabs (use cases & coverage, decisions, concepts, UC details, components) with C4 component registry, traceability check and EN/TH toggle. Use only when the user asks for an intent artifact or a solution artifact by name (e.g. "intent artifact", "make the intent page", "solution artifact", "solution design artifact", "use cases / decisions / components tabs"), or invokes /solution-artifact. Do not use for generic "make an artifact / page / publish this / share link" requests.
---

# solution-artifact (router)

Works in **any agent** (`agy`, `claude`, `codex`, `gemini`) for drafting, validating, rendering self-contained HTML (`<dir>/build/<tab>.html`), and exporting PDF. Optional private `claude.ai` hosting (Step 6) requires the `claude-artifacts` tools when running in Claude; pages start **private to the user** and sharing happens only in the `claude.ai` UI.

Spec files next to this one: `intent.md` (intent page), `solution.md` (all 5 tabs and cross-cutting rules). Read the one you route to before drafting.
Script: `scripts/artifact_sync.py` (resolve the path relative to this file). Python 3.9+, stdlib only, never calls the network.

## 1. Route
| User wants | Go to |
|---|---|
| What / why / constraints for a project, or "intent" | **Intent flow** (`intent.md`) |
| Design after an approved intent: use cases, decisions, concepts, flows, components | **Solution flow** (`solution.md`) |
| A one-off page (report, deck, dashboard, explainer) not tied to a project | **Generic publish** (section 6) |

If unclear, ask which one. Never draft a solution before the intent is approved.

## 2. Where data lives
Project data is `docs/artifact/` in the project repo (fallback: `<workspace>/projects/<name>/artifact/`, with its own `git init`):
`intent.md`, `registry.json`, `usecases.json`, `decisions.json`, `concepts.json`, `i18n/th.json`, `diagrams/*.svg`, `artifacts.json`.
Field shapes: copy `examples/sample-project/docs/artifact/`. English is the source; Thai is keyed by ID with a source hash.

## 3. Intent flow
1. Write `intent.md` (sections: What, Why, Before state, Constraints, Outcome success). Short. No open questions.
2. Draw the overview (swimlane by default; sequence only for a specific, understood flow) with the `diagram-design` skill (`diagram-design:diagram-design` in Claude), save as `diagrams/intent.svg`.
3. Run the **publish loop** (section 5) for `intent` only.
4. User reviews the page and approves **in chat**. Only then start the solution flow.

## 4. Solution flow
Build only the tabs the work needs (each is its own artifact). A tab whose data file is absent is skipped and shown **disabled, never hidden**, in every nav.
- Tab 1 `usecases.json` (outcomes, UCs, crud) · Tab 2 `decisions.json` · Tab 3 `concepts.json` · Tab 4 `usecases.json` (flows, touchpoints) · Tab 5 `registry.json` (per-component `change`).
- Component IDs come from the registry, C4 levels L1-L3.
- **Diagrams:** for every diagram, invoke the `diagram-design` skill (`diagram-design:diagram-design` in Claude) (swimlane, sequence, tree, concept, wireframe) with the registry component names and levels, the theme colors (status colors for changed/new/removed), English labels and every arrow labelled. Export self-contained SVG (no scripts, no external refs) to `docs/artifact/diagrams/<name>.svg`, using the file names the data files reference. **Never hand-draw placeholder diagrams**; a missing file shows a visible placeholder box and validate warns. Reference output: `examples/sample-project/docs/artifact/diagrams/`.
- **Tab 4: ONE sequence diagram per UC** (`uc.diagram`, e.g. `diagrams/uc-01.svg`), written **from reading the actual code**; label each `source`: `code`, `draft` or `mixed`. Every arrow labelled. The SVG holds the good path **plus every bad path and edge case as an `alt` fragment**: a `<g data-fragment="<flow id>" data-kind="bad|edge" data-block="1" data-y=".." data-h=".." display="none">` per entry of the UC's `flows[]` (ids must match), good-path messages as `data-block` rows, lifelines as `data-lifeline`, the legend as `data-legend`, root `data-reflow data-top data-w data-legend-h`. The page's toggles show or hide fragments in place and the diagram reflows. Fixture: `diagrams/uc-01.svg`. Aim for at least 2 bad and 2 edge scenarios on a main UC; `touchpoints` carry one row per scenario.
  Put `data-step="<step label>"` on each message group and list what the UC adds or changes in `usecases.json` `step_changes` (`{"2": "added", "3b": "changed"}`), taken from the code diff or plan; the renderer colours and tags those arrows.
- **Tab 5 per component:** each changed/new/removed component needs `change.diagrams.before` and `.after` (focused: the component, its direct neighbours, labelled flows, change colors); a db/store also needs `extra` = ER schema diff (tables, columns, PK/FK, crow's-foot cardinality, new/changed/removed), a queue `extra` = topology (producers, queue, DLQ, consumers). Optional `change.diagrams.flowchart: [{title, diagram}]` for "how the feature decides" (decision diamonds, yes/no branches; fixture: Web app freshness label). **C4 L3 lives inside one container**: give each L3 component `parent: <container id>` and put `views.L3 {before, after}` on that container; the overview switch (Tab 1, Tab 5) offers L1 and L2 only (`views.L3` at registry level is an error).
- **Tab 3 concepts are understandable at a glance:** each concept carries `plain` (one sentence a non-engineer gets, shown large and as the side-index subtitle; missing = warning `concept-plain-missing`), `before` / `now` / `why` (a small 3-row card in plain words) and an optional `analogy`. The technical `summary` and `terms` stay below the diagram. All translatable.
- **Top bar:** sticky, hides while scrolling down and returns on any upward scroll, near the top, with keyboard focus inside it, with the viewer open or while the side index is used (`--header-h` follows the visible state). There are no status or count badges in the header.
- **Phone first:** the page must never scroll sideways (a real-device bug). Tables always sit in a `.table-wrap` scroll box; UC, touchpoint, impact and dictionary tables are rendered twice from the same rows (`table_set`): at 640px or less the default is a **flipped** table (field names in a sticky first column, one item per column, scroll-snap swipe with a peek of the next item and a fade + swipe hint), with a Flip / Cards toggle (`ga-table-view` in localStorage) for the labelled-card layout (`class="stack"` plus a `cell-label` per cell); desktop keeps the normal table; the CRUD matrix keeps a scroll box with a sticky first column. `tests/browser/cdp_check.mjs` checks 390px and 360px with real touch events.
- **Tab 5 data detail:** `change.files` entries are objects `{path, change: added|modified|removed|renamed, from?}` (plain strings count as modified and warn `file-change-type-missing`); the page shows a +/~/- badge per file and a summary like "Files changed (4): 2 added, 1 modified, 1 removed". For a db/store, `change.data_dictionary.tables[]` documents every new or changed table: per column `name, change (new|changed|removed), type, nullable, default, meaning` (plain words; a new/changed column without one warns `column-meaning-missing`), `values[{value, meaning}]` for enums, 2-3 `samples`, an `example {before, after}` for a changed column, and per table `sample_rows {columns, rows}`. Meanings get Thai; samples stay data.
- Diagrams are embedded as **inline, sanitized SVG** (no scripts, `foreignObject`, event handlers or external references; `validate` reports them as `unsafe-svg`). Keep text at 11px or larger so it stays crisp and readable.
- Tab 2 is **read-only**: no choose buttons. Decisions happen in chat or artifact comments; update `decisions.json` (chosen/deferred/rejected + reason, deferred needs `revisit_when`), then republish.
- Feedback arrives in chat or artifact comments. Update the data, republish, reply in the thread.

## 5. Publish loop (per page set)
1. Edit data files.
2. **Thai:** `python scripts/artifact_sync.py i18n-diff <dir>` lists missing/stale entries as JSON. Spawn one fast/mid-tier sub-agent (e.g. Sonnet, Gemini Flash/Neon, or Codex) to translate only those into `i18n/th.json` as `{key: {"source_hash": <from diff>, "text": {path: thai}}}`. Keep IDs, component names, code, paths untranslated.
3. `python scripts/artifact_sync.py validate <dir>` - fix every ERROR (exit 1). Warnings (stale Thai, missing diagram) are shown; fix or tell the user.
4. **Commit** the data (`git commit`, mention IDs like `UC-02` in the message so change tracks fill in). The footer and changelogs come from git, so always render *after* this commit.
5. `python scripts/artifact_sync.py render <dir> [--tabs tab2 ...] [--theme anthropic]` -> `<dir>/build/<tab>.html` (self-contained). `--theme` takes a name in `themes/` or a path to a .css file.
6. **Hosting (optional, Claude-only):** When `claude-artifacts` tools are available, publish each changed page: first call `guide` (topic `pages`) once per session. New: `publish_files` with `files: [{as: "index.html", path: <build file>}]`, `title` and `icon`. Update: `get_artifact` for `base_version`, republish (pass `shared_live` if shared).
   Titles: `<Project> · <n> <Tab>` e.g. `Status Board · 2 Decisions` (the rendered `<title>` already matches; no version in the title).
   When `claude-artifacts` tools are **not** available (e.g. in `agy` or `codex`), deliver the local rendered HTML files in `<dir>/build/` (and optionally PDF via section 8).
7. When hosted on `claude.ai`, write `slug`, `url`, `version` into `artifacts.json` under `tabs.<intent|tab1..tab5>` (URLs must be https on claude.ai or they are not linked), commit again. A newly created tab changes every other page's nav, so re-render and republish the others once (cost: only when a tab is added).
8. Reply with the URLs or local build paths and one line per page. Do not paste page content.

Cost: build only the tabs needed; republish only pages whose data changed; Thai only for changed entries.

## 6. Generic publish (not a project page)
When `claude-artifacts` tools are available: `guide(intent: "slides" | "design" | "other")`; if it names a type, use `create_from_type`, otherwise read `guide(topic: "pages")` and `guide(topic: "design")`, build one self-contained page, and publish with `publish_version` (new: `title` + `icon` + `content`). Otherwise write a self-contained HTML file locally. Diagrams via `diagram-design`, charts via `dataviz`. No secrets or personal data unless asked.

## 7. Sharing warning (every time, Tab 4 and Tab 5)
Before the user shares, remind them: **Tab 4 and Tab 5 contain real code details** (flows, schemas, file paths). Check for secrets and internal hostnames first. The agent cannot change sharing settings; the user does it manually.

## 8. PDF export
`python scripts/artifact_sync.py export-pdf <dir> [--lang th]` writes `build/artifact-print.html` (cover + intent + existing tabs, everything expanded) and converts with headless Edge/Chrome if found; otherwise open the HTML and print to PDF.

## 9. End of life (offer, never automatic)
When the user says the project is done, **offer**: (a) an `[archive]` note in their Obsidian vault, (b) recording which production signal would start a new intent. Do each only if they say yes.

## Never
- Pages that impersonate a real person, company or brand, or fake records.
- Publishing a file you haven't read. Deleting an artifact unless asked (`delete_artifact`; the user confirms).
- Changing sharing settings (not possible with these tools; say so).
