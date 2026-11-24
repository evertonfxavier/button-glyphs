# Assassin's Creed (2008, Director's Cut) - 15100

## At a glance
| | |
|--|--|
| Install dir | `steamapps/common/Assassins Creed` (Proton 11.0-100 on the Deck) |
| Build verified | buildid 252091; `AssassinsCreed_Dx9.exe` MD5 `8e72c333...`, `_Dx10.exe` `ca877532...` |
| Engine / format | Ubisoft Scimitar, `.forge` v25: LZO1X chunks (Adler-32 initial value 0), TextureMap DXT3 |
| Strategy / profile | entry-level, `profiles/ac1.py` (`ForgeGlyphProfile` + `extra_targets`), revision 3 |
| Styles | PlayStation, Xbox, Original |
| Status | font glyphs confirmed in game; menu `Conrtols_*` redraw verified offline only |
| Summary | The four action glyphs (head, weapon hand, empty hand, legs) in the HUD, tutorials and menus become △ □ ○ ✕ (PlayStation) or Y X B A (Xbox). PlayStation also redraws the Xbox 360 button art of the menus and the Animus room: A/B/X/Y → ✕ ○ □ △, LT/RT → L2/R2 and the L/R sticks. |
| Support level | partly confirmed |

## Where the button art lives
The game has no letter prompts for the action buttons: it draws **four body-part glyphs** in
Xbox 360 colours - head (eye, = Y), weapon hand (fist, = X), empty hand (hand, = B), legs
(feet, = A). They are characters of the game's fonts, so every text that uses them changes.

`DataPC.forge`, entry `Game Bootstrap Settings` (all DXT3, no mips, rows stored **bottom-up**):
| id | texture | size | note |
|----|---------|------|------|
| `0x4719595F` | AnimusText Bold_PC tga32_Map | 256x512 | coloured glyphs |
| `0x47195956` | AnimusText PC tga32_Map | 256x256 | coloured glyphs |
| `0x47195991` | AnimusTechno Regular 22_PC tga32_Map | 64x1024 | white glyphs (tinted white) |
| `0x1DEA45F6` | ControlsHUD_ButtonA_2_Map | 64x64 | HUD "A" disc |

