# Intent artifact (spec v0.1)

Concept from the AI-native SDLC playbook: **intent.md**, a short, version-controlled statement of *what is wanted, why, and under which constraints*. It is the input to the solution artifact (requirements + design spec).

## Principles
- **Short.** One screen of text. Not a PRD.
- **Not rigid.** Only the core sections are required; add others only when they earn their place.
- **Visual first.** The page opens with a diagram preview, and the text follows.

## Page layout (top → bottom)
1. **Visual preview**, chosen by how specific the flow is:
   - **Swimlane**: overview of who/what is involved and the high-level handoffs. Default for early or draft intent.
   - **Sequence diagram**: only when a specific, already-understood flow matters (e.g. an API handshake or an auth flow). Not for draft flows.
   - Built with the `diagram-design` skill.
2. **What**: what is wanted, in 1–3 sentences.
3. **Why**: the motivation and the problem it removes.
4. **Before state**: how things work today (pain, current flow).
5. **Constraints**: budget, time, tools, must/must-not.
6. **Outcome success**: 1–2 lines on what changes when this works. Fuzzy is fine. Not acceptance criteria; those belong in the solution artifact.

No open questions or decisions here. Anything unresolved goes to the solution artifact as **decisions to make**.

## Review gate
The user reviews the intent **as the artifact page** and approves it in chat before any solution artifact is drafted (step mode stops here; see `SKILL.md` section 3). The intent section is published with its Thai text like every other stage.

## Outputs
- The `intent` section of the self-contained page `docs/artifact/build/index.html` (tab `#intent`), published through the selected adapter (see `SKILL.md` and `adapters/`).
- Local `docs/artifact/intent.md` with the same text content, versioned with git (see `solution.md` › Foundations).
