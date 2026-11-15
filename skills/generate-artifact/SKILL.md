---
name: generate-artifact
description: Turn project work into private claude.ai artifact pages. Two kinds. (1) INTENT artifact - what / why / before state / constraints / outcome success. (2) SOLUTION artifact - 5 tabs (use cases & coverage, decisions, concepts, UC details, components) with C4 component registry, traceability check and EN/TH toggle. Use when the user says "generate artifact", "make the intent page", "solution artifact", "publish this", "make a page", "share link", or hands over a finished deliverable (report, plan, spec, PRD, memo, dashboard) meant for others to read.
---

# generate-artifact (router)

Pages start **private to the user**. Sharing happens only in the claude.ai UI; these tools can't share, unshare or make a page public.

Spec files next to this one: `intent.md` (intent page), `solution.md` (all 5 tabs and cross-cutting rules). Read the one you route to before drafting.
Script: `scripts/artifact_sync.py` (resolve the path relative to this file). Python 3, stdlib only, never calls the network.

## 1. Route
| User wants | Go to |
|---|---|
| What / why / constraints for a project, or "intent" | **Intent flow** (`intent.md`) |
| Design after an approved intent: use cases, decisions, concepts, flows, components | **Solution flow** (`solution.md`) |
| A one-off page (report, deck, dashboard, explainer) not tied to a project | **Generic publish** (section 6) |

If unclear, ask which one. Never draft a solution before the intent is approved.

## 2. Where data lives
Project data is `docs/artifact/` in the project repo (fallback: `<claude-space>/projects/<name>/artifact/`, with its own `git init`):
`intent.md`, `registry.json`, `usecases.json`, `decisions.json`, `concepts.json`, `i18n/th.json`, `diagrams/*.svg`, `artifacts.json`.
Field shapes: copy `examples/sample-project/docs/artifact/`. English is the source; Thai is keyed by ID with a source hash.

## 3. Intent flow
1. Write `intent.md` (sections: What, Why, Before state, Constraints, Outcome success). Short. No open questions.
2. Draw the overview (swimlane by default; sequence only for a specific, understood flow) with the `diagram-design:diagram-design` skill, save as `diagrams/intent.svg`.
3. Run the **publish loop** (section 5) for `intent` only.
4. User reviews the page and approves **in chat**. Only then start the solution flow.

## 4. Solution flow
Build only the tabs the work needs (each is its own artifact). A tab whose data file is absent is skipped and shown **disabled, never hidden**, in every nav.
- Tab 1 `usecases.json` (outcomes, UCs, crud) · Tab 2 `decisions.json` · Tab 3 `concepts.json` · Tab 4 `usecases.json` (flows, touchpoints) · Tab 5 `registry.json` (per-component `change`).
- Component IDs come from the registry, C4 levels L1-L3. Every diagram is made with `diagram-design:diagram-design` from registry names/levels and saved in `diagrams/` (English only).
- Tab 4 sequence diagrams are written **from reading the actual code**; label each `source`: `code`, `draft` or `mixed`. Every arrow labelled.
- Tab 2 is **read-only**: no choose buttons. Decisions happen in chat or artifact comments; update `decisions.json` (chosen/deferred/rejected + reason, deferred needs `revisit_when`), then republish.
- Feedback arrives in chat or artifact comments. Update the data, republish, reply in the thread.

## 5. Publish loop (per page set)
1. Edit data files.
2. **Thai:** `python scripts/artifact_sync.py i18n-diff <dir>` lists missing/stale entries as JSON. Spawn one **Sonnet** sub-agent to translate only those into `i18n/th.json` as `{key: {"source_hash": <from diff>, "text": {path: thai}}}`. Keep IDs, component names, code, paths untranslated.
3. `python scripts/artifact_sync.py validate <dir>` - fix every ERROR (exit 1). Warnings (stale Thai, missing diagram) are shown; fix or tell the user.
4. `python scripts/artifact_sync.py render <dir> [--tabs tab2 ...]` -> `<dir>/build/<tab>.html` (self-contained).
5. **Commit** the data (`git commit`, mention IDs like `UC-02` in the message so change tracks fill in). The footer and changelogs come from git.
6. Publish each changed page with the claude-artifacts tools: first call `guide` (topic `pages`) once per session. New: `publish_files` with `files: [{as: "index.html", path: <build file>}]`, `title` and `icon`. Update: `get_artifact` for `base_version`, republish (pass `shared_live` if shared).
   Titles: `<Project> · <n> <Tab>` e.g. `Status Board · 2 Decisions` (the rendered `<title>` already matches; no version in the title).
7. Write `slug`, `url`, `version` into `artifacts.json` under `tabs.<intent|tab1..tab5>`, commit again. A newly created tab changes every other page's nav, so re-render and republish the others once (cost: only when a tab is added).
8. Reply with the URLs and one line per page. Do not paste page content.

Cost: build only the tabs needed; republish only pages whose data changed; Thai only for changed entries.

## 6. Generic publish (not a project page)
`guide(intent: "slides" | "design" | "other")`; if it names a type, use `create_from_type`. Otherwise read `guide(topic: "pages")` and `guide(topic: "design")`, build one self-contained page, publish with `publish_version` (new: `title` + `icon` + `content`). Diagrams via `diagram-design`, charts via `dataviz`. No secrets or personal data unless asked.

## 7. Sharing warning (every time, Tab 4 and Tab 5)
Before the user shares, remind them: **Tab 4 and Tab 5 contain real code details** (flows, schemas, file paths). Check for secrets and internal hostnames first. Claude cannot share; the user does it in claude.ai.

## 8. PDF export
`python scripts/artifact_sync.py export-pdf <dir> [--lang th]` writes `build/artifact-print.html` (cover + intent + existing tabs, everything expanded) and converts with headless Edge/Chrome if found; otherwise open the HTML and print to PDF.

## 9. End of life (offer, never automatic)
When the user says the project is done, **offer**: (a) an `[archive]` note in their Obsidian vault, (b) recording which production signal would start a new intent. Do each only if they say yes.

## Never
- Pages that impersonate a real person, company or brand, or fake records.
- Publishing a file you haven't read. Deleting an artifact unless asked (`delete_artifact`; the user confirms).
- Changing sharing settings (not possible with these tools; say so).
