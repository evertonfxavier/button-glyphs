# Quantum Break - 474960

## At a glance
| | |
|--|--|
| Install dir | `steamapps/common/QuantumBreak` |
| Build verified | buildid 1412244 |
| Engine / format | Remedy Northlight; `data/ep999-000.rmdp` (56 GB) stores raw files at fixed offsets; index `ep999-000.xml` lists (offset, size, crc32) |
| Strategy / profile | in-place regions, `profiles/quantum_break.py` (`RegionGlyphProfile`) + generated `quantum_break_cells.py` + `quantum_break_pictures.py`, revision 2 |
| Styles | PlayStation, Original (the game is natively Xbox) |
| Status | revision 1 (8 prompt atlases) was applied on a previous Deck; revision 2 (adds the controller picture) is **not yet applied or seen in game** (see Deck state) |
| Summary | Every Xbox prompt of the HUD, menus and video player: A/B/X/Y become ✕ ○ □ △ in the PlayStation colours, LB/RB → L1/R1, LT/RT → L2/R2, in all four HUD scales. Sticks, D-pad and View/Menu already match a PlayStation pad. The Xbox controller picture of the options screen (`controller.tex`) is replaced by a DualSense drawing. Xbox and Original are the same thing here. |
| Support level | unconfirmed |

## Where the button art lives
`textures/hardcoded/prompts.tex` and `videoplayer_controls.tex`, each in four HUD scales
(`hud75`, `hud100`, `hud150`, `hud200`) = **8 regions, ~37 MB**, plus the pad picture `textures/menu/controller.tex` = **1 region, 4 MB (9 regions, ~41 MB in total)**. Format: 128-byte DDS header
(reserved bytes 0x20..0x4B are zero) + raw 32-bit **BGRA**, no mips, straight alpha; same layout
at every scale. Locale archives have no prompt textures.

## What the profile changes (and what it leaves alone)
PlayStation redraws the face buttons as cross / circle / square / triangle in the PlayStation
colours and relabels shoulders `L1/R1` and triggers `L2/R2` (white-on-transparent masks in
`masks/`, label font Montserrat Bold, OFL). Sticks, D-pad and View/Menu already look the same on
a PlayStation pad and are left alone. NOT redrawn: the locale `ctrllayout_0x.tex` and
`controller_disconnected.tex`.

### Controller picture (revision 2)
`textures/menu/controller.tex` (key `controller/menu`, offset 40082693152, size 4194432, SHA-256
`722e6ba0...f0271`, build 1412244) is a 1024x1024 raw BGRA texture (128-byte header, no mips): an
Xbox One pad as vector art on a **transparent** background (white body with light hatching,
steel-grey buttons, footprint x 199..824, y 298..725, centre 511.5/511.5). Convention: **straight
alpha**; every fully transparent pixel stores BGR (130, 125, 100). The PlayStation style replaces
the whole picture (`replace` entry, no cells): the body is cleared to (130, 125, 100, 0) and the
user's DualSense line drawing (`masks/controller_dualsense.png`, 704x450, RGBA, cut out of its
white background, body interior opaque white) is written centred in the box (160, 262)-(863, 761),
so the pad is 704 px wide (the Xbox pad was 626) and centred on the same point. Header: untouched
except the 44-byte marker. The drawing is the project owner's own art; the unprocessed source
(1672x941 on white) is not in the repo.

## How it is verified
Offsets and SHA-256 of every original region are hard-coded and checked before any write;
unknown builds are refused. State = 44-byte marker in the DDS reserved area (`W` writing / `P`
patched); backups hold only the changed regions in `backups/474960/regions/` (~41 MB).
Before the first write the profile's `preflight()` loads every bundled PNG (mask and picture) and
checks that the art fits its box and the box lies inside the texture; a missing or damaged asset
fails the whole apply with a clear error and the archive is left untouched. A refused sync shows
its error in the status (it outranks "will be applied").

### Rollback
A plugin downgrade to revision 1 does not know the 9th region, so choosing **Original** there
would leave `controller.tex` as the DualSense picture. Fix it with Steam > Verify files (it
restores the chunk), or reinstall revision 2 and choose Original (the backup of the region is in
`backups/474960/regions/`).

## Verified / not verified
- Verified: bytes written on the Deck equal the local render; regions restore exactly (revision 1).
- Verified offline (revision 2): synthetic-fixture tests for the picture region (deterministic
  render, header untouched except the marker, exact SHA-256 undo, unknown texture refused, crash
  recovery); the real extract of `controller.tex` renders centred (511.5/511.5), 704x450, with
  no white halo on the dark menu colour (40, 40, 48); the render takes ~0.7 s on the Mac.
- NOT verified: how the picture looks in game (scaling by the menu, filtering of the soft edge:
  the game may blend with straight or premultiplied alpha; the colour under transparent pixels
  matches the original, so either way the edge should stay clean), and the render time on the Deck.
- NOT verified: the result in game as a whole, and whether the game checks the `.meta` CRC
  (`m_uFileCRC`); if it fails to start, revert with Original.

## Deck state
Current Deck: fully original, the plugin is **not installed**; nothing of this
game was ever applied there. The previous Deck had revision 1 applied (PlayStation,
status ok); do not mix the two up. Re-read the real state with `/game-status` before trusting this.

## Open questions and caveats
Steam "verify" restores whole chunks; every touched chunk differs from the manifest, so it all
comes back original and is patched again by the watcher.

## How to re-derive the data
`tools/qb_build.py <textures dir> <Montserrat-Bold.ttf>` (needs Pillow + numpy, original
textures extracted from the rmdp) regenerates `masks/` and `quantum_break_cells.py`.

`tools/qb_controller_art.py <dualsense.png> <controller.tex> [<preview dir>]` (Pillow + numpy)
cuts the drawing out (flood fill of the white background from the border; alpha from the distance
to white along the outer contour; premultiplied scaling to 704 px), writes
`masks/controller_dualsense.png` and `quantum_break_pictures.py` (offset, size, SHA-256, box, the
transparent-pixel colour read from the extract) and, with a preview dir, a render on the dark menu
colour. The extract is only read, never copied into the repo.

## Learnings
