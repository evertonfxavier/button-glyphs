# Safety rules

Button Glyphs edits files that belong to people's games, sometimes gigabytes at a time, on a device that can lose power at any
moment. These rules are what makes that acceptable. **A pull request that breaks one will not be merged**, however nice the feature.
Each rule says *why*, so you can judge new situations the rules did not foresee.

| # | Rule | Why |
|---|------|-----|
| 1 | **Analyse on copies.** Copy the files to your computer; only the plugin engine writes game files on the Deck. | A mistaken experiment must never hit the only copy. |
| 2 | **Check before you patch.** Verify ids, sizes, formats, checksums and that the glyphs are really there. An unknown layout means: touch nothing. | A game update would otherwise get garbage written into it. |
| 3 | **Undo must be exact.** *Original* has to give back the very same bytes, and a test must prove it with a whole-file SHA-256. | "Restore" that leaves the game slightly different is a bug nobody can see. |
| 4 | **Build every style from the original**, never from another patched state. | Otherwise styles stack and undo stops being exact. |
| 5 | **Write in an order that survives a power cut:** new data, then the marker, then the index; undo in reverse. | A crash must leave a file that is original or recoverable, never in between. |
| 6 | **Keep the state in the file** (a trailer or a header marker), not only in the config. | Steam updates and "Verify integrity" rewrite files; the plugin must notice by looking at the file. |
| 7 | **All or nothing across files.** Undo what must change, check every file while it is still original, then patch. | A game must never end up half patched. |
| 8 | **Never touch a running or updating game**, and check free disk space first. | Writing into files in use corrupts them; a full disk mid-write is worse. |
| 9 | **If a file no longer matches our marker, only remove our marker.** Never cut a file to a size we recorded earlier. | An update may have changed it; we would damage something that is not ours. |
| 10 | **Never delete a backup that was not verified**, and back up only what changes (an entry, some regions), never a giant file. | The backup can be the only copy of the original. |
| 11 | **Show errors, and show them before "pending".** | A failing sync that reads as "will be applied" hides the problem forever. |
| 12 | **Bump the profile `revision`** whenever its output changes. | Games already patched would never receive the fix. |
| 13 | **No game art, no unlicensed mods in the repository.** Tools output hashes and coordinates; icons are drawn by us. | Licensing, and we have no right to redistribute game assets. |
| 14 | **Standard library only in the backend; bounded memory.** Never read a 56 GB file whole. | The Deck has no pip, and limited memory and time. |
| 15 | **No secrets in commands, files or docs.** | They end up in logs and history. |

## How to apply them when you write code

For every place that writes, ask three questions:

1. **What was checked before this write?** If the answer is "nothing", it is a bug.
2. **If the power dies right after this call, what is on disk, and how does the next run recover?** Write a test that simulates exactly that.
3. **Could this read a patched file as if it were original?** Always derive from the original (rule 4).

`tests/test_entries.py` and `tests/test_regions.py` show the pattern: each strategy has tests for recovery, refusal and exact undo.
New strategies need the same, plus an entry in [architecture.md](architecture.md).
