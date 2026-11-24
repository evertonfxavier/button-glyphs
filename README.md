# Button Glyphs

Decky Loader plugin for **Steam Deck** that changes the button prompts a game shows to **PlayStation** or **Xbox** style:

- lists the games installed in every Steam library and marks the supported ones
- one dropdown per game: **Original**, **PlayStation** or **Xbox**
- the original game file is **backed up** (checked by SHA-256) before the first change; picking **Original** restores it
- when Steam **updates** the game (or "Verify integrity" restores the file), the chosen style is **re-applied automatically**
- never touches a game while it is running or while Steam is updating it

## Supported games

| Game | Steam appid | What changes |
|------|-------------|--------------|
| Assassin's Creed (Director's Cut) | 15100 | The four action glyphs (head, weapon hand, empty hand, legs) in the HUD, tutorials and menus: △ □ ○ ✕ or Y X B A. PlayStation also redraws the Xbox 360 button art of the menus and the Animus room (A/B/X/Y → ✕ ○ □ △, LT/RT → L2/R2, L/R sticks) in 11 area archives |
| Charlie Murder | 405290 | All button prompts (text, HUD, tutorials, attribute screen). Forces the game's own PlayStation or Xbox set for every controller; *Original* = automatic (PlayStation only for a DualShock 4) |
| Quantum Break | 474960 | Every Xbox prompt of the HUD, menus and video player: A/B/X/Y → ✕ ○ □ △ in the PlayStation colours, LB/RB → L1/R1, LT/RT → L2/R2 (in all four HUD scales). Sticks, D-pad and View/Menu already match a PlayStation pad; the Xbox controller picture of the options screen is not redrawn. *Xbox* and *Original* are the same thing here |
| Saint Seiya: Soldiers' Soul | 348710 | The controller sheet of menus and tutorials (A/B/X/Y → ✕ ○ □ △, LB/RB → L1/R1, LT/RT → L2/R2, L3/R3, START/BACK → OPTIONS/SHARE), the X/Y/B shown beside the attack commands in battle, and the big A of the continue screen, in all eight languages. Sticks, D-pad and the numbered pad buttons are left alone; the Xbox controller drawings inside the help/tutorial pictures are not redrawn. *Xbox* and *Original* are the same thing here |

