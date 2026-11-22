# Theme fonts

Subset woff2 files (Latin for Source Serif 4 and Inter, Thai for IBM Plex Sans Thai and Noto Serif Thai), all SIL OFL 1.1 (`OFL.txt`).
They are a one-time dev artifact; `artifact_sync.py` only reads them (stdlib), never downloads or subsets.

Rebuild: fetch the TTFs from `https://github.com/google/fonts/tree/main/ofl/<family>`, pin variable axes
(`fontTools.varLib.instancer`: Source Serif 4 `opsz=20 wght=500:700`, Inter `opsz=14 wght=400:600`, Noto Serif Thai `wdth=100 wght=500:600`),
then `fontTools.subset` with `flavor=woff2`, layout features kept, to Latin (U+0020-00FF, punctuation, arrows) or Thai (U+0E01-0E5B, ZWSP/ZWNJ/ZWJ, quotes, dotted circle).
Needs `pip install fonttools brotli`.

## How the page uses them
`themes/anthropic.css` has two marked blocks and `render --fonts <mode>` keeps one:
- `google` (claude-ai adapter): `@import` of `fonts.googleapis.com`, same families and weights, nothing copied.
- `local` (local adapter, default): `@font-face` rules with relative `url(fonts/<file>.woff2)`; render copies those files to `build/fonts/`.

Every `url(` / `@import` in a theme must be `fonts/<file>.woff2` directly inside this folder (it must exist) or an https `fonts.googleapis.com` / `fonts.gstatic.com` URL; anything else makes `render` fail.
Fallbacks: offline or Google blocked, or an `index.html` copied without `fonts/`, show the system stacks (Georgia / Segoe UI; Leelawadee UI / Tahoma for Thai). No font data is embedded in the page.
