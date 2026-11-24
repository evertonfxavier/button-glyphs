# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

Add your line under **Unreleased** in the pull request; maintainers move it into a version when they release.

## [Unreleased]

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

[Unreleased]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.3...HEAD
[0.3.3]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.2...v0.3.3
[0.3.2]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.1...v0.3.2
[0.3.1]: https://github.com/evertonfxavier/button-glyphs/releases/tag/v0.3.1
