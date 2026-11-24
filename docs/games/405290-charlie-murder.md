# Charlie Murder - 405290

## At a glance
| | |
|--|--|
| Install dir | `steamapps/common/Charlie Murder` (FNA/Linux port by flibitijibibo) |
| Build verified | buildid 8270183 (`CharlieMurder.exe`, 1.7 MB) |
| Engine / format | .NET assembly (XNA/FNA); art in `gfx/attribs.xnb` and font tables |
| Strategy / profile | IL patch, `profiles/charlie_murder.py` on `profiles/il_patch.py` (`IlPatchProfile`), revision 1 |
| Styles | PlayStation, Xbox, Original (= automatic by pad) |
| Status | confirmed in game by the user (2026-11-23) |
| Summary | All button prompts (text, HUD, tutorials, attribute screen) use the game's own PlayStation or Xbox set for every controller. Original is automatic: PlayStation only for a DualShock 4, Xbox for anything else (including the Deck). |
| Support level | confirmed |

## Where the button art lives
Nowhere to redraw: the port **already ships both sets** (Xbox, and PlayStation art: cross,
circle, square, triangle, L1/R1/L2/R2/L3/R3, OPTIONS/SHARE, red D-pad). `flibitPort.UpdateInput`
stores a pad type per player: 1 (PlayStation) when the pad GUID is a DualShock 4
(`4c05c405` / `4c05cc09`), else 0 (Xbox). The Deck presents an Xbox 360 pad, so it always got Xbox.

## What the profile changes (and what it leaves alone)
Finds the IL of that store by byte pattern (metadata tokens wildcarded; it must match exactly
once) and changes **one byte**: PlayStation makes the fallback branch store 1, Xbox makes the
DualShock branch store 0, Original is untouched. Everything the player sees goes through
`GetButtonIdx` / `DrawButtonAttrib`, which read that table, so text, HUD, tutorials and the
attribute screen all follow.

## How it is verified
Anchors (both GUID strings as UTF-16) must be present and the pattern must match once, otherwise
the file is left alone. Whole-file backup (1.7 MB) in `backups/405290/`.

## Verified / not verified
- Verified: game boots and shows PlayStation prompts on the user's Deck.
- NOT verified: other builds than 8270183; Xbox style on a DualShock 4 (not needed on the Deck).

## Deck state
PlayStation active, status ok at the last read-only check (2026-11-23); earlier the same day it had been put back to Original. Backup of the exe kept. Run `/game-status 405290` for the current state.

## Open questions and caveats
The disassembly was done with `dnfile` and a small CIL disassembler that only lived in an old
session scratchpad - not kept. For another XNA/FNA port, repeat that analysis first.

## How to re-derive the data
Disassemble `UpdateInput` (dnfile + CIL), locate the pad-type store, derive the pattern and the
two edit offsets (`DS4_STORES`, `OTHER_STORES` in the profile).

## Learnings
