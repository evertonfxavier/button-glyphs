# Game catalog

One page per game, named `<appid>-<slug>.md`. Pages hold **facts about that game** (where the
art lives, ids/offsets, what changes, what was and was not verified). Generic lessons go to
`CLAUDE.md` (`tools/learn.py`); long procedures go to the skills in `.claude/skills/`.

| appid | Game | Strategy | Profile | In-game confirmed | Page |
|-------|------|----------|---------|-------------------|------|
| 15100 | Assassin's Creed (2008) | entry-level forge | `ac1.py` rev 3 | font glyphs yes (PS and Xbox); menu `Conrtols_*` not yet | [15100](15100-assassins-creed.md) |
| 405290 | Charlie Murder | IL patch (1 byte) | `charlie_murder.py` rev 1 | yes | [405290](405290-charlie-murder.md) |
| 474960 | Quantum Break | in-place regions (raw BGRA) | `quantum_break.py` rev 2 | yes (controls-screen photo `ctrllayout_*` not redrawn) | [474960](474960-quantum-break.md) |
| 348710 | Saint Seiya: Soldiers' Soul | in-place regions (DXT5/CPK) | `saint_seiya.py` rev 1 | yes | [348710](348710-saint-seiya.md) |
| 435790 | 10 Second Ninja X | config value (one key in the save file) | `ten_second_ninja_x.py` rev 1 | yes | [435790](435790-10-second-ninja-x.md) |
| 389170 | Songs for a Hero - Definitive Edition | loose files (24 tiny XNB textures, state from SHA-256) | `songs_for_a_hero.py` rev 1 | not yet (offline tests only) | [389170](389170-songs-for-a-hero.md) |

Keep this table in sync with `PROFILES` (`/catalog` checks it). "In-game confirmed" means the
user saw the result in the game, not that tests pass.

## Adding a page
`python3 tools/scaffold_profile.py ...` creates the profile skeleton and a page from
[`_template.md`](_template.md). Fill every section; write "unknown" rather than guess.
