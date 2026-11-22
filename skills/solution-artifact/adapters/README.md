# Publish adapters

The skill renders ONE page, `<dir>/build/index.html` (inline CSS/JS/SVG; each adapter file states which `render --fonts` mode to use). An **adapter** puts that file somewhere a person can open it and returns where. Everything runtime- or host-specific lives in an adapter file; `SKILL.md` stays neutral.

## Contract
- **Input:** the path to `build/index.html`, a title (the project name), and the project's artifact directory.
- **Output:** `{adapter, id, url}`: the adapter name, a stable id used to update the same page next time, and a URL a person can open.
- **Recorded in `artifacts.json`** (no version field; adapters that need one look it up at publish time):
  ```json
  {"project": "Status Board", "adapter": "local",
   "publish": {"local": {"id": "build/index.html", "url": "file:///abs/path/build/index.html"}}}
  ```
  `adapter` is the one chosen last; `publish` keeps one entry per adapter ever used. Entries under the legacy `tabs` key are never touched.
- An adapter never changes sharing settings and never publishes a file it has not read.

## Selection order
1. `artifacts.json` `"adapter"`, if set.
2. The first hosting adapter whose detection rule is met (each adapter file states its own; the known ones are listed under Runtime notes).
3. `local`.
Tell the user which adapter was used.

## Add an adapter
1. Create `adapters/<name>.md` with: when it applies (how to detect it), the publish steps for a new page, the update steps for an existing `id`, what `id` and `url` mean, and any sharing or privacy warning.
2. Add it to the table above and, if it should be auto-selected, to the selection order (before `local`).
3. Keep host-specific words inside that file. The adapter must return `{adapter, id, url}` and the agent records it as above.

## Runtime notes
| Name | File | Applies when | Hosting |
|---|---|---|---|
| `claude-ai` | `claude-ai.md` | the `claude-artifacts` tools are available (selected before `local`) | private claude.ai artifact page |
| `local` | `local.md` | always (fallback) | none; open the file on disk |
