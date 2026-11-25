# Serious Sam HD: The First Encounter (41000)

## At a glance
| | |
|--|--|
| Steam appid / install dir | 41000 / `Serious Sam HD The First Encounter` (the archives live in `Content/SeriousSamHD/`) |
| Build verified | buildid 5820849: the pinned SHA-256 / CRC-32 / sizes of every replaced entry come from the real archives (generated `profiles/serious_tables.py`); the extracts used for the renders matched their pins. Not yet run against the real folder through the engine |
| Engine / format | Croteam Serious Engine 3.5 (`SamHD*.exe`, Windows build under Proton). `.gro` archives = plain ZIPs, SIGNED (see below). Textures are HD `.tex` (CTSEMETA; RGBA8 and DXT5 with a mip chain stored as a filtered block; `croteam_tex.py`) |
| Strategy / profile | **add-file overlay**: one small archive `zz_ButtonGlyphs.gro` is ADDED to `Content/SeriousSamHD/` and (if the engine loads it) wins over the game's own entries; nothing of the game is modified. `profiles/serious_sam_hd_tfe.py` on `profiles/serious_hd.py` + `overlay_glyphs.py` (`OverlayGlyphProfile`, `overlay_level`) + `overlay_patch.py`, revision 1 |
| Styles | PlayStation, Original (= Xbox, the game is natively Xbox) |
| Status | profile written and verified offline (synthetic archives, the real bundled art, renders on the real extracts); not installed, never run in the game; **whether the restricted exe loads an unsigned extra archive is unknown** |
| Summary | A / B / X / Y become the PlayStation cross, circle, square and triangle (round, in their colours) in the HUD hints, the menus and the controls screen, whose pad picture becomes a DualShock 3. Everything else (the D-pad, the sticks, the keyboard keys, the arrows) is left as it is; the pad screen's arrows are expected to be misplaced on the DualShock picture. |
| Support level | unconfirmed |

## Where the button art lives
The prompts are HD `.tex` textures in `All.gro` (`Content/SeriousSamHD`, 5,684 entries; patch archives `All_117121`, `All_582898`):
`Textures/Menu/A_button..Y_button.tex` (32x32 RGBA8, 6 mips: glossy ovals), `Textures/Menu/XBoxButtonA/B/X/Y.tex` (64x64 DXT5, 7 mips: white
discs with a cut-out letter, each with a small wrapper `.rsc`), and `Textures/GlobalAtlas.rsc` (a CStaticAtlas of three DXT5 blocks:
256x128, 512x512, 4096x2048) whose FIRST block bakes the four 64x64 prompts at (64,0) A, (0,0) B, (0,64) X, (64,64) Y, which is what
the HUD draws. The controls screen uses `Textures/Menu/GamepadPerspective.tex` (512x512 DXT5, 10 mips) named by `Databases/MenuPalette.rsc`.

## What the profile changes (and what it leaves alone)
**Replaced, 10 entries:** `A_button`, `B_button`, `X_button`, `Y_button` (32x32), `XBoxButtonA/B/X/Y` (64x64), `GamepadPerspective` (512x512),
and `GlobalAtlas.rsc` (the four baked cells, via `croteam_tex.with_region`; the other two blocks and every other byte identical). Roles:
A cross (blue), B circle (red), X square (pink), Y triangle (green).

**Left alone:** `AxesButton` and the stick caps, `Controller*Arrow`, `GamepadStrafe/Look*` (16-bit overlays), the keyboard keys `PC_*`, `Xbox_cross` and every other texture and atlas cell. Everything else in the archives is never read.

Placement (measured on the real textures, opaque box = alpha > 24, `x0,y0,x1,y1`): the 32x32 ovals A (5,4,25,28) B (7,4,27,28) X (7,4,27,28) Y (5,4,25,28) = 20 x 24; the 64x64 discs (14,14,50,50) = 36 x 36 (standalone and atlas cells identical: mean difference 0.00, max 8 = DXT noise).
Faces are the owner's choice of outline coloured symbols, drawn ROUND (diameter = the box's short side, centred on the box
centre); shoulders, triggers, START, BACK and the stick-click shapes are fitted into the whole box keeping their aspect. Under
alpha 0 the originals store the colour of the nearest visible pixel (a white disc bleeds white, a green oval green): ours bleeds its
own colour the same way (`bleed`). Variants (catalog `find --style ps`, 64x64): shoulders and triggers `plain-rectangle-L1/R1/L2/R2`
(solid; the outline ones vanish on light backgrounds), L3 / R3 `plain-L3` / `plain-R3` (a solid disc with a dark label, the most
legible at 64 px), START `plain-big-option` (a solid pill under the word OPTIONS), BACK `outline-share` (the only PlayStation
vector for it: thin, and the word SHARE is pale). All of it is checked by `tests/test_serious_hd.py` on the real PNGs.

