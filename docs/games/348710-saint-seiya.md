# Saint Seiya: Soldiers' Soul - 348710

## At a glance
| | |
|--|--|
| Install dir | `steamapps/common/SSSS` |
| Build verified | buildid 884973 |
| Engine / format | Dimps; `localize.cpk` (5.3 GB, CRI CPK, files stored uncompressed); UI bundles `.emb` with DXT5 DDS members |
| Strategy / profile | in-place regions, `profiles/saint_seiya.py` (`DxtRegionProfile`) + generated `saint_seiya_regions.py`, revision 1 |
| Styles | PlayStation, Original (= Xbox, the game is natively Xbox) |
| Status | applied on the Deck by another session (status ok); **not confirmed in game by the user** |
| Summary | The controller sheet of menus and tutorials (A/B/X/Y → ✕ ○ □ △, LB/RB → L1/R1, LT/RT → L2/R2, L3/R3, START/BACK → OPTIONS/SHARE), the X/Y/B beside the attack commands in battle and the big A of the continue screen, in all eight languages. Sticks, D-pad, the numbered pad buttons and the controller drawings inside help pictures are left alone. Xbox and Original are the same thing here. |
| Support level | unconfirmed |

## Where the button art lives
`resource/ui/<BR|CH|EN|FR|IT|JP|NS|SP>/*.emb` inside `localize.cpk` (absolute offset =
`min(TocOffset, ContentOffset) + FileOffset`). EMB = `#EMB` + (offset, size) table + name table;
members are plain DXT5 DDS, no mips, reserved header bytes zero. Xbox art, identical across the
8 languages: `BTN_PS3_S` 1024x1024 in `command_STEAM` / `option_STEAM` / `btn_STEAM` (plus
`BTN_black` in the JP `btn_STEAM`; despite its name the sheet is Xbox art), `cockpit_arts_btn`
(`battle_STEAM`: X / Y / B) and `btn` 2048x1024 (`continue_STEAM`: big A). **34 textures, ~42 MB.**
`resource.cpk` has no button art.

## What the profile changes (and what it leaves alone)
Layouts `pad`, `cockpit`, `continue` list each cell (clear area + icon boxes): face buttons,
bumpers `L1/R1`, triggers `L2/R2`, L3/R3, START (options), BACK (share), cockpit X/Y/B, continue A.
Only 4x4 blocks under cells are decoded, redrawn and re-encoded. Sticks, D-pad and the numbered
pad buttons stay. The game also ships real PS3 art that PC never uses; the plugin does **not**
copy it (no game art redistributed) and draws its own `ps/*` icons.

## How it is verified
34/34 SHA-256 of the original regions must match; a build whose textures differ is refused.
Same DDS marker and region backups as Quantum Break.

## Verified / not verified
- Verified: unit tests; read-only on the real Deck file: 34/34 hashes match, in-memory render 4.3 s.
- NOT verified: how it looks in game (it is applied, nobody has reported the result); `HELP.emb` (`HELP_IMG*`) may still show
  Xbox controller drawings - untouched.

## Deck state
PlayStation active, status ok (read-only dry run on 2026-11-23); applied outside this page's author's session. Not confirmed visually. Run `/game-status 348710` for the current state.

## How to re-derive the data
`tools/ssss_build.py <install dir>` (with `tools/cpk.py`, stdlib, run it on the Deck) regenerates
`saint_seiya_regions.py` and checks every copy has the same layout.

## Learnings
