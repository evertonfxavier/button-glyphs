#!/usr/bin/env python3
"""Generate the list of supported games from the profile registry and the game pages.

    python3 tools/gen_supported_games.py            write SUPPORTED_GAMES.md and refresh the README tables
    python3 tools/gen_supported_games.py --check    exit 1 if any of them is out of date (CI and tests use this)

Sources of truth - never edit the generated parts by hand:
  * button_glyphs.profiles.PROFILES      which games exist, their styles, the files they touch
  * docs/games/<appid>-*.md              the "Summary" and "Support level" rows of "At a glance",
                                         and every "- NOT verified:" line
The README.md / README.pt-BR.md tables live between <!-- GAMES:START --> and <!-- GAMES:END -->.

Support level (a row of every game page, exactly one of these):
  confirmed         somebody saw the result in the game
  partly confirmed  some of it was seen in game, some only verified by tests and renders
  unconfirmed       applied or verified offline only; nobody has reported the result yet
"""
import argparse
import re
import sys
import urllib.parse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

REPO_URL = "https://github.com/evertonfxavier/button-glyphs"
LEVELS = {
    "confirmed": {"icon": "✅", "en": "confirmed", "pt": "confirmado"},
    "partly confirmed": {"icon": "🟡", "en": "partly confirmed", "pt": "parcialmente confirmado"},
    "unconfirmed": {"icon": "⚪", "en": "unconfirmed", "pt": "não confirmado"},
}
STRATEGY = {
    "forge-glyphs": "Rewrites button textures inside the game's own archive files. The original data stays inside "
                    "them, so **Original** gives back the exact bytes Steam installed.",
    "il-patch": "Changes **one byte** of the game's executable so the game uses its own PlayStation or Xbox art for "
                "every controller. A backup of the executable is kept.",
    "regions": "Rewrites the button textures **in place** inside a very large archive (same size, nothing moves). "
               "Only the changed parts are backed up, not the whole archive.",
    "dxt-regions": "Rewrites the button textures **in place** inside a very large archive (same size, nothing moves). "
                   "Only the changed parts are backed up, not the whole archive.",
}
STYLE_NAME = {"original": "Original", "ps": "PlayStation", "xbox": "Xbox"}
START, END = "<!-- GAMES:START -->", "<!-- GAMES:END -->"


class PageError(Exception):
    pass


def anchor(heading):
    """GitHub's heading anchor: lowercase, drop punctuation, spaces to hyphens."""
    text = re.sub(r"[^\w\s-]", "", heading.lower())
    return re.sub(r"\s", "-", text.strip())


def read_page(root, appid):
    pages = sorted((root / "docs" / "games").glob("%s-*.md" % appid))
    if not pages:
        raise PageError("docs/games/%s-*.md is missing" % appid)
    text = pages[0].read_text()
    rows = {}
    for line in text.splitlines():
        m = re.match(r"^\|\s*([^|]+?)\s*\|\s*(.*?)\s*\|\s*$", line)
        if m and m.group(1) in ("Summary", "Support level", "Status"):
            rows[m.group(1)] = m.group(2)
    for key in ("Summary", "Support level"):
        if not rows.get(key):
            raise PageError("%s has no '%s' row in its 'At a glance' table" % (pages[0].name, key))
    if rows["Support level"] not in LEVELS:
        raise PageError("%s: Support level %r must be one of: %s" % (
            pages[0].name, rows["Support level"], ", ".join(LEVELS)))
    unverified, lines = [], text.splitlines()
    for i, line in enumerate(lines):
        if line.startswith("- NOT verified:"):
            parts = [line.split("NOT verified:", 1)[1].strip()]
            # a bullet may continue on the following indented lines
            for nxt in lines[i + 1:]:
                if nxt.startswith("  ") and nxt.strip():
                    parts.append(nxt.strip())
                else:
                    break
            unverified.append(" ".join(parts))
    return {"page": pages[0].name, "summary": rows["Summary"], "level": rows["Support level"],
            "status": rows.get("Status", ""), "unverified": unverified}


def styles_of(profile):
    if getattr(profile, "kind", "") == "il-patch":
        patch = [s for s in ("ps", "xbox") if s in profile.edits]
    elif getattr(profile, "kind", "") == "forge-glyphs":
        patch = [s for s in ("ps", "xbox") if s in profile.styles]
    else:
        patch = list(getattr(profile, "patch_styles", ("ps",)))
    names = ["Original"] + [STYLE_NAME[s] for s in patch]
    note = ""
    native = getattr(profile, "native_style", None)
    if native and native not in patch:
        note = " (%s is the same as Original: the game already shows it)" % STYLE_NAME[native]
    if profile.kind == "il-patch":
        note = " (Original is automatic: the game decides by controller type)"
    return ", ".join(names) + note


def files_of(profile):
    if getattr(profile, "entry_level", False):
        files = [t["file"] for t in profile.targets()]
        return "`%s` and %d other archive%s" % (files[0], len(files) - 1, "" if len(files) == 2 else "s")
    return "`%s`" % profile.file


def collect(root=ROOT):
    from button_glyphs.profiles import PROFILES
    games = []
    for appid, profile in PROFILES.items():
        page = read_page(root, appid)
        games.append({"appid": appid, "name": profile.name, "profile": profile, **page,
                      "heading": "%s (%s)" % (profile.name, appid)})
    games.sort(key=lambda g: g["name"].lower())
    return games


def issue_link(template, title):
    return "%s/issues/new?template=%s&title=%s" % (REPO_URL, template, urllib.parse.quote_plus(title))


