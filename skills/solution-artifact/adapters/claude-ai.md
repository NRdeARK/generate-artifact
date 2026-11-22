# Adapter: claude-ai

Publishes `build/index.html` as ONE private claude.ai artifact page through the `claude-artifacts` tools. This is the only place those tool steps live.

- **Applies:** the `claude-artifacts` tools (`publish_files`, `get_artifact`, `guide`, ...) are available in this session.
- **Render with:** `python scripts/artifact_sync.py render <dir> --fonts google`. The theme then loads Source Serif 4, Inter, IBM Plex Sans Thai and Noto Serif Thai from `fonts.googleapis.com` (the claude.ai artifact rules allow it and `fonts.gstatic.com`); no font files are uploaded. Offline or Google blocked: the page falls back to system fonts (Georgia / Segoe UI, Leelawadee UI / Tahoma for Thai). No other outside link is allowed.
- **Title:** the project name, e.g. `Status Board` (no tab number, no version; git has the history).
- **Once per session, before the first publish:** call `guide` with topic `pages`.

## New page (no `publish.claude-ai.id` yet)
`publish_files` with `files: [{as: "index.html", path: <absolute path of build/index.html>}]`, `title`, and `icon`. The result carries the artifact slug and URL.

## Update (`publish.claude-ai.id` is the slug)
1. `get_artifact` with the slug to read the live `base_version` and its `read` mode.
2. `publish_files` with the same slug, `base_version`, the same file mapping, and `shared_live` set to the `read` value when it is `users`, `org` or `public`.

## Record
`"adapter": "claude-ai"`, `"publish": {"claude-ai": {"id": "<slug>", "url": "<https url>"}}`. No version field. The URL must be `https://claude.ai/...` exactly; anything else is rejected by `validate` (`bad-url`) and must not be linked.

## Sharing
Pages start private. **Sharing is done only by the user in the claude.ai UI**; the agent cannot and must not change it. Before the user shares, warn every time: the one page contains **Tab 4 and Tab 5 code details** (flows, schemas, file paths), so check for secrets and internal hostnames first.

## Generic publish (a one-off page, not a project page)
Call `guide` with `intent: "slides" | "design" | "other"`. If it names a type, use `create_from_type`; otherwise read `guide` topics `pages` and `design`, build one self-contained page, and publish it with `publish_version` (new: `title`, `icon`, `content`). Diagrams via `diagram-design`, charts via `dataviz`. No secrets or personal data unless asked.

## Never
- Delete an artifact unless the user asks (`delete_artifact`; the user confirms).
- Publish a file you have not read, or a page that impersonates a real person, company or brand.
- Touch old per-tab artifacts listed under `tabs`: tell the user they were left as-is and can be deleted manually.
- **PDF:** `export-pdf <dir> --lang en|th|both --fonts google` (Google Fonts, needs network in the headless browser; falls back to system fonts offline). Ask the user which language first.
