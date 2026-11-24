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
