# Solution artifact (spec v0.1)

One large, living document that runs from design to finish. Input: the **approved intent artifact**. One artifact per project; it gets republished as a new version on every change (see Versioning).

## Cross-cutting rules (apply to every tab)

### Component registry (the comparison backbone)
- Keep one registry of components, each with a **stable ID**, a name and a **level** (e.g. L1 system → L2 service/app → L3 module/table/queue).
- Every diagram in every tab draws components **from the registry, at the same level, with the same name, position order and color**. The same box always looks the same wherever it appears.
- Change status is shown the same way everywhere: **unchanged / changed / new / removed** (consistent color + badge).
- Purpose: the user does lots of side-by-side comparison, so it must be obvious what changed and what stayed the same.

### Use-case IDs
- Each use case has a stable ID (`UC-01`…) and a tier: **Main** (comes directly from outcome success) or **Affected** (impacted because a shared component or data changed).
- The same IDs are used in all tabs.

### Diagrams
- **Every diagram is made with the `diagram-design:diagram-design` skill** (swimlane, sequence, tree, architecture, ER…), styled from the same component registry.

### Collapsing
- Anything "done" or "detail" starts **collapsed**: decided decision groups, change tracks, file lists.

---

## Tab 1 — Use cases & coverage
1. **Recap from intent**: before state and the issues/problems (pulled from the intent artifact, not re-written).
2. **Outcome success → use cases**: a full-width outcome banner, then a tree diagram (`diagrams/uc-tree.svg`: Outcome → Main UCs → Affected UCs, each Affected edge labelled with the causing component), then one compact table (ID, Tier, plain-words summary, traced from, link to Tab 4). The table replaces any separate list of Affected UCs.
3. **Impact sweep → Affected UCs**: for each data or component touched, ask "if this changes, which existing flows break or behave differently?" Every hit becomes an Affected UC, linked to the component that causes it.
4. **CRUD lifecycle check**: a matrix of **entity/component × Create / Read / Update / Delete**. Each cell maps to a UC or is explicitly marked "not needed (why)". Gaps get flagged so no lifecycle is half-built.
5. Before/after overview diagram using registry components.

*(Room reserved for more UC-quality ideas from the user later.)*

## Tab 2 — Decisions
- **Solution path tree** (overview at top): a tree diagram from the problem to the options considered, the sub-options found along the way, and the chosen path. Rejected branches stay visible (greyed) with a one-line "why not".
- **Decision groups** below the tree, one per fork in the tree:
  - The options compared side by side (pros, cons, cost, risk, which UCs each satisfies).
  - The choice + reason, with a date.
  - **Collapses automatically once decided**, so open decisions stay on top.
- **Read-only, no click-to-choose.** The page has no choose buttons and no in-page state. The user decides **in chat or in an artifact comment**, often with a custom answer that isn't one of the options, and Claude updates the data and republishes. (Click state wouldn't reach Claude and would be lost on a browser or cache change.)
- **Every option shows its status and reason**: `chosen` (why), `deferred` (why, and what would bring it back), `rejected` (why not). Deferred options stay visible in the group and in the tree.
- **UI decisions**: when a choice is visual, include low-fi **wireframes** per option for comparison.
- Decisions made *between* paths (e.g. "picked queue over cron, which forced X") are recorded here too, linked to tree nodes.

## Tab 3 — Concepts
- The intro for team review: explains what's new in the solution (new terms, new components, behavior changes) **at concept level**.
- **One block per concept.** The concept is the main thing; text is the description.
- **At least 1 diagram per concept block.**
- Ordered so a newcomer can read top to bottom.

## Tab 4 — Use case details
Per UC (Main first, then Affected):
- **Plain-words summary**: what this UC is about, in 1–2 simple sentences.
- **Sequence diagram** (ONE per UC, inline SVG, text at least 11px):
  - Written **from reading the actual code**, not from existing docs (docs go stale or skip steps).
  - **Every arrow is labelled.** No unlabelled hops through middle components.
  - Big flows get split: the top level shows the overview, and a step expands into its own **detail sequence diagram** underneath.
  - **Good path** is shown by default. Every **bad path** and **edge case** is an `alt` fragment *inside the same diagram* (each with its own id); group buttons ("Bad paths", "Edge cases") and a per-fragment list ("Bad: API timeout", "Edge: zero projects") show or hide them in place, and the diagram reflows (hidden fragments leave no gap). A main UC should show at least 2 bad paths and 2 edge cases.
