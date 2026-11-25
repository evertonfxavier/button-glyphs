# Serious Sam 2 (204340)

## At a glance
| | |
|--|--|
| Steam appid / install dir | 204340 / `Serious Sam 2` (14 root `.gro` archives, 3.8 GB on the Deck) |
| Build verified | buildid 24849618 (Core 2.224.00, 2026-PATCH-2-200): the six textures the profile replaces were read from the real archives on the Deck (copies in the scratchpad), their SHA-256 are pinned in the generated `profiles/serious_tables.py` and matched the extracts. Not yet run against the real folder through the engine |
| Engine / format | Croteam Serious Engine 2 (`Sam2.exe`, Windows build under Proton). Content is in `.gro` archives = plain ZIPs (no encryption); a texture is a `.tex` = CTSEMETA container with a zlib block of raw BGRA (`croteam_tex.py`, `gro.py`) |
| Strategy / profile | **add-file overlay** (new): one small archive `zz_ButtonGlyphs.gro` is ADDED to the install folder and wins over the game's own entries; nothing of the game is modified. `profiles/serious_sam_2.py` on `profiles/overlay_glyphs.py` (`OverlayGlyphProfile`, `overlay_level`) + `overlay_patch.py`, revision 2 |
| Styles | PlayStation, Original (= Xbox, the game is natively Xbox) |
| Status | profile written and verified offline (synthetic archives, the real bundled art, renders of the real art on the real originals); not installed, never run in the game |
| Summary | The face-button prompts become PlayStation symbols: A / B / X / Y turn into the cross, circle, square and triangle in their colours, drawn round; the black and white buttons of the original Xbox pad become R1 and L1 (a guess to confirm in game); the controller screen shows a DualShock 3 picture, but its arrows, stick glow and leader lines still follow the Xbox pad (known, see the page). The D-pad and stick pictures and the keyboard keys are left as they are. |
| Support level | unconfirmed |

## Where the button art lives
The engine mounts every root `.gro` of the game folder in **sorted file-name order** and a LATER archive's entry of the same
name replaces an earlier one (the game's own `Sam2.log` / `All.log` list them: `All_PC_01..03`, `Movies_PC_01..02`,
`Patch_02_068`, `Patch_02_080_*`, `Patch_02_090_593613`, `Patch_02_100_800000`). `Sam2.ini` has `fil_bPreferZips = 0`.
The prompts are 128x128 raw-BGRA `.tex` files under `Content/SeriousSam2/Textures/Menu/`, winning layer
`Patch_02_100_800000.gro` (older layers hold 32x32 versions of the same names). The art inside each square canvas is a
**tilted oval** (the original Xbox pad's glossy buttons). The prompts are drawn from `Databases/HudParams.rsc` (inline in the
HUD text) and the controller screen from `Menus/CMSControllerOptions.rsc`.

Measured on the real textures (dev script, alpha > 24; with alpha > 0 the box is one pixel bigger each way, 77 x 94):

| Texture | Canvas | Opaque box (x0,y0,x1,y1) | w x h | Ratio | Margins L / R / T / B | Colour under alpha 0 |
|---------|--------|--------------------------|-------|-------|------------------------|----------------------|
| `A_button` `B_button` `X_button` `Y_button` `Use_button` | 128x128 | (26,17,102,110) | 76 x 93 | 0.82 | 26 / 26 / 17 / 18 | (0,0,0) |
| `White_button` | 128x128 | (26,16,102,110) | 76 x 94 | 0.81 | 26 / 26 / 16 / 18 | (255,255,255) |
| `Dpad_button_up/down/left/right` | 128x128 | (24,24,104,104) | 80 x 80 | 1.00 | 24 each | (0,0,0) |
| `AxesButton` (stick) | 128x128 | (1,3,127,126) | 126 x 123 | 1.02 | 1 / 1 / 3 / 2 | (0,0,0) |
| `controller` | 1024x1024 | (14,149,1007,894) | 993 x 745 | 1.33 | | (0,0,0) |

