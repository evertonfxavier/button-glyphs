## What does this change?

<!-- One or two sentences. Link the issue if there is one: "Closes #12". -->

## Type

- [ ] Documentation or translation
- [ ] Bug fix
- [ ] Icons
- [ ] New game or change to a game profile
- [ ] Engine, tools or UI
- [ ] Other:

## Checklist

- [ ] `python3 -W error::ResourceWarning -m unittest discover -s tests` passes
- [ ] `npx tsc --noEmit -p .` passes (if I touched `src/`)
- [ ] I regenerated generated files instead of editing them (`tools/gen_supported_games.py`, `pnpm run glyphs`)
- [ ] I added a line under **Unreleased** in `CHANGELOG.md` (if a user would notice)
- [ ] No game assets (textures, mod files, game files) are in this pull request
- [ ] I followed [docs/safety-rules.md](../docs/safety-rules.md)

## If this writes game files (profile, strategy, engine)

- [ ] Tests prove **exact undo** (whole-file SHA-256 after a full cycle)
- [ ] An unknown layout is refused and nothing is written
- [ ] A simulated crash mid-write recovers
- [ ] I bumped the profile `revision` because its output changed
- [ ] The game's page in `docs/games/` says honestly what was verified and what was not

## Evidence

<!-- Screenshots (before / after), a contact sheet, or the output of tools/clone_validate.py. Optional for docs changes. -->
