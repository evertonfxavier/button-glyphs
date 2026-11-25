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
   ├─ files_level    files_patch.py           tiny loose files, atomic, no marker (Songs for a Hero)
   ├─ overlay_level  overlay_patch.py         ADD one archive/files that the game loads last (Serious Sam 2, Serious Sam HD)
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
| `files_level` | `XnbPictureProfile` | many tiny loose files (XNB textures of 0.7-2.7 KB) with no spare byte for a marker | the whole original file, each (`backups/<appid>/files/`), verified by SHA-256 | none: state is read from SHA-256 (original / patched / stale / orphan / unknown) | Songs for a Hero |
| `overlay_level` | `OverlayGlyphProfile` | the engine mounts every archive of the game folder in sorted order and a later entry of the same name wins (Croteam `.gro`) | none: no game file is ever written, so there is nothing to back up (`backups/<appid>/overlay/` holds only the ledger and a folder journal) | none: state is read from SHA-256 of OUR added file(s) (original = absent / patched / stale / unknown) | Serious Sam 2, Serious Sam HD (First and Second Encounter) |
| `config_level` | `ConfigValueProfile` | the game already has both sets and stores the choice as one value in a save/config file (outside the game folder) | whole save, once, before the first change (`backups/<appid>/<file>.orig`) | the value itself; the value found before our first change is recorded in config (`original_value`) | 10 Second Ninja X |

### Profile contracts (what the engine calls)
- Common attributes: `appid`, `name`, `kind`, `file` (relative to the install dir), `roles`
  `[(role, label)]` (sent to the UI, no longer drawn there), `revision`, `notes`, `original_hint`, `support`
  (`"full"` when the game's page says *confirmed*, else `"partial"`; the UI's dot colour, kept in step with
  `docs/games/<appid>-*.md` by `tests/test_ui_i18n.py`), and the
  styles it supports (`styles` dict for icon sets, `patch_styles` for region profiles).
- **whole-file**: `check(path) -> [problems]` on an original file; `patch(path, style, load_icon)`
  modifies a copy in place.
- **entry-level**: `targets()` / `targets_for(style)` (each `{file, entry, textures, styles?}`),
  `check_target(path, target)`, `patch_payload(payload, target, style, load_icon) -> bytes`.
- **region-level**: `regions` (`{key, offset, size, sha256, ...}`), `native_style`,
  `render(original_bytes, region, style) -> bytes` of the same size.
- **files-level**: `files` (`{key, file, size, header, sha256, ...}`, `file` relative to the install dir;
  `file` of the profile is just the folder), `native_style`, `patch_styles`,
  `render(original_bytes, entry, style) -> bytes` (same length, same first `header` bytes) and
  `preflight()` (the bundled art loads and has the right size, or a clear error before any write).
- **overlay-level**: `entries` (`{key, path, size, sha256, gro, ...}`: the originals it replaces, pinned), `mount`, `archive`,
  `vehicle` (`"gro"` = one added archive, `"loose"` = the same entries as loose files), `native_style`, `patch_styles`,
  `outputs(style, originals) -> {path: bytes}` (each proven to decode to the intended picture) and `preflight()`. Per-entry
  builders (`RoundSymbol`, `WholePicture`) live in `profiles/overlay_glyphs.py`; another Croteam game is a table plus builders.
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
- **Entry-level write order**: trailer (at its final place, one write that extends the file) → blob →
  index slot → name-record size, each fsynced. Undo: slot back → name size back → truncate.
  States found by `entries.inspect` (it reads both the slot and the name size): `original`, `patched`
  (both new), `partial` (both original: crashed before the index moved), `half` (a cut between the two
  fields: every field is one of our recorded values but they disagree; `revert` is total and repeats safely),
  `strange` (a field or the size is not one of ours: a game update; only our marker is cut), `legacy`
  (v0.1.x whole-file trailer, migrated from its backup).
- **Region markers**: `W` (writing) / `P` (patched) + style + revision + SHA-256[:16] of the
  original region. Write order: marker `W` → body → final header.