Is the quad stretched on top of the texture? Not determined: the HUD sizes are properties of `HudParams.rsc`
(`nhep_fButtonStretch` and per-button rectangles) that were not decoded; the 0.82 box is a property of the art, and the round
symbol is a one-line constant (`ROUND_DIAMETER`, `serious_sam_2.py`) to tune after looking in the game.

## What the profile changes (and what it leaves alone)
Added file: `zz_ButtonGlyphs.gro` in the install folder (a stored zip, 0.84 MB, seven entries: six buttons and the 1024x1024 pad picture). It sorts after every archive
of the build, so its entries win. Each entry is the game's own original `.tex` (read from the winning archive, SHA-256 checked)
with only its pixel block replaced (`croteam_tex.reencode`): same head, same tail, same size and format.

| Texture | Role (Xbox -> PS) | Drawn with |
|---------|-------------------|------------|
| `A_button` | A -> cross | `glyphs/ps/outline-blue-cross` |
| `B_button` | B -> circle | `outline-red-circle` |
| `X_button` | X -> square | `outline-purple-square` |
| `Y_button` | Y -> triangle | `outline-green-triangle` |
| `Use_button` | the dark oval = the Xbox BLACK button, drawn for the Use action -> R1 | `plain-rectangle-R1` (solid; **unconfirmed mapping**) |
| `White_button` | the white oval = the Xbox WHITE button -> L1 | `plain-rectangle-L1` (solid; **unconfirmed mapping**) |
| `controller` | the 1024x1024 pad picture of the controller screen | `masks/serious/ds3-sam2-1024.png` (the owner's DualShock 3 picture, already fitted to the original pad box) |

**Shape (decision of the owner):** the symbols are ROUND, not tilted ovals: each is cropped to its visible part, fitted into a
square whose side is the SHORT side of the original oval's box (76) and centred on that box's centre (floor on odd sizes), on a
transparent canvas; the original's margins stay transparent so the game's layout is unchanged. L1 / R1 are the solid `plain-rectangle-*` variants (the outline ones vanished on light backgrounds and were thin at native size): 76 px wide and about 50 px tall. Under alpha 0 each texture keeps the colour the original stores (black; the white button
keeps white). Straight alpha, rows top-down, mips 1 (the format has none), so no mip regeneration is needed.
`tests/test_serious_sam_2.py` checks all of this on the real PNGs.

**Left alone:** `AxesButton` and `Dpad_button_*` (shapes that look the same on a PlayStation pad), `PC_*` keyboard keycaps,
`arrow_selected*`, `XBox_cross` (a crosshair), `AxesButton_glow`, the four arrow pictures `Controller*Arrow`, the layout `.rsc`
(`controller.tex` IS replaced, see below), every HUD / menu texture that is not a prompt. Everything else in the 14 archives is never read.

### The controller screen: DualShock 3 picture, Xbox annotation (KNOWN MISMATCH)
The owner chose (2026-11-25) to ship the DualShock 3 picture even though the annotation does not match it, to see it in game
(`make_profile(pad_picture=False)` turns it off). What the real files showed:
- The `lbl*Arrow*` gadgets of `Menus/CMSControllerOptions.rsc` are **text labels** ("Sidestep Left/Right", "Move
  Forward/Backward", "Rotate Left/Right", "Look Up/Down": `LT` = left thumb, `RT` = right thumb), not the arrow pictures. So are
  `lblDPadUp/Down/Left/Right` ("Throw Hand Grenade", "Third person view", "Toggle combo weapons", "Choose left weapon") and
  `lblLeftThumb` ("Down/Duck"). **Labels that refer to the D-pad: the four `lblDPad*`. No arrow gadget refers to the D-pad.**
- The arrow pictures (`ControllerForwardArrow`, `ControllerRightArrow`, `ControllerLookUpArrow`, `ControllerLookRightArrow`), the
  stick glow (`AxesButton_glow`) and the leader lines are drawn by `Sam2Game.dll` at **hard-coded layout coordinates of the
  Xbox "S" pad** (layout = 640x480 units; the pad picture is drawn at (220,180,200,200), so px = (x-220)*1024/200). The Xbox
  pad has the left stick upper-left and the D-pad lower-left, the DualShock the other way round, so on the DS3 the left-stick
  arrows and glow land on the **D-pad** and the right-stick ones around the right stick and the face buttons (a mock-up was made
  while authoring). **The arrows, the stick glow and the leader lines therefore do NOT match the DS3.** The DS3 picture itself also labels its small buttons SELECT and START (the picture is the owner's drawing; SS2's own prompts are not touched for them). Fixing it needs a patch of
  those coordinates in the DLL (a later step: a new strategy that writes a game binary, with its own safety review). The arrow
  gadgets, `Controller*Arrow`, `AxesButton_glow`, the layout `.rsc` and `Dpad_button_*` stay untouched.

Where the coordinates are (for that later step): `Bin/Sam2Game.dll` of build 24849618 (SHA-256
`5628b4ed30a966f10c8e8ea46bf0789257a35ea80127bacacebe28b1ce5303df`, 5,550,080 bytes; `objdump -d -M intel` reads the PE; `.text`
raw offset = RVA, so **file offset = VA - 0x10000000**). Helper functions after `CMSControllerOptions::OnSetCurrent`
(`0x101d3810..0x101d6000`) build float32 tables on the stack with `mov dword ptr [ebp - N], imm32`; each imm32 is a patch target:
- `0x101d3840`: the two stick-glow quads (`AxesButton_glow`), `(x0,y0,x1,y1)`: left (253,240,280,267), imm32 VAs `0x101d38c4`=x0,
  `0x101d38d2`=y0, `0x101d38cb`=x1, `0x101d38d9`=y1 (centre (266.5,253.5)); right (332,272,359,299), `0x101d3972`=x0, `0x101d3980`=y0,
  `0x101d3979`=x1, `0x101d3987`=y1 (centre (345.5,285.5)).
- `0x101d39c0` (526 instructions): 32 arrow quads (32x32 each, `(x0,y0,x1,y1)`) in four sets of eight, one set per axis mode
  (standard / lefty / beginner / lefty beginner, chosen by a `cmp eax, 3` on the mode argument). Set A (stack `ebp-0x240..`, imm32
  VAs `0x101d3d70..0x101d3ea6`, 10-byte steps): (330,245,362,277) (330,290,362,322) (273,237,305,269) (229,237,261,269)
  (251,216,283,248) (251,258,283,290) (354,264,386,296) (306,264,338,296). Set B (`ebp-0x1c0..`, `0x101d3aea..0x101d3c98`):
  (330,245,362,277) (330,290,362,322) (353,270,385,302) (307,270,339,302) (251,216,283,248) (251,258,283,290) (275,232,307,264)
  (226,232,258,264). Set C (`ebp-0x140..`, `0x101d3bda..0x101d3d66`) and set D (`ebp-0xc0..`, `0x101d39da..0x101d3ae0`) are the other
  two combinations. The left-stick quads orbit (266.5,253.5) and the right-stick ones (345.5,285.5), exactly the glow centres. The
  author's full list (every imm32 address of the 32 quads) is `dll_arrow_tables.txt` in the session scratchpad (regenerate with
  `objdump -d -M intel --start-address=0x101d3840 --stop-address=0x101d4460`).
- `0x101d4460` (354 instructions): the leader polylines (three points each, e.g. (380,298) (402,315) (425,315) for the white
  button, (394,285) (409,295) (425,295) for the black one, (268,197) (268,200) (268,212) for a trigger) from each control of the Xbox
  pad to its label column; the label text boxes themselves come from the `.rsc` gadgets. Their imm32 addresses were not listed.
Moving the DS3's sticks onto these centres by changing the picture rectangle is impossible (the Xbox sticks differ by 32 layout
units in height, the DS3's by none), which is why the DLL tables, not the picture, would have to change.

## How state is detected without a marker
The overlay adds a file, so there is nothing inside a game file to mark. State is read from the files:
- **original**: our file is absent (also what Steam > Verify files leaves if it deletes it: the status says it will be re-applied)
- **patched**: our file is present and byte-identical to what the plugin would build now
- **stale**: present, its SHA-256 is in the ownership ledger (`backups/204340/overlay/renders.log`) but it differs from today's
  build (an older drawing): rebuilt from the originals, never edited in place
- **unknown**: a file with our name that this plugin did not write (not in the ledger, not equal to today's build, a folder, a
  symlink, too big): a style is refused with nothing written, Original leaves it alone and says so (`foreign_file`)
A loose file at one of the six texture paths (the engine prefers loose files over archives when `fil_bPreferZips = 0`) counts as
foreign too: it would hide our entries.

**If the game is updated while our file is on disk** (a pinned entry no longer matches), a file PROVEN ours (ledger / build record) is removed at once, by the Original path (it was built from the old files and, loaded last, would override the new ones) and the status says so; a file we cannot prove ours is left alone. As root (Decky), the file (and any folder of the loose vehicle) is handed to the game folder's owner, with a neighbouring file's mode, on the temp file before it gets its final name.

## How it is verified
Before the first write: the folder is listed, every `.gro` (except ours) is opened ONCE (central directories only), each of the
six originals is read (at most its pinned size) from the archive that wins it and checked against the pinned SHA-256 and the
winning archive's name; any difference is an unknown build and nothing is written. Then the six outputs are built in memory,
each decoded back and compared with the intended pixels, and the archive is read back as a zip (CRC, names, bytes). Only then:
room check, ledger (fsynced), temp file + fsync + `os.replace` + fsync of the folder, read back. Original deletes our file (the
hash it deletes is re-checked) and nothing else.
- Tests: `tests/test_overlay_patch.py` (about 100 tests, synthetic Croteam-style game, both vehicles: apply, exact undo, whole-tree hashes,
  a cut at EVERY write/replace/remove event of apply and of Original, ledger before the first write, stale/foreign/lost-ledger,
  unknown build, extra archives, shadowing loose file, damaged archive, no space, preflight, running/updating, error before
  pending, watcher), `tests/test_serious_sam_2.py` (table pins, sort rule, real art shapes and colours, the real profile through
  the engine), `tests/test_croteam_tex.py`, `tests/test_serious_build.py`.
- Renders of the real art on the real originals (dark and light backgrounds, native-size strip): scratchpad `real_dark.png`,
  `real_light.png` (revision 1, outline R1 / L1: they vanished on light backgrounds, so revision 2 uses the solid
  `plain-rectangle-*` variants). The face-button rings are thin at native size (the owner's choice of outline art).

## Vehicles
`gro` (default): our archive. `loose`: the same entries as loose files under the game folder (journaled folders, removed again).
Both are implemented and tested; Serious Sam HD games are signed, so the vehicle may have to change after the first install.

## Verified / not verified
- Verified: the table pins match the real extracts; the six outputs decode to the intended round symbols on the real originals;
  apply, undo, crash cuts, refusal and watcher on synthetic archives.
- NOT verified: that the game really loads an extra `zz_ButtonGlyphs.gro` (the engine's load order was read from its log, not tested).
- NOT verified: the Use -> R1 and White -> L1 mapping (a guess from the Xbox black and white buttons).
- NOT verified: how the round symbols look in the game (size, thin rings) against the oval art, and the DualShock picture on the controller screen (its arrows, glow and leader lines are known to be wrong, see above).
- NOT verified: the real folder through the engine (clone validation of the 3.8 GB install, Steam verify, a game update).
- NOT verified: whether a layer I did not decode (`HudParams.rsc` ButtonStretch) stretches the quads.

## Deck state
Not installed. Nothing of this game was changed on the Deck (read-only copies only).

## Open questions and caveats
- Controller screen annotation: a DLL coordinate patch (a separate strategy) is the only way to make the arrows follow the DS3.
- `Use_button` is the black button the game draws for "Use"; R1 is the position of the Xbox RB, not of the black button.

## How to re-derive the data
`python3 tools/serious_build.py` writes the table (`profiles/serious_tables.py`) from copies of the root `.gro` files
(`deck.sh --from`). The boxes in `serious_sam_2.CELLS` come from decoding the six originals (alpha > 24 bounding box of each).

## Learnings
- A square texture can hold a tilted oval: measure the opaque box before placing round art (skill `glyph-mapping-ps-xbox`).
- A menu screen drawn by the game DLL at fixed coordinates cannot be re-laid-out by replacing its picture and `.rsc`: read the
  DLL before promising to move arrows.
