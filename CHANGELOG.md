# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

Add your line under **Unreleased** in the pull request; maintainers move it into a version when they release.

## [Unreleased]

## [0.3.10] - 2026-11-24

### Fixed
- `tools/deck_probe.py`, first run on a real Deck, had three false results: a game the plugin had patched was reported as a
  hash mismatch (it now recognises the plugin's own files by the render ledger and checks that the backups still equal the
  pinned originals); Steam's `TargetBuildID` of 0 ("no update target") was read as a pending update; and a plugin without
  the Decky `root` flag was assumed to run as root. A saved-value game is no longer reported as "pending" because its pinned
  file never changes. A probe test that compared two runs also compared the machine's uptime, which moves between runs on
  Linux, so the CI failed (a Mac never showed it).

## [0.3.9] - 2026-11-24

### Added
- Maintainer tool `tools/triage_game.py`: a read-only, bounded triage of a game's install folder (a local path, or `--appid`
  through the Deck) that reports the engine with evidence, containers by magic, texture formats, ranked prompt-like files,
  Xbox/PlayStation/Switch variant sets and strategy leads. It recognises 17 engines and container families and is meant to run
  before the recon.

## [0.3.8] - 2026-11-24

### Fixed
- *Assassin's Creed*: a power cut between the two writes that repoint an entry (the index slot and the size in its name
  record), or while the new data was being written, could leave the file in a state the plugin did not recognise, so
  **Original** was no longer byte-exact. Apply and Original now recover from a cut at every write point, with no change to
  the on-disk format: files patched by older versions keep working, and half-applied ones are repaired. Known limits are
  documented on the game page (a torn marker write; a junk tail left by the older code, which Steam "Verify" fixes).
- An **Original** interrupted by a power cut (or chosen while the game was running) is now finished by the watcher for
  *Assassin's Creed*, *Charlie Murder*, *Quantum Break* and *Saint Seiya*, as it already was for the loose-file and
  saved-value games. It reacts only to states the plugin itself wrote and writes nothing on an idle check.

### Added
- Maintainer tools: `tools/verify_profile.py` (offline battery on copies of real files with the real engine: pinned hashes,
  whole-tree cycle, power cuts as an exception and as a hard exit, running guard, Steam verify, new build, unknown build,
  damaged art, ownership ledger, timings) and `tools/deck_probe.py` (one read-only SSH round trip: filesystem, owner,
  pinned hashes, running guard, installed plugin).

## [0.3.7] - 2026-11-24

## [0.3.6] - 2026-11-24

### Changed
- Quantum Break: the DualSense picture is recorded as the project owner's own art, as stated by the owner (this corrects the
  wording of 0.3.5, which made no authorship claim).

## [0.3.5] - 2026-11-24

### Added
- A **button catalog** (`catalog/`, `docs/button-catalog.md`, `tools/build_button_catalog.py`): every distinct button drawn or
  mapped for the confirmed games (our own art plus metadata only, never game pixels), searchable with
  `find --role a --size 13x13` (drop-in vs resize/restyle). A test fails when art, profiles or confirmed game pages change
  without rebuilding it.

### Changed
- The release pipeline prints a warning when `Unreleased` is empty, so a release never goes out without notes unnoticed.
- The README credits no longer name the source of the icon sets, which are open-licensed.
- Quantum Break page: the DualSense picture is described as supplied by the project owner, without an authorship claim.

## [0.3.4] - 2026-11-24

### Changed
- Songs for a Hero: the game page and the generated art table now state the origin of the artwork (drawn by the project
  owner), with a re-run overlap check against the game's textures (0 identical pixels).

## [0.3.3] - 2026-11-24

### Added
- Two more games: *10 Second Ninja X* (the game ships PS4 sprites; the plugin sets the one save value that picks them and
  restores the original value) and *Songs for a Hero - Definitive Edition* (24 tiny loose XNB textures replaced by bundled
  pixel art).
- A new strategy for loose files with no spare byte for a marker: each file is replaced atomically, state is read from
  SHA-256, and a ledger of what the plugin wrote (fsynced before the first write) decides what is ours.
- *Quantum Break* now also replaces the Xbox controller picture of the options screen with a DualSense when PlayStation is chosen.

### Changed
- The running-game guard also recognises the same SD card under its other mount name, the real path and the launcher's
  `AppId`, so the watcher no longer writes while such a game runs.
- A status error now outranks "pending" for every strategy, and bundled pictures are validated before the first write.
- Under Original, files that are not the supported build are reported instead of silently shown as fine.

## [0.3.2] - 2026-11-24

### Changed
- Every push to `main` is now released: when the version in `package.json` already has a release at another commit, CI bumps
  the patch version, moves `Unreleased` in this file to the new version, builds the zip with it and publishes the release.
  Before, the release stayed at the first zip of a version while the code moved on (`scripts/ci_version.py`).

### Added
- Open-source packaging: a new README (English and Portuguese), project logo and social preview, `SUPPORTED_GAMES.md`
  (generated from the code), FAQ, contributor guides, issue forms, pull request template, code of conduct and security policy.
- `tools/gen_supported_games.py` keeps the list of supported games in step with the profiles and the game pages.

## [0.3.1]

First published release.

### Added
- Per-game choice of **Original**, **PlayStation** or **Xbox** button prompts from a Decky Quick Access panel, with a marker
  for supported games and a warning for the others.
- Four games: *Assassin's Creed* (entry-level archive patch), *Charlie Murder* (one-byte code switch), *Quantum Break* and
  *Saint Seiya: Soldiers' Soul* (in-place region patches in very large archives).
- Exact undo (checked by whole-file SHA-256 in the tests), crash-safe write order, and a marker inside each changed file.
- A background watcher that re-applies the chosen style after a game update or "Verify integrity of game files".
- Automatic GitHub releases with a zip and a `.sha256` file when the version in `package.json` changes.

[Unreleased]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.10...HEAD
[0.3.10]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.9...v0.3.10
[0.3.9]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.8...v0.3.9
[0.3.8]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.7...v0.3.8
[0.3.7]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.6...v0.3.7
[0.3.6]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.5...v0.3.6
[0.3.5]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.4...v0.3.5
[0.3.4]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.3...v0.3.4
[0.3.3]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/evertonfxavier/button-glyphs/releases/tag/v0.3.1
