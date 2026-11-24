# Security policy

Button Glyphs writes to files inside people's game folders, so "security" here covers two things: classic vulnerabilities, and
**anything that could damage a user's games or data**. We want to hear about both.

## What to report privately

- A way the plugin could write outside a game's own folder (path traversal, symlink tricks).
- A way it could damage a game that the safety checks should have refused, or leave files that **Original** cannot restore exactly.
- Code that runs untrusted input, or leaks anything about the user's machine.
- A problem in the release or CI pipeline (for example a way to publish a modified zip).

Ordinary bugs (a wrong icon, a status message that does not update) are not security issues: please use a normal
[bug report](https://github.com/evertonfxavier/button-glyphs/issues/new?template=bug_report.yml).

## How to report

Use GitHub's private reporting: the repository's **Security** tab → **Report a vulnerability**. Please do not open a public issue or post details until we have had a chance to fix it.

Include: what you found, how to reproduce it (the game, the style, the steps), the plugin version, and what you think the impact is.
You do not need a working exploit.

## What to expect

This is a volunteer project, so these are goals, not guarantees:

- an acknowledgement within **7 days**;
- an assessment and a plan within **14 days**;
- a fix released as soon as it is safe, with credit in the changelog if you want it.

## Supported versions

Only the **latest release** receives fixes.

## Good to know

- The plugin has **no network code** and needs no credentials; it only reads and writes files of games you chose.
- Releases are built by GitHub Actions from the `main` branch, and every zip has a `.sha256` file next to it so you can check your download.
- If you ever see a plugin zip that did not come from this repository's releases, do not install it.
