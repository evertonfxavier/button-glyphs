# FAQ and troubleshooting

Answers for people who *use* Button Glyphs. Looking for the engineering side? See [architecture.md](architecture.md).

**Jump to:** [The basics](#the-basics) · [Is it safe for my games?](#is-it-safe-for-my-games) · [Using it](#using-it) ·
[Something is not working](#something-is-not-working) · [Removing the plugin](#removing-the-plugin) ·
[Where things live](#where-things-live)

---

## The basics

### What does Button Glyphs do?
It changes **what the button prompts look like** in a game ("press ✕" instead of "press A", or the other way round).
It does **not** change what the buttons do: your controls stay exactly as they are.

### Why do I need a plugin for that? Does the Deck not already show PlayStation buttons?
The Steam Deck introduces itself to games as an Xbox-style controller, so most games draw Xbox prompts. A few games have
PlayStation art but only use it when they detect a PlayStation controller. Button Glyphs changes the game's own files so the
prompts match your taste.

### Does it need the internet? Does it send anything anywhere?
No. The plugin works offline and has no network code.

### Do I need a PlayStation controller?
No. It only changes the pictures, and it works with the Deck's built-in controls or any controller.

### Why only some games?
Every game stores its prompts differently (inside archives, inside code, in textures with different layouts). Each supported
game needed someone to work out where, and how to change it without breaking anything. The list is in
[SUPPORTED_GAMES.md](../SUPPORTED_GAMES.md), and you can [ask for more](https://github.com/evertonfxavier/button-glyphs/issues/new?template=game_request.yml).

### What does "unconfirmed" mean next to a game?
Nobody has reported how it looks in the game yet. It was verified with tests and on the real game files, but a visual change can only be
confirmed by looking at the screen. If you try it, please [tell us](https://github.com/evertonfxavier/button-glyphs/issues/new?template=in_game_report.yml).

---

## Is it safe for my games?

It was designed so that you can always go back. In detail:

- **It checks before it touches.** Each game profile knows what the original files look like (sizes, formats, checksums of the parts
  it changes). If anything differs, for example after a game update, it changes **nothing** and says so.
- **It never changes a running game.** It waits until you close the game, and it waits while Steam is updating it.
- **Going back is exact.** *Original* restores the very same bytes Steam installed. For most games the original data is kept inside the
  game's own files or in a small backup; the plugin does not copy gigabytes around.
- **Power cuts are handled.** The plugin writes in an order that leaves a file that is either original or recoverable, and every
  file it changes carries a small marker saying what was done.
- **It stays in the game's folder.** It does not touch the system, Steam itself or other plugins.
- **Online games are out of scope.** Changing files of a game with anti-cheat can get an account banned. The plugin only supports
  games where that is not a concern, and each game's notes say so.

What it cannot promise: it cannot know how a game will react to a texture it did not expect. That is why every supported game was
verified on the real files, and why you can verify the result in Steam (*Properties → Installed Files → Verify integrity of game files*) at any time.

---

## Using it

### How do I go back to the original?
Open Button Glyphs, pick the game, choose **Original**. Done: the files are restored exactly.
You can also use Steam: *game → Properties → Installed Files → Verify integrity of game files*.

### The game got an update. What happens?
Steam replaces the files, which removes our changes. Within about a minute the plugin notices that the game is back to its original files and
**applies your chosen style again**. If the update changed the game so much that the plugin no longer recognises it, it leaves the game untouched
and shows *this game version is not supported yet*. [Report it](https://github.com/evertonfxavier/button-glyphs/issues/new?template=bug_report.yml) and include the game's build number.

### I used "Verify integrity of game files". What happens?
Steam puts the original files back. The plugin sees that and re-applies your style, so your choice sticks.

### How much disk space does it use?
It depends on the game and the style:
- *Charlie Murder*: about 2 MB (a backup of the game's small program file).
- *Assassin's Creed*: about 180 MB more while PlayStation is active (the new texture data is added to the archives; *Original* removes it again).
- *Quantum Break*: about 36 MB of backups; the 56 GB archive does not grow.
- *Saint Seiya*: about 42 MB of backups; the archive does not grow.

### Can I use it while the game is open?
You can pick a style, but the game's files are only changed when it is closed. The status line tells you *Game is running* until then.

### Does it work in Desktop Mode?
It edits the game's files, so the result is the same however you start the game. The plugin's own screen lives in Game Mode (Quick Access menu).

---

## Something is not working

### Button Glyphs does not appear in the Decky list after installing
Restart Steam (or reboot the Deck) so Decky reloads its plugins. Make sure the file you picked is the `.zip` from the release, not the source code archive.

### My game is not in the dropdown
The dropdown lists the games installed in your Steam libraries, including the SD card. If it is empty, check that the game is fully installed
(not "update required") and open the plugin again.

### My game says "Not supported yet"
That means no profile exists for that game. [Request it](https://github.com/evertonfxavier/button-glyphs/issues/new?template=game_request.yml) or see [how to add it](adding-a-game.md).

### The status is stuck on "…will be applied"
Look at the line under the dropdown or at the *Details* box of the plugin; if there is an error it is shown there. Common reasons: the game is running,
Steam is updating it, or the game folder is on a drive that is not mounted. Press **Check for game updates now** to retry immediately.

### I applied a style but the game still shows the old buttons
- Close the game completely (not only to the menu) and start it again.
- Some screens use pictures the plugin does not redraw (for example, drawings of a whole controller in a help screen). The game's page in
  [SUPPORTED_GAMES.md](../SUPPORTED_GAMES.md) lists what is and is not changed.
- If it still looks wrong, [tell us which screen](https://github.com/evertonfxavier/button-glyphs/issues/new?template=in_game_report.yml).

### A button looks wrong, odd or cut off
Please report it with a screenshot (Steam + R1 on the Deck) and the screen where it appears: that is exactly the kind of detail nobody can find without playing.

### The game crashes or does not start after applying a style
1. In Button Glyphs choose **Original** for that game (or use Steam's *Verify integrity of game files*).
2. [Open a bug report](https://github.com/evertonfxavier/button-glyphs/issues/new?template=bug_report.yml) with the game, the style and the log (see below). Please include the game's build number (Steam → game → Properties → Updates).

### How do I get the log for a bug report?
On the Deck, open a terminal (Desktop Mode → Konsole) and run:

```bash
tail -n 80 "$(ls -t ~/homebrew/logs/Button\ Glyphs/*.log | head -1)"
```

Attach the output. The plugin's `config.json` (see below) is also useful, and contains no personal data.

---

## Removing the plugin

1. For every game, set the style to **Original** and wait for *Original glyphs active*.
2. In Decky, open the plugin list and remove **Button Glyphs**.

If you removed the plugin first, nothing is lost: Steam's *Verify integrity of game files* restores the original files of any game you changed.

---

## Where things live

| What | Where (on the Deck) |
|------|---------------------|
| Plugin | `~/homebrew/plugins/Button Glyphs/` |
| Your choices | `~/homebrew/settings/Button Glyphs/config.json` |
| Backups | `~/homebrew/data/Button Glyphs/backups/<steam app id>/` |
| Logs | `~/homebrew/logs/Button Glyphs/` |
| Games | `~/.local/share/Steam/steamapps/common/` (and your SD card / extra libraries) |

Still stuck? [Open an issue](https://github.com/evertonfxavier/button-glyphs/issues/new/choose): there is a form for each case.
