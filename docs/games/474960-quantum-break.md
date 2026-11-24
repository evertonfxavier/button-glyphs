# Quantum Break - 474960

## At a glance
| | |
|--|--|
| Install dir | `steamapps/common/QuantumBreak` |
| Build verified | buildid 1412244 |
| Engine / format | Remedy Northlight; `data/ep999-000.rmdp` (56 GB) stores raw files at fixed offsets; index `ep999-000.xml` lists (offset, size, crc32) |
| Strategy / profile | in-place regions, `profiles/quantum_break.py` (`RegionGlyphProfile`) + generated `quantum_break_cells.py`, revision 1 |
| Styles | PlayStation, Original (the game is natively Xbox) |
| Status | applied to the real game on the Deck; **not yet seen in game** |
| Summary | Every Xbox prompt of the HUD, menus and video player: A/B/X/Y become ✕ ○ □ △ in the PlayStation colours, LB/RB → L1/R1, LT/RT → L2/R2, in all four HUD scales. Sticks, D-pad and View/Menu already match a PlayStation pad, and the Xbox controller picture of the options screen is not redrawn. Xbox and Original are the same thing here. |
| Support level | unconfirmed |

## Where the button art lives
`textures/hardcoded/prompts.tex` and `videoplayer_controls.tex`, each in four HUD scales
(`hud75`, `hud100`, `hud150`, `hud200`) = **8 regions, ~37 MB**. Format: 128-byte DDS header
(reserved bytes 0x20..0x4B are zero) + raw 32-bit **BGRA**, no mips, straight alpha; same layout
at every scale. Locale archives have no prompt textures.

## What the profile changes (and what it leaves alone)
PlayStation redraws the face buttons as cross / circle / square / triangle in the PlayStation
colours and relabels shoulders `L1/R1` and triggers `L2/R2` (white-on-transparent masks in
`masks/`, label font Montserrat Bold, OFL). Sticks, D-pad and View/Menu already look the same on
a PlayStation pad and are left alone. NOT redrawn: `controller.tex` (the Xbox pad picture) and
the locale `ctrllayout_0x.tex`.

## How it is verified
Offsets and SHA-256 of every original region are hard-coded and checked before any write;
unknown builds are refused. State = 44-byte marker in the DDS reserved area (`W` writing / `P`
patched); backups hold only the changed regions in `backups/474960/regions/` (~36 MB).

## Verified / not verified
- Verified: bytes written on the Deck equal the local render; regions restore exactly.
- NOT verified: the result in game; whether the game checks the `.meta` CRC (`m_uFileCRC`) - if
  it fails to start, revert with `set_style original`.

## Deck state
PlayStation active, status ok.

## Open questions and caveats
Steam "verify" restores whole chunks; every touched chunk differs from the manifest, so it all
comes back original and is patched again by the watcher.

## How to re-derive the data
`tools/qb_build.py <textures dir> <Montserrat-Bold.ttf>` (needs Pillow + numpy, original
textures extracted from the rmdp) regenerates `masks/` and `quantum_break_cells.py`.

## Learnings