def render_supported_games(root=ROOT):
    games = collect(root)
    out = [
        "# Supported games",
        "",
        "<!-- GENERATED by tools/gen_supported_games.py - do not edit by hand. Run the tool after adding or changing a game. -->",
        "",
        "Button Glyphs works **game by game**: every game stores its button prompts differently, so each one needs its own "
        "*profile*. This is the complete list of games the plugin can change today (%d). It is generated from the code, so "
        "it is always in step with the plugin." % len(games),
        "",
        "> Playing a game that is not listed? [Ask for it](%s). Want to add it yourself? Start with the "
        "[guide to adding a game](docs/adding-a-game.md)." % issue_link("game_request.yml", "[Game request] "),
        "",
        "## How much can you trust each game?",
        "",
        "Nobody can confirm a visual change without looking at the game, so the list is honest about it:",
        "",
        "| Level | Meaning |",
        "|-------|---------|",
        "| %s **confirmed** | Someone opened the game and saw the new prompts working |" % LEVELS["confirmed"]["icon"],
        "| %s **partly confirmed** | Some screens were seen in game; the rest is verified by tests and renders only |" % LEVELS["partly confirmed"]["icon"],
        "| %s **unconfirmed** | Verified by tests and on the real files, but nobody has reported the result in game yet. "
        "[Be the first](%s) |" % (LEVELS["unconfirmed"]["icon"], issue_link("in_game_report.yml", "[In-game report] ")),
        "",
        "Whatever the level, the plugin checks the game files before changing anything, and **Original** puts them back "
        "exactly as Steam installed them (see [how safe is it?](docs/faq.md#is-it-safe-for-my-games)).",
        "",
        "## At a glance",
        "",
        "| Game | Steam ID | Styles | Support |",
        "|------|----------|--------|---------|",
    ]
    for g in games:
        lv = LEVELS[g["level"]]
        out.append("| [%s](#%s) | [%s](https://store.steampowered.com/app/%s) | %s | %s %s |" % (
            g["name"], anchor(g["heading"]), g["appid"], g["appid"], styles_of(g["profile"]).split(" (")[0],
            lv["icon"], lv["en"]))
    out += ["", "## Details", ""]
    for g in games:
        p = g["profile"]
        lv = LEVELS[g["level"]]
        out += [
            "### %s" % g["heading"],
            "",
            "**Support:** %s %s" % (lv["icon"], lv["en"]),
            "",
            g["summary"],
            "",
            "- **Styles:** %s" % styles_of(p),
            "- **How it works:** %s" % STRATEGY[p.kind],
            "- **Files it changes:** %s" % files_of(p),
        ]
        if g["unverified"]:
            out.append("- **Not confirmed yet:** " + " ".join(u.rstrip(".") + "." for u in g["unverified"]))
        out += [
            "- **Links:** [Steam store](https://store.steampowered.com/app/%s) · [technical notes](docs/games/%s) · "
            "[report how it looks in game](%s)" % (g["appid"], g["page"],
                                                  issue_link("in_game_report.yml", "[In-game report] %s" % g["name"])),
            "",
        ]
    out += [
        "## Not on the list?",
        "",
        "- **Request it:** [open a game request](%s). Include how the prompts look today and whether the game is online." % issue_link("game_request.yml", "[Game request] "),
        "- **Add it:** [docs/adding-a-game.md](docs/adding-a-game.md) walks through it, and `python3 tools/scaffold_profile.py` creates the starting files.",
        "- **Only offline games, please.** Games with anti-cheat or online ranked play are out of scope: changing their files can get accounts banned.",
        "",
    ]
    return "\n".join(out)


def render_table(games, lang):
    if lang == "pt":
        head = ["| Jogo | Steam ID | Suporte | Detalhes |", "|------|----------|---------|----------|"]
        details = "notas técnicas"
    else:
        head = ["| Game | Steam ID | Support | Details |", "|------|----------|---------|---------|"]
        details = "technical notes"
    rows = []
    for g in games:
        lv = LEVELS[g["level"]]
        rows.append("| [%s](SUPPORTED_GAMES.md#%s) | %s | %s %s | [%s](docs/games/%s) |" % (
            g["name"], anchor(g["heading"]), g["appid"], lv["icon"], lv[lang if lang == "pt" else "en"], details, g["page"]))
    return "\n".join(head + rows)


def update_block(text, table):
    a, b = text.find(START), text.find(END)
    if a < 0 or b < a:
        raise PageError("missing %s ... %s markers" % (START, END))
    return text[:a + len(START)] + "\n" + table + "\n" + text[b:]


def targets(root=ROOT):
    """(path, new content) for every generated file."""
    games = collect(root)
    result = [(root / "SUPPORTED_GAMES.md", render_supported_games(root))]
    for name, lang in (("README.md", "en"), ("README.pt-BR.md", "pt")):
        path = root / name
        if path.exists():
            result.append((path, update_block(path.read_text(), render_table(games, lang))))
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="do not write; exit 1 if something is stale")
    ap.add_argument("--root", default=str(ROOT))
    args = ap.parse_args(argv)
    root = Path(args.root)
    try:
        files = targets(root)
    except PageError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    stale = [p for p, content in files if not p.exists() or p.read_text() != content]
    if args.check:
        for p in stale:
            print("out of date:", p.relative_to(root), file=sys.stderr)
        if stale:
            print("run: python3 tools/gen_supported_games.py", file=sys.stderr)
        return 1 if stale else 0
    for p, content in files:
        if p in stale:
            p.write_text(content)
            print("updated", p.relative_to(root))
    if not stale:
        print("already up to date")
    return 0


if __name__ == "__main__":
    sys.exit(main())