Every game stores its prompts differently, so each one needs its own profile (see [Adding a game](#adding-a-game)).

## Installation

1. Download `button-glyphs-vX.Y.Z.zip` from the [latest release](https://github.com/evertonfxavier/button-glyphs/releases/latest) (or build it, below). A `.sha256` file sits next to it.
2. In **Game Mode**: Quick Access (⋯) → **Decky** → **Settings** → enable **Developer Mode**.
3. **Install Plugin from ZIP** and pick the file.
4. Open **Button Glyphs** from the Decky plugin list.

The plugin does not need root: game files belong to the `deck` user.

## Usage

Open **Button Glyphs** in the Quick Access menu, pick a style for a game and start it. The status line under each game tells you what is happening:

| Status | Meaning |
|--------|---------|
| *PlayStation glyphs active* | Done |
| *Game is running* | The change is applied as soon as you close the game |
| *Steam is updating this game* | Applied after the update finishes |
| *…will be applied* | Waiting for the next check (every minute, or **Check for game updates now**) |
| *this game version is not supported yet* | An update changed the files in a way the profile does not recognise; nothing was modified |

## How it works

- **Backup**: for single-file games, `homebrew/data/Button Glyphs/backups/<appid>/` keeps the original file (a new copy is taken whenever a new original shows up after a game update). Games that spread their art over many big archives (Assassin's Creed) are patched *per entry*: the new data is appended to the archive and only its index slot is repointed, so the original bytes never leave the file. *Original* puts the slot back and cuts the file to its original size, which gives back exactly the bytes Steam installed (verified by SHA-256 in the tests), without copying gigabytes.
- **Self-describing files**: every file the plugin writes ends with a small trailer that records the style and the SHA-256 of the original it came from. No trailer means the file is an original, so after an update the plugin knows it has to apply the style again. Styles are always built from the original, never stacked.
- **Safety check**: before patching, the profile verifies that the textures it expects are there, with the expected size, format and glyphs. Unknown layouts are left untouched.
- **Assassin's Creed**: the action glyphs live in two font atlases, a white font atlas and one HUD texture inside `DataPC.forge` (Ubisoft Scimitar archive, LZO1X chunks, DXT3 textures); the Xbox 360 button art (`Conrtols_*`) is duplicated in 11 area archives. Only the touched 4×4 texture blocks are re-encoded. Writes are ordered new data → marker → index, so a power cut at any point leaves a file that is either original or recoverable from its marker; a game that fails the layout check is left untouched.

- **Quantum Break**: the prompts are two raw 32-bit BGRA atlases (`prompts.tex`, `videoplayer_controls.tex`, four HUD scales = 8 textures, ~37 MB) stored uncompressed at fixed offsets inside `data/ep999-000.rmdp` (56 GB). The profile rewrites those byte ranges **in place with the same size**: the archive never grows and its index is untouched. Each shape (disc, trapezoid) is wiped to its own fill colour and the new ✕ ○ □ △ / L1 R1 L2 R2 is drawn on top, so every colour variant of the atlas is kept. Only the 8 regions are backed up (`backups/<appid>/regions/`), not the whole archive. State is a marker in the zeroed reserved fields of each texture's DDS header, written marker → body → header so a power cut is recoverable; before touching anything the SHA-256 of every region must match the supported build.
- **Saint Seiya: Soldiers' Soul**: the UI lives in `resource/ui/<language>/*.emb` bundles (Dimps) inside `localize.cpk` (5 GB, CRI CPK, files stored uncompressed). The Xbox art sits in three DXT5 textures per language (controller sheet, battle `X/Y/B`, continue-screen `A`; 34 textures, ~42 MB in total, all eight languages). Same in-place, same-size, marker-in-DDS-header mechanism as Quantum Break, but the textures are block-compressed, so only the 4×4 blocks under each button cell are decoded, redrawn with the bundled PlayStation icons and re-encoded; every other block is byte-identical. Positions, sizes and SHA-256 of the originals come from `profiles/saint_seiya_regions.py`; a build whose textures differ is refused.
- **Charlie Murder**: the Linux port already contains both button sets and picks one from the pad GUID, so the Deck (which looks like an Xbox 360 pad) always got Xbox. The profile finds the IL that stores the pad type in `CharlieMurder.exe` by byte pattern and changes **one byte**; nothing is redrawn. The pattern must match exactly once, otherwise the file is left untouched.

## Development

Requirements: Node.js 20+, pnpm 9+, Python 3.9+ (backend is stdlib only).

```bash
pnpm install
pnpm run glyphs       # assets/svg -> py_modules/button_glyphs/glyphs/*.png + src/generated/glyphs.ts
pnpm run build        # frontend -> dist/
pnpm test             # backend unit tests (synthetic forge, no game data needed)
pnpm run build:zip    # out/button-glyphs-vX.Y.Z.zip
```

### Releases (automatic)
Releases are published by GitHub Actions ([`build-plugin.yml`](.github/workflows/build-plugin.yml)); there is nothing to tag by hand.

1. Bump `version` in `package.json` (`X.Y.Z`, or `X.Y.Z-beta.1` for a pre-release) and push to `main`.
2. CI runs the tests and builds the plugin. If that version has **no release yet**, the zip that was just tested is
   published as release `vX.Y.Z` (the tag is created at that commit) with `button-glyphs-vX.Y.Z.zip`, its `.sha256` and
   generated notes. A push that does not change the version publishes nothing.
3. A failing test or build publishes nothing. Re-run the workflow from the Actions tab (`Run workflow`) to retry.

### Project layout

| Path | Purpose |
|------|---------|
| `src/` | React UI (`@decky/ui`) |
| `main.py` | Decky entry point: callables + background watcher |
| `py_modules/button_glyphs/engine.py` | Apply/restore, backups, re-apply after updates |
| `py_modules/button_glyphs/entries.py` | Entry-level patching of big archives (append, repoint, exact undo) |
| `py_modules/button_glyphs/trailer.py` | The marker left at the end of every file we modify |
| `py_modules/button_glyphs/region_patch.py` | In-place patching of fixed byte ranges of a huge archive (Quantum Break) |
| `py_modules/button_glyphs/masks/` | White-on-transparent ✕ ○ □ △ and L1/R1/L2/R2 masks (labels use Montserrat Bold, SIL OFL) |
| `tools/qb_build.py` | Dev tool (needs Pillow + numpy): finds every button cell in the original Quantum Break atlases and regenerates `profiles/quantum_break_cells.py` and the masks |
| `py_modules/button_glyphs/profiles/dxt_regions.py` | Region profile for DXT5 textures (redraws only the blocks under each cell) |
| `tools/ssss_build.py`, `tools/cpk.py` | Dev tools (stdlib): read the game's CPK/EMB and regenerate `profiles/saint_seiya_regions.py` |
| `py_modules/button_glyphs/steam.py` | Steam libraries, manifests, running games |
| `py_modules/button_glyphs/forge.py` | Scimitar `.forge` reader/writer, LZO1X decoder |
| `py_modules/button_glyphs/image.py` | DXT1/3/5 codecs, PNG I/O, resampling |
| `py_modules/button_glyphs/profiles/` | One profile per supported game (`forge_glyphs.py`: textures in a forge; `il_patch.py`: one-byte code patch) |
| `assets/svg/{ps,xbox}/` | Icon sources, one SVG per button |
| `tests/` | Backend tests with a synthetic forge builder |

### Adding a game

1. Find where the game draws its prompts (texture, font atlas, config file…).
2. If it is a Scimitar forge with DXT glyphs, describe it with `ForgeGlyphProfile` (see `profiles/ac1.py`): forge entry, texture ids, size/format and the box of each glyph. Otherwise add a new profile kind with the same `check(path)` / `patch(path, style, load_icon)` interface.
3. If the game already ships both sets and only chooses by controller type, use `IlPatchProfile` (see `profiles/charlie_murder.py`).
4. Register it in `profiles/__init__.py`.

## Icons

PlayStation and Xbox icons come from the Figma community files *PlayStation Controller Icons* and *Xbox Controller Icons Free*. PlayStation and Xbox are trademarks of their respective owners; this project is not affiliated with Sony, Microsoft, Valve or Ubisoft.

## License

BSD-3-Clause, see [LICENSE](LICENSE). Based on [decky-plugin-template](https://github.com/SteamDeckHomebrew/decky-plugin-template).
