# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project follows [Semantic Versioning](https://semver.org/).

Add your line under **Unreleased** in the pull request; maintainers move it into a version when they release.

## [Unreleased]

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

[Unreleased]: https://github.com/evertonfxavier/button-glyphs/compare/v0.3.1...HEAD
[0.3.1]: https://github.com/evertonfxavier/button-glyphs/releases/tag/v0.3.1
