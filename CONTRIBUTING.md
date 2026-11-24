# Contributing to Button Glyphs

Thank you for wanting to help. This project is young, every game is a new puzzle, and **you do not need to be a programmer to
contribute**. This page shows the ways in, from five minutes to a weekend, and what we expect from every change.

By taking part you agree to follow the [Code of Conduct](CODE_OF_CONDUCT.md).

## Ways to contribute

| I want to… | Effort | How |
|------------|--------|-----|
| Tell whether a game looks right | 5 min | [In-game report](https://github.com/evertonfxavier/button-glyphs/issues/new?template=in_game_report.yml). The most valuable thing you can do: nobody can verify a visual change without seeing the game. |
| Ask for a game | 5 min | [Game request](https://github.com/evertonfxavier/button-glyphs/issues/new?template=game_request.yml) |
| Report a bug | 10 min | [Bug report](https://github.com/evertonfxavier/button-glyphs/issues/new?template=bug_report.yml) |
| Fix a typo, improve the docs, translate | 15 min | Open a pull request (see [Pull requests](#pull-requests)) |
| Add or fix icons | 1 h | [Add or fix icons](#add-or-fix-icons) |
| Add support for a game | a few hours | [docs/adding-a-game.md](docs/adding-a-game.md) |
| Improve the engine or the UI | varies | [docs/development.md](docs/development.md) |

Not sure where to start? Open an issue and say what you would like to do: half-formed ideas are welcome.

## Before you write code

1. **Search the [issues](https://github.com/evertonfxavier/button-glyphs/issues)** to see whether someone is already on it.
2. **For anything bigger than a small fix, open an issue first** and say what you plan. It saves you from building something we cannot merge.
3. **Read [docs/safety-rules.md](docs/safety-rules.md).** This tool edits files that belong to people's games. Those rules are not negotiable.

## Set up

You need Node.js 20+, pnpm 9+ and Python 3.9+ (the backend uses only the standard library).

```bash
git clone https://github.com/<your-fork>/button-glyphs.git
cd button-glyphs
pnpm install
python3 -W error::ResourceWarning -m unittest discover -s tests   # should be green before you change anything
```

The [development guide](docs/development.md) explains the project layout, every command, and how to try your build on a real Steam Deck.

## What we expect from every change

- **Tests pass:** `python3 -W error::ResourceWarning -m unittest discover -s tests` and `npx tsc --noEmit -p .`
- **Safety rules hold** ([docs/safety-rules.md](docs/safety-rules.md)). Anything that writes game files needs tests for exact undo, refusal of unknown layouts, and crash recovery.
- **No game assets** in the repository: no extracted textures, no copies of mods, no game files. Tools produce hashes and coordinates, never art.
- **Generated files are regenerated, not edited:** `SUPPORTED_GAMES.md`, `src/generated/`, and `py_modules/button_glyphs/glyphs/`. Run `python3 tools/gen_supported_games.py` or `pnpm run glyphs`.
- **English** for code, identifiers, docs and UI strings. If you change the English README, update `README.pt-BR.md` too, or say in the pull request that you could not.
- **Honest docs:** a game's page says what was verified and what was not. "Unconfirmed" is a fine answer.
- **A changelog line** under *Unreleased* in [CHANGELOG.md](CHANGELOG.md) for anything a user would notice.

You do not need to bump the version: maintainers do that when they cut a release.

## Add or fix icons

The PlayStation and Xbox icons are separate SVG files in [`assets/svg/ps/`](assets/svg/ps) and [`assets/svg/xbox/`](assets/svg/xbox). The plugin renders them to PNG for the games.

1. Edit or add the SVG. Keep it a **single shape-only file**: no embedded bitmaps, no fonts that are not outlined, a square `viewBox`.
2. Use **your own drawing** or a source whose licence allows redistribution, and say where it comes from in the pull request. Never trace or copy art taken from a game.
3. Name it like its neighbours (`outline-blue-cross.svg`, `plain-L1.svg`).
4. Run `pnpm run glyphs`. It re-renders the 128 px PNGs and updates `src/generated/glyphs.ts`.
5. Look at the result (the PNGs in `py_modules/button_glyphs/glyphs/`) on a dark background; include a screenshot in the pull request.
6. Run the tests: one of them checks that every icon a profile names exists.

If you change an icon that a game already uses, mention which games, because their output changes: the maintainers will bump the profile `revision` so already-patched games get the new art.

## Pull requests

1. **Fork** the repository and create a branch from `main` (`fix-typo-in-faq`, `add-quantum-break-icons`).
2. **Keep it focused.** One topic per pull request is much easier to review than five.
3. **Write clear commits**, in the imperative: `Add Quantum Break profile`, `Fix undo after a crashed write`.
4. **Run the checks** from [What we expect](#what-we-expect-from-every-change). CI runs them again on your pull request.
5. **Open the pull request** and fill in the template. For a new game, include the evidence listed in [docs/adding-a-game.md](docs/adding-a-game.md#10-verify-on-the-real-files) and a screenshot if you have one.
6. **Be patient and kind in review.** We will ask questions; they are about the safety of other people's games, never about you. Push more commits to the same branch; we squash on merge.

Small fixes (typos, links, wording) can go straight to a pull request, no issue needed.

## Releases

Every push to `main` is released automatically: CI tests, builds the zip and creates the GitHub release, bumping the patch version itself when the code moved on. Maintainers only set the version by hand for a bigger change. Details in [docs/development.md](docs/development.md#releases).

## Licensing of contributions

The project is under the [BSD 3-Clause licence](LICENSE). By contributing you agree that your contribution is licensed under the same terms, and that you have the right to submit it (it is your own work, or its licence allows it).

## Security

Found a way this tool could damage a game, or harm a user? Please do **not** open a public issue. Follow [SECURITY.md](SECURITY.md).

## Questions

Open an [issue](https://github.com/evertonfxavier/button-glyphs/issues/new/choose) and say what you are trying to do. There are no silly questions here, and your question usually shows us a gap in the docs.