- **Loose files** (no marker fits, so SHA-256 is the state): per file `original` (= pinned hash),
  `patched` (= the render of its backed-up original with today's art), `stale` (an older drawing of ours: its hash is
  in the **render ledger** `backups/<appid>/files/renders.log`, it keeps the original's size and header and a verified
  backup exists; rebuilt **from the backup**), `orphan` (ours, but no verified backup: error), `unknown` (anything else,
  e.g. a game update that changed only pixels: a style is refused with nothing written; Original leaves it alone and the
  status says "N of M files are not the supported build": an error if the plugin handled the game before, a notice
  otherwise) and `missing`. The ledger (one `<sha256> <revision>` line per render, atomic
  rewrite, de-duplicated, capped, torn lines ignored) is written before the first file of an apply; if it is lost, old
  drawings become `unknown` (safe side). Order: check every file -> art preflight -> whole-file backups (temp + fsync +
  rename, read back) -> render everything -> ledger -> replace each file atomically (temp in the same folder, fsync,
  `os.replace`, fsync of the folder, read back). Undo: verified backups back in reverse order, each checked against the
  pinned hash. A crash leaves every file whole (original or drawn); the next sync finishes, or Original restores. An
  Original chosen while the game ran is finished by `sync_all` (`_files_restore_pending`).
- **Add-file overlay** (nothing of the game is written, so exact undo = delete our file): `overlay_patch.plan` classifies every
  file the vehicle writes as `missing` (original), `patched` (= today's output, built from the game's own pinned originals),
  `stale` (its hash is in the ledger `backups/<appid>/overlay/renders.log`: an older drawing, rebuilt, never edited), or
  `unknown` (a file with our name that we did not write, also a folder/symlink/oversize file, also a loose file that would
  shadow an entry: a style is refused with nothing written, Original leaves it alone and says `foreign_file`). Files of the
  vehicle NOT in use are checked the same way (a vehicle switch removes our old artefact last). Reads: the `.gro` folder is
  listed, every archive except ours is opened once (central directories only), each pinned entry is read at most `size` bytes
  from the archive that wins it and SHA-256 checked; any difference is an unknown build (nothing written). Order: plan ->
  art preflight -> build and prove every output in memory (decode back, zip CRC) -> room -> ledger (fsynced) -> loose only:
  folder journal `dirs.log` then folders -> each file atomically (temp + fsync + `os.replace` + fsync of the folder, read
  back). Undo: remove our files in reverse order (each re-hashed first), then the journaled folders that are empty. A cut
  leaves a prefix ("half"): the next sync finishes or Original removes it. Original reads nothing from the game (a game
  update never blocks it); with a lost ledger a file equal to today's output is still recognised as ours. The watcher
  (`_overlay_restore_pending`) reacts only to files whose hash is in the ledger or to our empty journaled folders.
- **Overlay: an update, ownership, waiting**: when the pins no longer match (a game update) and a file of ours is PROVEN ours
  (ledger / build record), the sync removes it by the Original path (hash re-checked, reverse order, crash-safe) and reports
  `unsupported_build` saying so: built from the old originals and loaded last it would keep overriding the new ones; a file we
  cannot prove ours is left alone, and a plain read failure removes nothing. As root (Decky) every file and folder we create in
  the game folder is handed to the game folder's owner with a neighbouring file's mode, on the TEMP file before the rename
  (`files_patch.write_atomic(prepare=...)`); the ledger lives in the plugin's own folder and is not touched. The watcher waits 10
  minutes after ANY failed apply (build, full disk, OSError); the room is checked from the pinned sizes before anything is built.
- **Overlay status is cheap**: `overlay_patch.plan` never builds. For a style it checks the central directories of the game's `.gro` files
  (size + CRC-32 + winning archive of every pinned entry; parsed once per archive, cached while (size, mtime, ctime, inode) are
  unchanged), computes the build **fingerprint** (SHA-256 of the pins, the parameters of every builder, the bytes of the bundled
  pictures, the revision and the source of the drawing / encoding / writing code) and compares our file's SHA-256 with
  `backups/<appid>/overlay/builds.log` (`<fingerprint> <style> <sha256>`, written with the ledger BEFORE the first write). A match is
  `patched`; in the ledger without a match is `stale`; neither is `unknown`. Anything that cannot be proven cheaply (an unreadable
  picture, a lost record) is stale or unknown, never patched. The full SHA-256 check of the originals, the in-memory build and the
  decode-back proof (`build_plan`) run only in the apply path and when a file with our name needs adopting (the watcher tries that
  once: a failed build leaves the error and `overlay_retry_after`, so the watcher waits 10 minutes; an explicit choice retries at
  once; a cut or a full disk during the WRITE is retried by the next tick). HD games (DXT encode of a 512x512 pad and a 12 MB atlas
  cost seconds) therefore cost milliseconds per status poll.
