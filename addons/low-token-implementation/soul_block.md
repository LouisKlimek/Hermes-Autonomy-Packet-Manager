## Low-Token Implementation

- Keep logs and final reports compact; avoid giant diffs and broad repository archaeology unless required.
- Prefer targeted inspection and focused changes. Do not refactor unrelated code.
- Do not use `/tmp` for scratch or verification files when it is unavailable; use persistent workspace storage such as `/opt/data/hermes-tmp` and remove temporary files when finished.
