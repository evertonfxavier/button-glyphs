# Adding a game

This guide takes you from "I wish game X showed PlayStation buttons" to a pull request. You do not need to know everything
up front: it is a puzzle, and the steps below tell you which piece to look at next. Four games are already done, so there are
worked examples for every situation you will meet.

> **Short on time?** You can contribute without writing code: [request the game](https://github.com/evertonfxavier/button-glyphs/issues/new?template=game_request.yml),
> or write down what you found about its files (step 2) in an issue. Someone else can pick it up.

## Before you start

Only take on a game that passes this checklist:

- [ ] **You own the game** and it runs on your Steam Deck.
- [ ] **No anti-cheat and no online ranked play.** Changing files of such games can get accounts banned. Single-player and co-op games without anti-cheat are fine.
- [ ] **The game's files are yours to edit** (normal files in your Steam library). Never bypass DRM or encryption.
- [ ] You will **not copy game assets into the repository**. Tools in this project produce hashes and coordinates, never extracted art.
- [ ] You have a computer to analyse files on. Do not experiment on the Deck's only copy.

Read [safety-rules.md](safety-rules.md) once. It is short, and every rule exists because breaking it would damage somebody's game.

## The big picture

Every game draws its button prompts in a different place. A **profile** describes where, for one game, and a **strategy** is
the technique the profile uses to change them. There are four so far:

| Strategy | Use it when… | Example | Class |
|----------|--------------|---------|-------|
| **Code switch** | The game already contains both PlayStation and Xbox art and chooses in code | [Charlie Murder](games/405290-charlie-murder.md) (changes 1 byte) | `IlPatchProfile` |
| **Entry-level archive patch** | Textures live in compressed archives of moderate size, spread over many files | [Assassin's Creed](games/15100-assassins-creed.md) | `ForgeGlyphProfile` |
| **In-place raw regions** | Raw (uncompressed) textures sit at fixed offsets inside one giant file | [Quantum Break](games/474960-quantum-break.md) (56 GB archive) | `RegionGlyphProfile` |
| **In-place DXT regions** | The same, but the textures are DXT-compressed (DDS) inside a container | [Saint Seiya](games/348710-saint-seiya.md) | `DxtRegionProfile` |

How to choose, in order (stop at the first yes):

1. **Does the game already ship both sets?** Look for PlayStation art next to Xbox art, or a switch by controller type. A code or config change is the cheapest and safest option.
2. **Is there a documented option or loose-file override?** Some games read loose files before packed ones.
3. **Can a texture keep its exact size and offset?** Then rewrite it in place (the archive never grows, nothing moves). Use the raw or DXT region strategy.
4. **Are entries compressed or would the texture not fit?** Then append the new entry and repoint the index (entry-level).
5. **Encrypted, signed or checksummed by the game?** Probably not feasible. Write down what you found (see the end of this guide): that is a useful result.

Controls that are wrong (not just icons) are a different problem (XInput, trigger swaps, Steam Input templates). Do not mix them into a glyph profile.

## Step by step

### 1. Find the game on your Deck
- Install folder: `~/.local/share/Steam/steamapps/common/<install dir>` (or on your SD card / extra library).
- Steam app id and build: `~/.local/share/Steam/steamapps/appmanifest_<appid>.acf` (`appid`, `installdir`, `buildid`).
- Proton games keep their prefix in `.../steamapps/compatdata/<appid>/`.

### 2. Look around (read-only)
```bash
ls -la "<install dir>"                  # what is there, how big
du -sh "<install dir>"/*                # where the bytes are
file <some file>; xxd -l 64 <some file> # what format is it? (magic bytes)
strings -n 8 <exe> | grep -i -E "xbox|playstation|ps3|controller|gamepad|button"
```
Write down: the engine, the archive formats and sizes, anything with `button`, `pad`, `controller`, `hud`, `icon`, `prompt` in its name,
and whether PlayStation art is already present. Search the web for existing icon mods and input fixes: a mod tells you *which files* hold the art
(never copy a mod's files, and check its licence).

### 3. Pick a strategy
Use the table above. Write the decision and the evidence in your notes.

### 4. Copy the files to your computer
```bash
scp "deck@<deck-ip>:/home/deck/.local/share/Steam/steamapps/common/<dir>/<file>" ./work/
```
Check sizes first; for a huge archive copy only what you need (or work read-only over SSH for that one). Never analyse by writing to the Deck.
([development.md](development.md#working-with-a-real-deck) explains how to turn SSH on.)

### 5. Find the prompts
You are looking for **every** place a prompt is drawn: font atlases, HUD textures, menu textures, tutorial pictures, per language, per resolution.
- Scimitar `.forge` games: [`tools/forge_scan.py`](../tools/forge_scan.py) lists every texture with its size, format and button-colored blocks; [`tools/forge_render.py`](../tools/forge_render.py) turns textures into PNGs to look at.
- CPK/EMB games: `cpk.py` and `ssss_build.py` (Saint Seiya) are the model; ask in an issue if you need them, they are not in this repository yet.
- Other formats: write a small reader first (the [format notes](games/) of each game show what to record), prove it by **round-trip** (parse, serialise, get identical bytes), then scan.
- **Look at the pictures.** Decode a texture you can recognise (a HUD button) before trusting any table. Render originals on a dark background, upscaled, in the orientation the game shows (many textures are stored upside down).
- **Find all copies.** The same art is often repeated per area, language or HUD scale. Check that every copy has the same layout.

### 6. Generate the data with a tool
Never type ids, offsets or hashes by hand. Write (or adapt) a tool that outputs a Python module marked
`Generated by tools/<x>.py - do not edit` with: where each texture is, its size, and the **SHA-256 of the original bytes**. The hashes are what lets
the plugin refuse a game version it does not know. Example: [`tools/qb_build.py`](../tools/qb_build.py) (Quantum Break); `ssss_build.py` (Saint Seiya) works the same way.

### 7. Create the starting files
```bash
python3 tools/scaffold_profile.py --appid 12345 --name "Some Game" --kind region-glyphs
```
`--kind` is one of `forge-glyphs`, `il-patch`, `region-glyphs`, `dxt-regions`. This creates a valid but **empty** profile in `py_modules/button_glyphs/profiles/`,
a page in `docs/games/` and a test in `tests/`. It does **not** register the game: that happens only when the data is real.

### 8. Fill in the profile
Open the new profile: every `TODO` is a decision. The docstring of the strategy class explains each field. Choose the icons from
[`assets/svg`](../assets/svg) (names like `ps/outline-blue-cross`, `ps/plain-L1`); the mapping between Xbox and PlayStation is by position:

| Position | Xbox | PlayStation |
|----------|------|-------------|
| bottom | A | ✕ |
| right | B | ○ |
| left | X | □ |
| top | Y | △ |
| shoulders / triggers | LB RB / LT RT | L1 R1 / L2 R2 |
| menu / view | Menu / View | OPTIONS / SHARE |

Keep what already looks the same on both pads (sticks, D-pad) untouched. If the original shows a **label** (LB, RT), relabel it (L1, R2) instead of just recolouring.
Add the profile to [`profiles/__init__.py`](../py_modules/button_glyphs/profiles/__init__.py) when the tests pass.

### 9. Write the tests
Tests never use real game files. Build a **small synthetic archive with the same layout** (see `tests/forge_fixture.py`, `tests/test_regions.py`) and check at least:

- every style is applied and looks right (compare pixels);
- **Original gives back the exact bytes** (SHA-256 of the whole file before and after);
- switching styles back and forth does not make files grow;
- an unknown layout is refused and nothing is written;
- after a simulated game update (replace the files, new build id) the style is re-applied;
- a simulated crash mid-write recovers.

Run everything: `python3 -W error::ResourceWarning -m unittest discover -s tests` and `npx tsc --noEmit -p .`.

### 10. Verify on the real files
- Single-file and forge games: copy the files the profile touches and run
  `python3 tools/clone_validate.py <appid> <folder with the files>`. It clones them (instantly on macOS), runs *PlayStation → Xbox → PlayStation → Original* through the real engine, and proves the originals come back byte for byte.
- Giant archives: validate read-only on the Deck instead (the profile's `check` against every recorded hash, plus an in-memory render).
- **Look at the result.** Make a contact sheet with the original on top and the patched textures below, in game orientation, for every style.

### 11. Write the game's page and regenerate the list
Fill every section of the page in `docs/games/` (the template tells you what each means). Be honest in *Verified / not verified*. The *Summary* and
*Support level* rows feed the public list, so write them for players. Start with `unconfirmed` unless you saw the result in the game. Then:

```bash
python3 tools/gen_supported_games.py     # updates SUPPORTED_GAMES.md (the READMEs only link to it)
```
The tests fail if you forget.

### 12. Open the pull request
Follow [CONTRIBUTING.md](../CONTRIBUTING.md#pull-requests). The template asks the same things as this guide, plus a screenshot if you have one.

## Lessons from the first four games

These cost real time once; they are yours for free:

- **Look for a code switch first.** Charlie Murder needed one byte, not a redraw.
- **Format codes lie by name.** In the Scimitar texture header, code `4` is DXT3, not DXT5; decoding it wrongly looks like corrupted fonts.
- **Rows are often stored upside down.** Symmetric icons (✕ ○ □) hide a missing flip; test with a triangle or a letter.
- **Prompts live in more than one place.** Assassin's Creed has font glyphs *and* separate menu textures; scan every archive before calling a game done.
- **Checksum details matter.** One game's checksum uses an initial value of 0 instead of the usual 1; if checks never match, try that first.
- **Pure-Python decompression is slow** (about 0.5 MB/s): decode only the pieces you touch, and use a native library for bulk analysis on your computer.
- **Reserved header bytes can hold a marker.** DDS headers have bytes that are zero in the originals: a 44-byte marker fits there without changing the file size.
- **Prove undo with a whole-file hash**, after a full cycle. A cheap clone makes a 6 GB check take seconds.
- **Look at it.** Numbers prove the bytes; only a picture proves it looks right.
- **A community mod is a map, not a source.** Diff it against the original to learn which textures matter, then draw your own icons.

## If it turns out not to be feasible
That is a valid result. Open an issue titled *"[Not feasible] <game>"* with: how the game stores its prompts, why it cannot be done safely
(encrypted? checksummed? rendered by code?) and what would change the answer. It saves the next person days.

## Getting help
Stuck on a step? [Open a discussion-style issue](https://github.com/evertonfxavier/button-glyphs/issues/new/choose) and say which step. Half-finished work is welcome: say what you have, and someone can continue from there.