### The pad screen and the left stick / D-pad swap
`GamepadPerspective.tex` becomes the owner's DualShock 3 picture (`masks/serious/ds3-hd-fe-512.png`, 512x512,
colour under alpha 0 kept as the original: `(0, 0, 0)`). The arrows and overlays of that screen
(`Controller*Arrow`, `GamepadStrafe/Look*`) are placed by the exe with no readable layout and stay untouched: **the DualShock 3's left
stick and D-pad sit where the Xbox 360 pad has them the other way round (Xbox: stick upper-left, D-pad lower-left; DS3: D-pad
upper-left, stick lower-left, centre), so those arrows are EXPECTED to be misplaced on the DS3 picture.** Fixing it needs an exe
patch (a later step, like the Serious Sam 2 DLL coordinates).

## The signed-archive uncertainty (read this before the first install)
The game folder holds a `SignatureCatalog.dat` and the restricted exe prints, for an archive it cannot verify,
`<archive>: SKIPPED - This archive can only be used with moddable (unrestricted) applications.` (a good archive logs
`<archive>: N files, signature: <result>`; the real logs of the untouched game show `Content/SeriousSamHD/All.gro: ... files, signature: OFFICIAL`
for every archive). It also has "Block signature verification failed", "File signature verification failed" and "Executable
signature verification failed" strings; **the log of the UNTOUCHED game already contains `ERR:   Executable signature verification failed.`
(First Encounter), so that line alone is not caused by this plugin.** The folder has no `DLC_*.dat` sidecar files.
So **an unsigned extra `zz_ButtonGlyphs.gro` may be ignored by the restricted exe**, and whether a LOOSE file under `Content/` overrides an
archive entry in this engine is unknown too. A moddable `..._Unrestricted.exe` exists (`ETRSVersion.Unrestricted=Moddable`, no
achievements), launched by Steam only if the user chooses it; this plugin never changes launch options.
- **Profile design**: both vehicles are implemented and tested; the vehicle is ONE constant, `VEHICLE` in `profiles/serious_sam_hd_tfe.py`
  (`"gro"` = our archive, the default; `"loose"` = the same entries as loose files under the game folder). A later release that
  flips it cleans the old artefact itself (a vehicle switch removes our file of the other vehicle, hash checked, last).
- **How to read the first install** (the first approved install is the test): start the game once, open the newest `Log/SamHD*.log` of the
  install folder and look for the line of our archive:
  - `Content/SeriousSamHD/zz_ButtonGlyphs.gro: N files, signature: ...` = the archive was loaded (then look at the buttons);
  - `Content/SeriousSamHD/zz_ButtonGlyphs.gro: SKIPPED - This archive can only be used with moddable (unrestricted) applications.` = it was
    ignored: switch `VEHICLE` to `"loose"` and re-apply; if the buttons still do not change the engine ignores loose files too and only the
    Unrestricted exe or a signed route remains;
  - no line at all = the archive was not even listed (wrong folder or name).
- **Load order**: the real logs list the archives in strict sorted order (`All`, `All_<build>`, ..., `CachedShaders_PC`, `DLC_*`), so
  `zz_ButtonGlyphs.gro` sorts last and its entries would win; the same log shows whether it really loads last.

## How state is detected without a marker
The overlay adds a file, so nothing inside a game file can mark it. State is read from the files: our file absent = original;
present and recorded for the current build fingerprint = patched; present and in the SHA-256 ledger (`backups/41000/overlay/`)
but not current = stale (an older drawing, rebuilt from the originals, never edited); a file with our name that we did not write
= unknown (refused, never read as an original, overwritten or deleted; Original leaves it and says so). A lost ledger is
healed by the next full check when the file equals today's output. **If the game is updated while our file is on disk** (a pinned entry no longer matches), a file PROVEN ours is removed at once (it was built from the old files and, loaded last, would override the new ones) and the status says so; a file we cannot prove ours is left alone. As root, the file (and any folder of the loose vehicle) is handed to the game folder's owner before it gets its final name. See `docs/architecture.md`.

