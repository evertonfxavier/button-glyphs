# 10 Second Ninja X (435790)

## At a glance
| | |
|--|--|
| Steam appid / install dir | 435790 / `10 Second Ninja X` (installed on the SD card library) |
| Build verified | buildid 1638978: `data.win` 140512524 bytes, SHA-256 `cf7fdc33...a5a008` (pinned in the profile; any other build is refused) |
| Engine / format | GameMaker Studio 1.4, VM bytecode (not YYC), Windows build run through Proton. `data.win` is a plain, unencrypted FORM container (140 MB) |
| Strategy / profile | **config value** (new): edit one key in the game's save file; `profiles/ten_second_ninja_x.py` on `profiles/config_value.py` (`ConfigValueProfile`, `config_level`), revision 1 |
| Styles | ps / xbox / original |
| Status | applied on the Deck from the plugin UI and confirmed ok in game by the user (2026-11-24, their report); clone validation was not run |
| Summary | The game already ships PlayStation 4, Xbox 360, Xbox One and Vita button sprites and picks one from a saved number. The plugin will set that number; no art is changed. |
| Support level | confirmed |

## Where the button art lives
Nothing needs to be drawn. `data.win` (SPRT chunk) holds four 64x64, 12-frame sprites:

| Sprite index | Name | `global.buttons` value |
|--------------|------|------------------------|
| 523 | `spr_XboxButtons` | 523 (Xbox 360, the default on PC) |
| 524 | `spr_PS4Buttons` | 524 (PlayStation 4) |
| 525 | `spr_XboneButtons` | 525 (Xbox One) |
| 526 | `spr_VitaButtons` | 526 (Vita) |

