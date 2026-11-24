# Architecture

```
Decky UI (src/, React)                      Steam Deck, Game Mode
   │  callable("list_games" | "set_style" | "recheck" | "ping")
   ▼
main.py ── Plugin ── watcher (every 60 s: Engine.sync_all("watch"))
   ▼
Engine (engine.py)  ── config.json (homebrew/settings/Button Glyphs)
   │   dispatch on the profile's flags
   ├─ default        whole-file strategy      backup of the file + trailer      (IlPatchProfile, simple ForgeGlyphProfile)
   ├─ entry_level    entries.py               append new entry, repoint slot    (AC1)
   ├─ region_level   region_patch.py          in-place regions + backups        (Quantum Break, Saint Seiya)
   └─ config_level   engine._sync_config      one value in a save/config file   (10 Second Ninja X)
   ▼
Profile (profiles/<game>.py)  = pure knowledge of one game: where the art is, how to redraw it
```

The engine owns *state, safety and timing*; a profile owns *knowledge of one game*. A profile
never opens a backup, writes a marker or decides when to run.

## Strategies

| Flag | Profile class | File layout it fits | Backup | Marker (state in the file) | Games |
|------|---------------|---------------------|--------|----------------------------|-------|
| default | `IlPatchProfile` | game code picks the art; one constant to change | whole file (small) | trailer at the end of the file | Charlie Murder |
| default | `ForgeGlyphProfile` (single target) | forge archive that can be copied | whole file | trailer | (v0.1.x AC1, kept for compatibility) |
| `entry_level` | `ForgeGlyphProfile` with `extra_targets` | many/big forge archives, movable entries | none: the original entry stays in the file | trailer (`kind: entries`) with the original index slots | Assassin's Creed |
| `region_level` | `RegionGlyphProfile` | raw BGRA textures at fixed offsets in a giant file | only the changed regions (`backups/<appid>/regions/`) | 44-byte marker in the DDS reserved bytes | Quantum Break |
| `region_level` | `DxtRegionProfile` | DXT5 DDS files at fixed offsets (CPK/EMB) | only the changed regions | same DDS marker | Saint Seiya |
| `config_level` | `ConfigValueProfile` | the game already has both sets and stores the choice as one value in a save/config file (outside the game folder) | whole save, once, before the first change (`backups/<appid>/<file>.orig`) | the value itself; the value found before our first change is recorded in config (`original_value`) | 10 Second Ninja X |

### Profile contracts (what the engine calls)
- Common attributes: `appid`, `name`, `kind`, `file` (relative to the install dir), `roles`
  `[(role, label)]` (shown under the preview), `revision`, `notes`, `original_hint`, and the
  styles it supports (`styles` dict for icon sets, `patch_styles` for region profiles).
- **whole-file**: `check(path) -> [problems]` on an original file; `patch(path, style, load_icon)`
  modifies a copy in place.
- **entry-level**: `targets()` / `targets_for(style)` (each `{file, entry, textures, styles?}`),
  `check_target(path, target)`, `patch_payload(payload, target, style, load_icon) -> bytes`.
- **region-level**: `regions` (`{key, offset, size, sha256, ...}`), `native_style`,
  `render(original_bytes, region, style) -> bytes` of the same size.
- **config-level**: `values` (`{style: text}`), `find_saves(libraries)` (the Proton prefix is searched
  in every Steam library), `locate(data) -> (offset, value)` (raises `ConfigValueError` unless the
  key is exactly once, in its section, in the expected number format), `style_of(value)`,
  `valid_value(value)`, `check_build(buildid, install_path, full)` (pinned build id + file size + SHA-256).
- Texture spec keys (forge): `id`, `name`, `label`, `width`, `height`, `format`, `boxes`, `icons`,
  `flip_y`, `tint`, `clear_all`, `verify` ("glyphs" | "name"). See the `forge_glyphs.py` docstring.

## State and recovery
- A patched file says so itself, so Steam updates and "Verify integrity" (which restore whole
  chunks and therefore remove our marker) are detected without trusting the config.
- **Trailer**: last 2048 bytes of the file, magic `BUTTONGLYPHS\x01` + JSON. Entry-level trailers
  hold `orig_size`, `patched_size` and, per entry, the original and new index slot.
- **Entry-level write order**: blob → trailer → index slot. Undo: slot back → truncate.
  States found by `entries.inspect`: `original`, `patched`, `partial` (crashed before the slot
  moved), `strange` (file no longer matches the trailer: only our marker is cut), `legacy`
  (v0.1.x whole-file trailer, migrated from its backup).
- **Region markers**: `W` (writing) / `P` (patched) + style + revision + SHA-256[:16] of the
  original region. Write order: marker `W` → body → final header.
- **Config value** (user data the game rewrites, so no whole-file hash): checks (one save, layout, pinned
  build) -> copy of the save, first change only -> durable record (`original_value`, `intended_value`) in
  `config.json` and `backups/<appid>/*.record.json`, both fsynced -> one in-place same-length write +
  fsync -> read back -> `written_value`. `Original` writes the recorded value back only if the file still
  holds a value we wrote, and forgets the record; any other value (the player's own choice) is kept. A game set to
  Original with a recorded value still pending is finished by `sync_all`; one without is never touched.
- **All-or-nothing across files**: undo what must change, check every file while original, then
  patch. A failed check leaves the game original.

## Engine behaviour worth knowing
- `sync` does nothing while the game runs (`pgrep`-style scan of `/proc`) or while Steam
  updates it (`StateFlags != 4`); the watcher retries every minute.
- Status precedence: running > updating > error > pending > ok. Errors are kept in the game's
  config entry until the next successful sync.
- `profile.revision` is recorded in every marker; a bumped revision re-applies automatically.
- Config (`config.json`): `games[appid] = {style, last_event, error?, applied_buildid?, backup?}`; config-value games also keep
  `original_value`, `written_value`, `intended_value` and `verified_buildid` + `verified_stamp` (the build and the size/mtime of the file whose hash was already checked; never re-hashed every minute).

## Paths
| Where | Path |
|-------|------|
| Plugin on the Deck | `~/homebrew/plugins/Button Glyphs/` (root-owned) |
| Settings / backups | `~/homebrew/settings/Button Glyphs/config.json`, `~/homebrew/data/Button Glyphs/backups/<appid>/` |
| Logs | `~/homebrew/logs/Button Glyphs/*.log` |
| Steam libraries | `~/.local/share/Steam/steamapps/{common,compatdata}` (+ SD / extra drives via `libraryfolders.vdf`) |

## Adding a strategy
Only when no existing one fits (see skill `choosing-patch-strategy`): add a profile class with
the same attributes, give the engine a flag and a `_status_*` / `_sync_*` pair that keeps the
invariants of skill `patch-safety-invariants`, add synthetic-fixture tests, and document the
layout here.