## How it is verified
Before the first write: every pinned original entry is read (at most its pinned size) from the archive that wins it and its
SHA-256 checked (the status poll uses only central-directory size and CRC-32); an unknown build writes nothing. Every output is then
built in memory and decoded back: RGBA8 textures exactly, DXT5 within a mean error of 12 and the first mip level too; the atlas
is written cell by cell with the block-granular writer and proved to change only the cells' 4x4 blocks and their mip footprints
(everything else in the file identical); the archive is read back as a zip. Only then: room check, ledger and build record
(fsynced), temp + fsync + `os.replace` + fsync of the folder, read back. Original deletes our file (re-hashed first). The overlay file is 12.0 MB.
- Tests: `tests/test_serious_hd.py` (tables, boxes, real art, builders, the atlas proof, a synthetic HD game through the engine
  with both vehicles and a cut at every write), `tests/test_overlay_patch.py` (the strategy), `tests/test_verify_overlay.py`
  (`tools/verify_profile.py` on the overlay, with mutation tests).
- Renders of the real overlay built in memory from the real extracts (dark and light backgrounds, atlas included): looked at;
  the First Encounter's ovals have the colour bled under alpha 0 per texture ((41,157,20) green for A ...), which our drawings replace by their own edge colour.

## Cost: apply versus status (measured on the Mac)
Apply builds everything in memory (once, proved before the first write): pad `GamepadPerspective` 512x512 DXT5 + mips about 3.6 s,
the 256x128 atlas block 0.6 s, the 64x64 / 32x32 textures about 0.1 s each; about 5 s in all on the Mac, several times that on a Deck (the
panel says "keep the game closed" meanwhile). The status poll and the watcher's idle tick do NOT build: they read the archives'
central directories (size, CRC-32 and winning archive of every pinned entry, parsed once per archive and cached while its
size/time stamp are unchanged), compare our file's SHA-256 with the build record for the current fingerprint (pins, bundled art
bytes, revision, drawing code), and stop there. Measured by the verifier on the real copies (Mac, `verify_profile.py`, both vehicles, three games): status 6-22 ms, idle watcher tick
5-17 ms (one 77 ms outlier on loose files), first apply 1.9 s (Serious Sam 2) / 5.5 s (First Encounter) / 8.9 s (Second Encounter), the
status poll hashing our own file of 12.0 MB included. A failed build or write is retried by the watcher only every 10 minutes (an explicit
choice retries at once); the room is checked from the pinned sizes before anything is built.

## Verified / not verified
- Verified: the table pins match the real extracts that were available; the real builders decode back to the intended pictures
  on the real originals and atlas; apply, undo, crash cuts, refusal and watcher on synthetic archives; the cheap status path.
- NOT verified: that the restricted exe loads an unsigned extra archive, or overrides an entry with a loose file (see above).
- Verified in the real logs: the archives load in strict sorted order (`All`, `All_117121`, `All_582898`, `CachedShaders_PC`), so `zz_ButtonGlyphs.gro` sorts last; not verified that it is accepted.
- NOT verified: how the symbols look in game (size of the 20 px rings in the 32 px hints), and the pad screen with the DualShock picture (arrows misplaced by design).
- NOT verified: the real 1 GB folder through the engine (Steam verify, a game update); only the entries that were extracted (A/B/X/Y_button, XBoxButtonA/B/Y, GamepadPerspective, the atlas) were checked against their pins, `XBoxButtonX.tex` was drawn on a stand-in original of the same format.

## Deck state
Not installed. Nothing was changed on the Deck (read-only copies only).

## Open questions and caveats
- The first install decides the vehicle (see the signed-archive section); the Unrestricted exe is a possible route the plugin
  does not take by itself.
- Face rings are thin at native size (the owner's choice). The DualShock pad picture labels its small buttons SELECT and START (the owner's drawing; this game's START / BACK prompts are not replaced).

## How to re-derive the data
`python3 tools/serious_build.py` writes the table (`profiles/serious_tables.py`) from copies of the root `.gro` files
(`deck.sh --from`); the boxes in `serious_hd.SPECS` come from decoding the originals (alpha > 24 bounding boxes; the 64x64 ones on
the cells of the real atlas, which hold the same art as the standalone files).

## Learnings
- The HD atlas bakes the prompts: replacing the standalone textures alone changes nothing the HUD draws, so the atlas cells are
  redrawn too (block-granular writer, everything else byte-identical).
- A signed archive set makes an unsigned extra archive a gamble: keep both vehicles, one constant apart, and read the game's own log.
