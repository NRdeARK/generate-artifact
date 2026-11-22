# Adapter: local

No hosting. The rendered file stays on disk.

- **Applies:** always available; the fallback of last resort.
- **Publish / update:** nothing to upload.
  - `id`: the relative path `build/index.html`.
  - `url`: the absolute `file://` URI of that file.
  - Both come from `python scripts/artifact_sync.py open <dir> --no-open`, which prints the URI (and errors if `render` has not run).
- **Record:** `"adapter": "local"`, `"publish": {"local": {"id": "build/index.html", "url": "<file:// URI>"}}`.
- **Open it:** `python scripts/artifact_sync.py open <dir>` also starts the default browser.
- **Sharing:** the file is yours to copy or send; it contains Tab 4 and Tab 5 code details, so check for secrets and internal hostnames first.