- **Entry, region and single-file games set to Original** are also finished by `sync_all` when a file still carries OUR
  marker (`_marker_restore_pending`: entries patched/half/partial, regions patched/writing, our trailer): a power cut
  inside Original or a game that was running then. Unknown, strange or missing files never trigger it; an idle tick
  writes nothing.
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
  "Running" means a command line holds the install path under any of its names (as Steam reports it, its realpath, the
  `/run/media/<user>/<label>` and `/run/media/<label>` aliases of the same folder, each also as `Z:\...`) or the launcher's
  `AppId=<appid>` token (`steam.is_running`); it can only see more than a plain substring test, never less.
- Status precedence: running > updating > error > pending > ok. Errors are kept in the game's
  config entry until the next successful sync.
- `profile.revision` is recorded in every marker; a bumped revision re-applies automatically
  (loose files have no marker: a changed render is found by content and rebuilt, the revision is informational).
- Config (`config.json`): `games[appid] = {style, last_event, error?, applied_buildid?, backup?}`; config-value games also keep
  `original_value`, `written_value`, `intended_value` and `verified_buildid` + `verified_stamp` (the build and the size/mtime of the file whose hash was already checked; never re-hashed every minute).

## Paths
| Where | Path |
|-------|------|
| Plugin on the Deck | `~/homebrew/plugins/Button Glyphs/` (root-owned) |
| Settings / backups | `~/homebrew/settings/Button Glyphs/config.json`, `~/homebrew/data/Button Glyphs/backups/<appid>/` |
| Logs | `~/homebrew/logs/Button Glyphs/*.log` |
| Steam libraries | `~/.local/share/Steam/steamapps/{common,compatdata}` (+ SD / extra drives via `libraryfolders.vdf`) |

## Verification kit
`tools/verify_profile.py <appid> <dir of real copies>` knows the overlay (`OverlayKind` in `tools/verify_worlds.py`): `<dir>` holds the
game's root `.gro` archives (or the install-relative tree); worlds clone them; the units are the added file(s) (and the folders the
loose vehicle creates); the whole-tree map hashes each archive once per world. Probes: cycle (originals byte-identical, our file gone),
power cuts as an exception and as a hard `os._exit` at write / replace / **remove** events, running guard, Steam verify (our file
deleted), new build, unknown build (a bit flipped inside a pinned entry's data), foreign file / link / folder / 3 GB sparse file with our
name (and a loose file that would hide an entry), damaged art, ledger and build-record loss, timings (including `status` and the idle
tick), visual sheets. `tools/clone_validate.py` and `tools/deck_probe.py` (presence of the game's archives only) support it too.

## Adding a strategy
Only when no existing one fits (see skill `choosing-patch-strategy`): add a profile class with
the same attributes, give the engine a flag and a `_status_*` / `_sync_*` pair that keeps the
invariants of skill `patch-safety-invariants`, add synthetic-fixture tests, and document the
layout here.

## UI (`src/`)
One panel, never replaced by a loading screen: an inline status line (only when something needs attention), a **legend** of
the dots, the **game select** (a `DropdownItem` whose options are every installed game, supported first, each with its dot; the
menu's own focus highlight shows which game the cursor is on), then for the selected game the **style select** with its status
as description and the last change, or a neutral "no support yet" note (tone is only the dot; no coloured borders); then **updates** and **details**. The default current game
is the running one if supported (`Router.MainRunningAppID`), else the last one used, else the first supported.
- **Dots**: green = `support: "full"`, yellow = `"partial"`, red = the game has a profile but its `state` is `error`
  (for example an unsupported build), hollow grey = no profile, blue = no profile but the game has its own Xbox/PS option (`support: "ingame"`, from
  `button_glyphs/ingame_games.json`, optional `how` line; the plugin only lists it). Never colour alone: the legend and each dot's label carry it.
- **Language**: `src/i18n/{en,pt-BR,es}.json`, `t(key, vars)` with English fallback. Resolved once per mount from Steam
  (`LocalizationManager.m_rgLocalesToUse[0]`, then `SteamClient.Settings.GetCurrentLanguage()`), then `navigator.language`;
  Steam names (`brazilian`, `latam`, `spanish`) and any `pt*` / `es*` map to pt-BR / es, everything else to English.
- **Status text**: the engine adds a stable `code` (+ `args`) next to its English `message` (list: `status_codes.py`); the UI
  translates `code.<code>`. The stored last-change sentence is recognised by shape (`eventRef` in `src/lib/messages.ts`).
- The games list is cached at module level, so reopening the panel renders at once and refreshes behind it. Applying shows a
  spinner and a short line under the select (select disabled) and a toast when done; errors show inline in red.