How the game picks (from the recon's disassembly of the CODE chunk, partly decoded, not run):
- `global.buttons` holds the sprite index. `obj_GameStart_Other_2` sets 523; only `os_type == 14` (PS4) and the Vita path change it. There is no pad GUID, name or description check.
- The pause menu option "Controller UI" / "Button UI" cycles 523 -> 524 -> 525 (`obj_HubPause_Step_0`, `gml_Script_SetOption`).
- `PersistentDataObject_Step_0` writes the value to `[storyfile] buttons`; `PersistentDataObject_Other_72` reads it back (the current value is the default), so a stored value wins over the default.

Save file (inside the Proton prefix, not the game folder):
`<library>/steamapps/compatdata/435790/pfx/drive_c/users/steamuser/AppData/Local/_10_Second_Ninja_X_Test/asyncsavegroup/10SNX.sav`
- plain INI text, zero-padded to 32768 bytes; sections `[my_save_data]` and `[storyfile]`;
- one line `buttons="523.000000"` (line 477, inside `[storyfile]`), 18 characters long, same length for 524/525;
- on this Deck the prefix is on the internal drive although the game is on the SD card.

## Strategy decision
Skill `choosing-patch-strategy`, question 1 (does the game already contain both sets?) is **yes**, and the switch is a stored config value, so no archive, texture or code patch is needed.

Rejected:
- Patching the default `pushi16 523` in `data.win` (1 byte at file offset 0xe1b028): an existing save holds `buttons="523.000000"` and overrides the default, so it would only help a fresh profile; it also means copying a 140 MB file for nothing.
- Telling the player to use the in-game option: works without code (to be confirmed in game), but it is not plugin support, which the user asked for.

Chosen: **edit `[storyfile] buttons` in `10SNX.sav`** (ps = 524, xbox = 523), in place, same length, game closed.

Invariants adapted for a user-data file (the game rewrites the save, so a whole-file hash cannot be the undo proof):
- Check before patching: exactly one save found (the Proton prefix is searched in **every** Steam library, because on the Deck the prefix is on the internal drive and the game on the SD card); at most 1 MiB, plain text, only NULs after the text; `[storyfile]` appears once; exactly one line starting with `buttons` in the whole file, and it is `buttons="NNN.NNNNNN"` inside `[storyfile]`; the installed build is pinned (build id, `data.win` size, and its SHA-256, hashed once per build id and cached as `verified_buildid` plus the file's size and mtime in `verified_stamp`; a changed mtime hashes again). Otherwise touch nothing.
- Exact undo: the value found before our first change is recorded (`original_value`, plus `written_value` = what we wrote and `intended_value` while a write is in flight) in the game's config entry **and** in `backups/435790/10SNX.record.json`, both flushed to disk (file and folder) **before** the save is touched; losing `config.json` therefore does not lose the way back (the record file is adopted). "Original" writes `original_value` back only when the file still holds a value we wrote (`written_value` or `intended_value`, not whatever `profile.values` says today), then forgets the record. Only the 10 digits of the value are ever written. If the file already holds the wanted value nothing is written and nothing recorded. If, at restore time, the value is neither the recorded one nor one we wrote (the player picked it in the game, even if it equals one of our styles), it is kept and the record dropped. An invalid record is dropped with the save left alone (no error every minute). Proof by test: ps -> xbox -> ps -> original leaves everything except the value bytes identical at every step, also when the game rewrote other keys in between; a full-file SHA-256 round trip on a copy of the real save is still to do (clone validation).
- The state is read from the file itself (the stored number), not from config alone.
- Never write while the game runs or Steam updates it (the engine skips them). The value is replaced in place with one small write + flush + fsync (same length), then read back and re-parsed. A whole-save copy `backups/435790/10SNX.sav.orig` is made once, before the very first change, and never replaced.
- Status: running > updating > error (no save: "start the game once, then close it"; layout/build refused) > pending > ok. A game never configured (Original, no record) is never in error because of its save: it shows ok with "Start the game once to enable this" (or "Not available yet: ..."). The value in the file decides `applied` (524 = PlayStation, 523 = Xbox, anything else = Original).
- A game left on Original with a recorded value (Original was chosen while the game was running) is finished by the periodic sync once the game closes; a game on Original with no record is never touched.

## What the profile changes (and what it leaves alone)
Changes: the digits of the `buttons` value (524.000000 / 523.000000), nothing else in the save. Leaves alone: every other save key, scores, ghosts, `data.win`, the executable. Adds: `backups/435790/10SNX.sav.orig` (first change only) and `original_value` / `verified_buildid` in the game's config entry.

## How it is verified
Done: `tests/test_ten_second_ninja_x.py` (synthetic two-library Steam home, fake `data.win`, INI save with the real layout): per-style apply, byte-identical rest of the file at every step, rewrites by the game between steps, restore of a non-523 start (525), refusals (no save, save in two libraries, two `buttons` lines, key in the wrong section, binary/oversized file, unknown build id, wrong `data.win` size, same size but different bytes, missing `data.win`), running/updating skipped, single backup, crash after recording, hash cached once per build id. The pinned size and SHA-256 were computed from a copy of the real `data.win`.
Planned: clone validation (`python3 tools/clone_validate.py 435790 <dir>`; `<dir>` holds `data.win` and the save at `drive_c/users/steamuser/AppData/Local/_10_Second_Ninja_X_Test/asyncsavegroup/10SNX.sav`) on a copy of the real save fetched from the Deck (full-file SHA-256 of an untouched copy before/after ps -> xbox -> ps -> original), then the user checks the prompts in game.

## Verified / not verified
- Verified: sprite names and option strings are present in `data.win`; the save exists on the Deck with the `buttons` line (read-only); the profile's logic on synthetic fixtures; the pinned `data.win` size and SHA-256 on the copy used for recon.
- Verified in game: the user applied PlayStation from the plugin (save `buttons` 523 -> 524, log '435790: ps applied to the save (user)', 2026-11-24 00:27) and reported it ok; which screens they looked at was not recorded.
- NOT verified: whether the game rewrites `buttons` from its own default on a later start; whether the save is checksummed (no clone validation was run); anything about Proton controller behaviour.

## Deck state
Plugin v0.3.1 was installed on the Deck on 2026-11-24 (with the Quantum Break work); the user then applied PlayStation to this game from the plugin UI: save `buttons` was `523.000000` (original), now `524.000000`; confirmed ok in game by the user (2026-11-24). Re-read the real state with `/game-status`.

## Open questions and caveats
- If the player changes the option in the game menu (e.g. to Xbox One, 525) while PlayStation or Xbox is chosen in the plugin, the periodic check puts the plugin's value back after the game closes. Choose Original to use the in-game option freely.
- A fresh install without a save is an error state ("Save file not found: start the game once, then close it"); a save without the `buttons` line, or with it twice, is refused with a message.
- The plugin only writes the save while the game is closed; the game rewrites it on exit, which keeps our value because it is the value the game itself stores.
- The record lives in the plugin's config and in `backups/435790/10SNX.record.json`; if both are lost while the save holds our value, Original cannot know what the player had; `10SNX.sav.orig` is the manual way back.
- The save is searched with `os.path.realpath` de-duplication only: two library paths that are symlinks to the same folder count once; anything else holding a second copy of the save is refused as ambiguous (accepted).
- The in-game Button UI option is reset to the plugin's style while PlayStation or Xbox is chosen (said in the profile's `original_hint`); choose Original to use the in-game option freely.
- Text is checked byte-wise (control characters refused, high bytes tolerated), not decoded, so saves with accents are fine.
- Steam Cloud: no sync files were found for this app on the Deck.

## How to re-derive the data
Copy `data.win` from the install folder to a scratch folder and parse the FORM chunks (SPRT/STRG/CODE); the recon's helper scripts live only in the session scratchpad. The pin for a new build is `os.path.getsize` and `sha256sum` of that `data.win`; add it to `builds` in `profiles/ten_second_ninja_x.py` only after re-checking the sprite indices.

## Learnings
- The game ships a PS4 sprite set and a stored option; the recon found this by reading sprite names in the string table before looking at any texture.
- A strategy that edits user data cannot prove undo with a whole-file hash (the game rewrites the file): record the value before the first change, persist the record before writing, and test that everything except the value bytes is identical at every step.
