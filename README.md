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
| Assassin's Creed (Director's Cut) | 15100 | The four action glyphs (head, weapon hand, empty hand, legs) in the HUD, tutorials and menus: △ □ ○ ✕ or Y X B A |
| Charlie Murder | 405290 | All button prompts (text, HUD, tutorials, attribute screen). Forces the game's own PlayStation or Xbox set for every controller; *Original* = automatic (PlayStation only for a DualShock 4) |

Every game stores its prompts differently, so each one needs its own profile (see [Adding a game](#adding-a-game)).

## Installation

1. Download `button-glyphs-vX.Y.Z.zip` from Releases (or build it, below).
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

- **Backup**: `homebrew/data/Button Glyphs/backups/<appid>/` keeps the original file. A new backup is taken whenever a new original shows up (game update).
- **Self-describing files**: every file the plugin writes ends with a small trailer that records the style and the SHA-256 of the original it came from. No trailer means the file is an original, so after an update the plugin knows it has to apply the style again. Styles are always built from the original, never stacked.
- **Safety check**: before patching, the profile verifies that the textures it expects are there, with the expected size, format and glyphs. Unknown layouts are left untouched.
- **Assassin's Creed**: the glyphs live in two font atlases and one HUD texture inside `DataPC.forge` (Ubisoft Scimitar archive, LZO1X chunks, DXT3 textures). Only the touched 4×4 texture blocks are re-encoded; the rewritten archive entry is appended to the file and the index is repointed, so the original data is never overwritten in place.

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

### Project layout

| Path | Purpose |
|------|---------|
| `src/` | React UI (`@decky/ui`) |
| `main.py` | Decky entry point: callables + background watcher |
| `py_modules/button_glyphs/engine.py` | Backups, apply/restore, re-apply after updates |
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