- **User touchpoints**: a table of every message or screen the user sees, per scenario (good / bad / edge).
- **Arrow legend**: what each arrow means (call, async message, response, event…).
- **Change track** (collapsed by default): what changed in this UC per version.

## Tab 5 — Components
1. **All components, before and after, with data flow**: a side-by-side **architecture / data-flow diagram** (made with diagram-design), not just a grid of boxes. Every arrow between components is labelled with what flows (request, event, rows, email…). New or changed flows use the same change colors as components (new / changed / removed). The system-wide overview has one before/after pair per level, **L1 Context and L2 Containers only** (the switch swaps the pair; the same applies to the Tab 1 overview). **C4 L3 is components inside ONE container**, so it never joins the overview: it appears inside the owning container's card as "Inside <container> (C4 L3)" with its own before/after pair (a container with L3 views but no changes gets a compact card with an unchanged badge). Every before/after pair has an **Expand both** button: the viewer opens both diagrams side by side (stacked on narrow screens) with synced zoom and pan and a Sync toggle. Data: `registry.json` `views` (`{L1: {before, after}, L2: ...}`), per-container `views.L3`, L3 components declare `parent`. A level or container with components but no view is a validation warning; a global `views.L3` is an error (`l3-not-global`).
2. **Per-component change** (each card starts with its own diagrams: a focused before/after pair, a type-specific extra, and optional logic flowcharts):
   - Diagrams (`change.diagrams`): `before`/`after` (the component, its direct neighbours and labelled flows, with new/changed/removed colored); `extra` for a db/store (ER schema diff with PK/FK and crow's-foot cardinality) or a queue (topology: producers, queue, DLQ, consumers); optional `flowchart: [{title, diagram}]` for how a feature decides (diamonds, yes/no branches, terminals). Missing before/after/extra is a warning; the flowchart is optional.
   - What changes and why (linked to UCs and decisions).
   - Data changes: schema migrations, new tables/columns, indexes, plus a **data dictionary** for every new or changed table (collapsed by default, under the migration table): per column name, type, nullable, default, meaning in plain words, allowed values with what each means (enums), 2-3 sample values, a before -> after example for a changed column, and a small sample-rows table. Meanings are translated; sample values stay data.
   - Infra changes: new queues/topics (with counts), jobs, configs, permissions.
   - **Files changed** (collapsed by default): each file carries its change type (added / modified / removed / renamed with old -> new path) as a +/~/- badge in the theme change colors, and the summary line counts them ("Files changed (4): 2 added, 1 modified, 1 removed").

---

## Theme & i18n (applies to intent + all tabs)

### Theme: separate layer from templates
- Templates contain **structure only** and use CSS variables (`--bg`, `--fg`, `--accent`, `--font-display`, `--font-body`, status colors `--unchanged/--changed/--new/--removed/--deferred`…). They contain no hard-coded colors or fonts.
- Themes are swappable token files (`themes/<name>.css` or `.json`). Changing the theme never touches a template, and changing a template never touches the theme.
- **Default theme: Anthropic**: Ivory `#FAF9F5` ground, Slate `#141413` text, Clay `#D97757` accent, serif display over sans body. Status colors are tuned to stay distinguishable on Ivory (they differ in lightness, not hue alone).
- Diagrams made with `diagram-design` take their colors from the same theme tokens (via a diagram-design client profile).
- **Thai font fallback** in every theme: Latin fonts don't cover Thai, so the stacks need a Thai face (e.g. IBM Plex Sans Thai / Noto Sans Thai / Noto Serif Thai).

### i18n: EN master, TH translated
- Two layers of text:
  1. **UI strings** (tab names, labels, legends, "chosen/deferred"): one catalog per language (`i18n/en.json`, `i18n/th.json`). Translated once and reused by every project.
  2. **Project content** (UCs, decisions, concepts, touchpoints): English is the source of truth in the data files. The Thai version sits alongside it, keyed by the same IDs.
- **Flow**: write and approve English → spawn a **Sonnet sub-agent** to translate into Thai → publish. On each new version only **changed or new entries** get re-translated (diff by ID + content hash), which keeps cost down.
- **Kept untranslated**: IDs (UC-01, D2), component names from the registry, code, API paths, file names.
- **Diagrams stay English-only** (decided 2026-11-15): diagram labels are never translated and each diagram is drawn once. Only the text around diagrams toggles EN/TH.
- **Switching**: an EN | TH toggle on every page. Language is a view preference, not a decision, so in-page state is fine here. It's remembered per browser.
- The sync script flags any entry whose TH translation is missing or stale.

## Versioning = git (decided 2026-11-15)
- These are personal work docs, so **git is the history**. There's no hand-maintained changelog and no per-item "changed in vX" markers.
- Each republish follows a commit. The page footer shows `updated <date> · <short commit>`.
- The collapsed "Changelog" and "Change track" sections are **generated from `git log`** (for the whole doc, or filtered to an entry's ID). Nothing is written by hand.
- No document status badges (draft/approved…). The work is agile; the user says "go" in chat when ready.

## Foundations (decided 2026-11-15)

### Project folder & data
- Data lives **in the project's own repo** at `docs/artifact/`, next to the code that Tab 4 reads and versioned with it. Projects without a repo use `<claude-space>/projects/<name>/artifact/` with its own `git init`.
- Layout:
  ```
  docs/artifact/
    intent.md            # EN source, Markdown
    registry.json        # components (id, name, c4_level, status)
    usecases.json        # UCs (id, tier, summary, flows, touchpoints)
    decisions.json       # decision groups + options (chosen/deferred/rejected + reason)
    concepts.json
    i18n/th.json         # TH content keyed by ID (+ source hash)
    diagrams/            # diagram-design outputs (EN only)
    artifacts.json       # tab → artifact slug + live version (for republish & cross-links)
  ```
- JSON for anything the script reads; Markdown for prose (intent).

### Feedback loop: built-in, nothing to build
- The user gives feedback **in chat or in artifact comments**. Claude reads the comment, updates the data, republishes and replies in the thread.
- Comments only work while pages are hosted as claude.ai artifacts. Chat works anywhere.

### Traceability check (in the sync script)
- Validates the chain **outcome → UC → decision → component → files** and fails or warns on orphans: a UC with no sequence diagram, a component change with no reason or UC, a decision linked to no UC, an ID used but missing from the registry, a missing or stale TH translation, a CRUD cell with no UC and no "n/a (why)".

### Naming convention
- Each tab is its own artifact, so titles must tell them apart in claude.ai lists: `<Project> · <n> <Tab>`, e.g. `Status Board · 0 Intent`, `Status Board · 2 Decisions`. The number keeps them sorted. No version in the title (git has it).

### Sharing (warn every time)
- Pages start private. Before the user shares, remind them: **Tab 4 and Tab 5 contain real code details** (flows, schemas, file paths). Check for secrets or internal hostnames first. Sharing is done in claude.ai, not by Claude.

### Export
- **One combined PDF**: intent + all existing tabs in order, with a cover page, generated locally from the same templates in print mode. Toggles get expanded: bad paths and edge cases shown, collapsed sections opened.

### End of life (user's choice, not automatic)
- When the user says a project is done, **offer** to (a) write an `[archive]` note to Obsidian and (b) record which production signal would start a new intent. Do each only if the user wants it.

## Decided (2026-11-15)
- **Component levels = C4 L1–L3**: L1 Context, L2 Container (apps, APIs, DBs, queues, functions), L3 Component (modules inside a container). L4 Code is skipped; Tab 5 file lists cover it. Sequence diagrams use L2 participants, and detail diagrams drop to L3.
- **Tab 4 source labels**: each diagram is marked `from code` / `draft (new code)` / `mixed`. Existing flows (especially Affected UCs) are **always read from current code**.
- **One artifact per tab**, linked. The intent page is the hub, with links to all 5 tabs and back-links from each.
- **Single source of truth**: a local project folder holds the data (registry, use cases, decisions, changelog, intent). There's one HTML template per tab. A sync script validates cross-tab consistency (IDs exist, levels match; full traceability check below), renders only the changed tabs, and publishes them.
- Not every project needs all 5 tabs every time, but all 5 are the standard for development work.
- **Tabs not created yet are shown disabled, not hidden.** The header is a sticky tab bar: numbered tabs 0–5 (Intent grouped apart from the Solution tabs), the current tab filled with the accent and `aria-current`, hover/focus states, and a horizontally scrolling row on phones. A missing tab is greyed with a lock and a tooltip "Not created yet", not clickable and not struck through. The nav therefore looks the same on every page and every project.

## Resolved design points (v0.1 build)
- Data format: JSON for anything the script reads, Markdown for the intent. Location: `docs/artifact/`.
- Template build: plain HTML templates with `{{slot}}` / `{{ui:key}}` placeholders and `<!--part:name-->` blocks, filled by `scripts/artifact_sync.py` (stdlib only). Theme tokens live in `themes/`, structure CSS in `templates/base.css`.
