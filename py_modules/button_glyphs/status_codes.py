"""Stable codes the engine puts next to a status `message` so the UI can translate it.

The engine keeps returning an English `message` (logs, older UIs); `code` (+ `args`) is additive.
The UI looks `code.<code>` up in src/i18n/*.json and falls back to `message` for a code it does not know.
tests/test_ui_i18n.py keeps this list, the codes engine.py emits and the three catalogs in step.
"""
CODES = (
    "file_missing",       # args: file
    "running",
    "updating",
    "will_apply",         # args: style (original / ps / xbox)
    "will_improve",       # args: style
    "will_restore",       # args: style
    "active",             # args: style
    "cannot_read",        # args: file, reason
    "unsupported_build",  # args: detail
    "start_game_once",
    "original_kept",
    "foreign_file",       # args: file (a file with the overlay's name that this plugin did not write)
)