Xbox 360 button art ("Conrtols" is the game's own spelling), 32x32 DXT3 unless noted, one copy
per area archive: `Conrtols_{A,B,X,Y}_Map` ids `0x1DEA5162 / 5168 / 517A / 5180` (white) and
`Conrtols_{A,B,X,Y}_Color_Map` `0x1DEA55D2 / 55C4 / 55CA / 55B8` (coloured, absent in
`Present_Room`); only `Present_Room` has `Conrtols_{Left,Right}Joystick_Map` `0x1DEA5544 / 554A`
(32x32) and `Conrtols_{Left,Right}Trigger_Map` `0x1DEA5556 / 5550` (32x64).
Area archives and the entry holding them (`CellNNNNN_DataBlock`, 2-34 MB each, ~164 MB total):
Acre, Damascus, Jerusalem, Assassination_Talal `Cell01364`; Arsuf `Cell21844`;
Kingdom, Masyaf `Cell05460`; Flashback_Paradise, SolomonTemple `Cell00340`;
Memory_Corridor, Present_Room `Cell00084`.

## What the profile changes (and what it leaves alone)
- **PlayStation**: glyphs become triangle / square / circle / cross (outline icons in PS colours
  on the coloured atlases, white versions on the white atlas); `Conrtols_A/B/X/Y` become
  cross / circle / square / triangle (white and coloured variants), sticks `L`/`R`, triggers `L2`/`R2`.
- **Xbox**: only the four body-part glyphs are redrawn as Y/X/B/A discs; `Conrtols_*` already are Xbox.
- Untouched: `Conrtols_Back_Map` (plain disc, use unknown), `Quick_Inventory_*`,
  `ControlsHUD_Splitter*`, ~110 `AFS_Tut_*` tutorial photos (no baked buttons in the six sampled).
- Roles: `head`, `weapon`, `empty`, `legs` (+ `a b x y ls rs lt rt` for the area textures).

## How it is verified
`check_target` per file: the entry exists, each texture id has the expected name/size/format
(`verify: name` for the single-glyph textures; coloured glyphs must be present in the font boxes).
Tests: `tests/test_entries.py` (synthetic forges). Clone validation (12 files, 5.6 GB, APFS
clone): ps -> xbox -> ps -> original left every file byte-identical to Steam's; PS took 4.7 s
and +181 MB; style switches never grow the files.

## Crash safety
Found by the power-cut probe of `tools/verify_profile.py`; fixed without changing what is written to disk
(a patched file is byte-identical to before, so files made by older plugin versions keep working and `revision`
stays 3).
- **Window that existed (old code)**: the slot and the size kept in the entry's name record are two writes, far
  apart in the file. A cut between them read as `patched` (apply) or `partial` (Original) because only the slot was
  looked at; nothing repaired the name size, so Original was not exact. **Fixed** (below).
- **Now**: apply writes the trailer first, at its final place, in one write that also extends the file (the gap reads
  as zeros), then the blob, then the slot, then the name size, each fsynced. `entries.inspect` reads both fields
  and every combination of our own recorded old/new values is a known state: all new = `patched`, all original =
  `partial`, anything in between = `half` (what the old code could leave too). `half`, `partial` and `patched` are all
  undone by the same total `revert` (slot, then name size, each only if not already original, then truncate to
  `orig_size`), so a cut inside a repair converges. A value that is not ours in either field, or a file of another
  size, is still `strange` (a game update): only our marker is removed, nothing is guessed.
- **The watcher finishes a cut Original**: `Engine.sync_all` also syncs a game whose config says Original while a
  file is `patched`, `half` or `partial` (`_marker_restore_pending`); `strange`, unknown or missing files never
  trigger it, an idle tick writes nothing and a running or updating game is left alone.
- Tests: `tests/test_entries_crash.py` (a cut at every write of apply and of Original, a second cut while repairing,
  both windows by hand, old-format bytes, `strange` unchanged, fsync between slot and name size),
  `tests/test_watcher_original.py` (a watcher tick finishes a cut Original) and `tests/test_verify_profile.py`
  (probe in full mode). The "second cut while repairing" test covers only the apply cut at write 4 (the half state);
  a review replayed a second cut at every write and every 512-byte tear of the repair.

**Known limits (present, NOT fixed)**
1. A Deck where the OLD code was cut after the blob and before the trailer keeps a junk tail: the file is longer
   than Steam's and reads `original` (no trailer). Compare the forge sizes with Steam's (`/game-status`); Steam
   "Verify integrity of game files" restores them. The new code never creates this state.
2. The ~2 KB trailer write is assumed atomic (as the crash probe models it, `SECTOR` = 4096). If a disk tears it,
   the file is left longer with an unreadable or zero trailer and reads `original`; a later apply then records the
   bloated size as `orig_size`, so Original stops being exact (it returns that bloated size) until Steam "Verify
   integrity of game files". The full fix (record each file's `orig_size` in the config before the first write) is
   NOT done.

## Verified / not verified
- Verified: glyph swap in game (user: "aparece os botoes la"), exact undo, migration from the
  v0.1.x whole-file patch, upright orientation of all new icons in renders.
- NOT verified: where each `Conrtols_*` texture appears in game and whether the trigger icons
  (square L2/R2 in a 32x64 texture) look right; whether LB/RB prompts exist (none found).

## Deck state
Xbox active (plugin v0.3.1, trailer revision 3). No backup copies: originals stay inside the files.

## Open questions and caveats
- Input layer (not prompts): **EaglePatchAC1** (github Sergeanur/EaglePatch) adds XInput, hotplug
  and `PS3Controls`; both Steam exes match its supported hashes; install = Ultimate ASI Loader
  `dinput8.dll` + `scripts/EaglePatchAC1.{asi,ini}`. It has **no license** (do not bundle) and
  needs a test for the Proton 11 `dinput8` override. Not integrated.
- Help/controls screens may carry baked controller pictures; not examined.

## How to re-derive the data
Parse with `py_modules/button_glyphs/forge.py`; bulk scans use `tools/forge_scan.py` and
`tools/forge_render.py` (need the Deck's forge files copied to the Mac; native LZO via
`lzallright` in a venv). Lessons so far are in `CLAUDE.md`.

## Learnings
- forge set_slot writes the index slot and the name-record size as two separate writes: a power cut between them leaves a size the engine does not repair (apply: looks PATCHED; restore: PARTIAL truncates but never rewrites the name size, so Original is not byte-exact). _(verify_profile crash probe, 2026-11-24)_
