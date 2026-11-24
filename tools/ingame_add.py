#!/usr/bin/env python3
"""Maintain the list of games that already have an Xbox/PlayStation option in their own settings ("In game").

    python3 tools/ingame_add.py add <appid> [--where "<menu path in the game>"] [--note "..."] [--name "<name>"]
                                [--reported-by "the project owner"] [--on YYYY-MM-DD] [--dry-run] [--no-regen]
    python3 tools/ingame_add.py update <appid> [--where "..."] [--note "..."] [--dry-run] [--no-regen]
    python3 tools/ingame_add.py remove <appid> [--dry-run] [--no-regen]
    python3 tools/ingame_add.py list

    --file PATH (or env BG_INGAME_FILE) points at another registry file; tests use it so the real one is never touched.

The registry is py_modules/button_glyphs/ingame_games.json. Every entry is a REPORT, not something Button Glyphs verified.
The plugin never changes these games: it only lists them.

`add` reads the game's name from the Steam Deck's appmanifest (read-only: `deck.sh --check` first, then a tiny program on
stdin of `deck.sh 'python3 -'` that carries no game path). If the Deck cannot be reached or the app is not installed it
stops and asks for --name: a name is never guessed.

It REFUSES (exit 1, with the reason) to add a game that is already in the file (use `update`) or that has a plugin profile
(a game cannot be both). Every write is first checked with the same validator the plugin uses (button_glyphs.ingame), then
written atomically (temp file in the same folder, fsync, os.replace) keeping the file's indentation and key order. After a
real write on the real registry it regenerates SUPPORTED_GAMES.md (tools/gen_supported_games.py; --no-regen skips it).

Exit codes: 0 ok, 1 refused, 2 the name could not be read from the Deck and no --name was given, 64 usage.
"""
import argparse
import datetime
import difflib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))
sys.path.insert(0, str(ROOT / "tools"))

from button_glyphs import ingame  # noqa: E402

DEFAULT_REPORTED_BY = "the project owner"
KEY_ORDER = ("appid", "name", "reported_by", "reported_on", "where", "note")
ENV_FILE = "BG_INGAME_FILE"
STEAM_PY = ROOT / "py_modules" / "button_glyphs" / "steam.py"

# The program sent to the Deck on stdin. Read-only: it only lists the Steam library manifests (no game folder is opened)
# and prints ONE JSON object. The appid travels inside the script, so the command line names nothing.
REMOTE_SOURCE = '''
import json, os, types
def remote_main(steam_source, appid):
    steam = types.ModuleType("steam")
    try:
        exec(compile(steam_source, "steam.py", "exec"), steam.__dict__)
        app = steam.installed_apps(os.environ.get("INGAME_ADD_HOME") or os.path.expanduser("~")).get(appid)
        result = {"ok": bool(app), "appid": appid, "name": (app or {}).get("name", "")}
        if not app:
            result["error"] = "the app is not installed (no appmanifest_%s.acf in any library)" % appid
    except Exception as exc:
        result = {"ok": False, "appid": appid, "error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(result, sort_keys=True))
'''


class Refused(Exception):
    """A rule said no: the message is the reason (exit 1)."""

    def __init__(self, message, entry=None):
        super().__init__(message)
        self.entry = entry


class NoName(Exception):
    """The name could not be read from the Deck (exit 2)."""


class _Parser(argparse.ArgumentParser):
    def error(self, message):                # exit 2 is reserved for "no name from the Deck"
        self.print_usage(sys.stderr)
        print("%s: error: %s" % (self.prog, message), file=sys.stderr)
        sys.exit(64)


# ---------------------------------------------------------------------------------------------- the file
def default_file():
    return os.environ.get(ENV_FILE) or ingame.PATH


def read_registry(path):
    """(raw text, raw entries as written). The entries go through the plugin's own validator; a bad file is refused."""
    try:
        with open(path, encoding="utf-8") as f:
            text = f.read()
    except OSError as exc:
        raise Refused("cannot read %s: %s" % (path, exc))
    try:
        raw = json.loads(text)
        ingame.validate(raw)
    except (ValueError, ingame.InGameError) as exc:         # InGameError is a ValueError; JSON errors too
        raise Refused("%s is not a valid In game list (fix it by hand first): %s" % (os.path.basename(path), exc))
    return text, raw


def detect_indent(text):
    m = re.search(r"^([ \t]+)\S", text, re.M)
    if not m:
        return 2
    return "\t" if "\t" in m.group(1) else len(m.group(1))


def key_order(raw):
    """The key order of the first entry (so a new entry looks like its neighbours), the standard order for the rest."""
    first = list(raw[0]) if raw else []
    return first + [k for k in KEY_ORDER if k not in first]


def render(entries, indent):
    """The file's text: entries sorted by appid, the given indentation, UTF-8 (no \\u escapes), one trailing newline."""
    ordered = sorted(entries, key=lambda e: int(e["appid"]))
    return json.dumps(ordered, indent=indent, ensure_ascii=False) + "\n"


def new_entry(raw, appid, name, reported_by, reported_on, where, note):
    values = {"appid": appid, "name": name, "reported_by": reported_by, "reported_on": reported_on,
              "where": where, "note": note}
    first = raw[0] if raw else None
    keep = [k for k in key_order(raw) if k in values and (first is None or k in first or values[k] or k in KEY_ORDER[:4])]
    return {k: values[k] for k in keep}


def atomic_write(path, text):
    """Temp file in the same folder, fsync, os.replace, fsync of the folder. A failure leaves the file as it was."""
    path = os.path.abspath(path)
    folder = os.path.dirname(path)
    try:
        mode = os.stat(path).st_mode & 0o777
    except OSError:
        mode = 0o644
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=".ingame_games.", suffix=".tmp")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(text.encode("utf-8"))
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, mode)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    try:
        dfd = os.open(folder, os.O_RDONLY)
        try:
            os.fsync(dfd)
        finally:
            os.close(dfd)
    except OSError:
        pass


# ---------------------------------------------------------------------------------------------- rules
def check_appid(appid):
    """The plugin's own validator decides what a good appid is (digits only); the other fields are placeholders."""
    try:
        ingame.validate([{"appid": appid, "name": "x", "reported_by": "x", "reported_on": "2000-01-01"}])
    except ingame.InGameError as exc:
        raise Refused(str(exc).split(": ", 1)[-1])


def registered_profile(appid):
    from button_glyphs.profiles import PROFILES
    return PROFILES.get(appid)


def deck_name(appid):
    """The game's name from the Deck's appmanifest. NoName (never a guess) when the Deck or the app is not there."""
    import deck_probe as dp
    try:
        reachable, why = dp.check_reachable()
    except (OSError, ValueError) as exc:     # no deck.sh, a timeout: the same as an unreachable Deck
        reachable, why = False, "%s: %s" % (type(exc).__name__, str(exc)[:120])
    if not reachable:
        raise NoName("the Deck is unreachable (%s). Wake it up and run .claude/scripts/deck.sh --check, "
                     "or pass --name \"<the game's name>\"." % (why or "no answer"))
    steam_source = STEAM_PY.read_text(encoding="utf-8")
    script = "%s\nremote_main(%r, %r)\n" % (REMOTE_SOURCE, steam_source, appid)
    try:
        done = dp.run_remote(script, timeout=120)
        res = dp.parse_result(done.stdout)
    except Exception as exc:                  # a timeout, garbage on stdout, a missing deck.sh: never fatal, ask for --name
        raise NoName("could not read the name from the Deck (%s: %s). Pass --name \"<the game's name>\"." % (
            type(exc).__name__, str(exc)[:120]))
    if not res.get("ok") or not str(res.get("name", "")).strip():
        raise NoName("could not get the name of app %s from the Deck: %s. Pass --name \"<the game's name>\"." % (
            appid, res.get("error") or "its manifest has no name"))
    return " ".join(str(res["name"]).split())


def describe(entry):
    return json.dumps(entry, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------------------------- commands
def plan_add(ns, path):
    text, raw = read_registry(path)
    appid = ns.appid
    check_appid(appid)
    existing = next((e for e in raw if e["appid"] == appid), None)
    if existing:
        raise Refused("appid %s is already in the list (use `update %s --where ... --note ...` to change it)" % (appid, appid),
                      existing)
    profile = registered_profile(appid)
    if profile:
        raise Refused("this game is already supported by the plugin (%s): it cannot also be In game. "
                      "Remove its profile first if it should be In game instead." % profile.name)
    name = " ".join(ns.name.split()) if ns.name is not None else deck_name(appid)
    entry = new_entry(raw, appid, name, ns.reported_by, ns.on, ns.where or "", ns.note or "")
    return text, raw, entry, raw + [entry], 'added %s "%s"' % (appid, name)


def plan_update(ns, path):
    text, raw = read_registry(path)
    appid = ns.appid
    check_appid(appid)
    old = next((e for e in raw if e["appid"] == appid), None)
    if old is None:
        raise Refused("appid %s is not in the list (use `add %s ...`)" % (appid, appid))
    entry = dict(old)
    for key in ("where", "note"):
        value = getattr(ns, key)
        if value is not None:
            if key not in entry and not value:
                continue
            entry[key] = value
    return text, raw, entry, [entry if e is old else e for e in raw], 'updated %s "%s"' % (appid, old["name"])


def plan_remove(ns, path):
    text, raw = read_registry(path)
    appid = ns.appid
    check_appid(appid)
    old = next((e for e in raw if e["appid"] == appid), None)
    if old is None:
        raise Refused("appid %s is not in the list" % appid)
    return text, raw, old, [e for e in raw if e is not old], 'removed %s "%s"' % (appid, old["name"])


def regenerate():
    import gen_supported_games
    return gen_supported_games.main([])


def finish(ns, path, plan):
    text, raw, entry, entries, what = plan
    try:
        ingame.validate(entries)                        # EVERY write is checked with the plugin's own validator first
    except ingame.InGameError as exc:
        raise Refused("the result would not be a valid In game list, nothing written: %s" % exc)
    new_text = render(entries, detect_indent(text))
    if new_text == text:
        print("Nothing to change: %s already looks like that." % os.path.basename(path))
        return 0
    if ns.dry_run:
        print("Dry run (nothing written). Result:")
        print(describe(entry))
        diff = difflib.unified_diff(text.splitlines(), new_text.splitlines(), "before", "after", lineterm="", n=2)
        print("\n".join(diff))
        return 0
    atomic_write(path, new_text)
    regen = "SUPPORTED_GAMES.md not regenerated (--no-regen)"
    if not ns.no_regen:
        if os.path.abspath(path) != os.path.abspath(ingame.PATH):
            regen = "SUPPORTED_GAMES.md not regenerated (another --file than the shipped registry)"
        else:
            code = regenerate()
            if code != 0:
                print("The list was written, but regenerating SUPPORTED_GAMES.md failed (exit %s): "
                      "run `python3 tools/gen_supported_games.py` and fix what it says." % code, file=sys.stderr)
                return 1
            regen = "SUPPORTED_GAMES.md regenerated"
    print("Changed: %s in %s (%s)." % (what, os.path.basename(path), regen))
    print("Next: run the tests (python3 -W error::ResourceWarning -m unittest discover -s tests).")
    print("Remember: the plugin never changes this game; record how you verified it in --note.")
    return 0


def cmd_list(ns, path):
    _, raw = read_registry(path)
    if not raw:
        print("No In game games yet.")
        return 0
    for e in sorted(raw, key=lambda e: int(e["appid"])):
        print("%-9s %s (reported by %s on %s)%s" % (e["appid"], e["name"], e["reported_by"], e["reported_on"],
                                                  ": " + e["where"] if e.get("where") else ""))
        if e.get("note"):
            print("          note: %s" % e["note"])
    return 0


def build_parser():
    ap = _Parser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", parser_class=_Parser)
    sub.required = True
    common = _Parser(add_help=False)
    common.add_argument("--file", help="another registry file (default: the shipped one, or env %s)" % ENV_FILE)

    def writer(name, help_):
        p = sub.add_parser(name, parents=[common], help=help_)
        p.add_argument("appid")
        p.add_argument("--dry-run", action="store_true", help="print the resulting entry and the diff; write nothing")
        p.add_argument("--no-regen", action="store_true", help="do not regenerate SUPPORTED_GAMES.md")
        return p

    p = writer("add", "register a game that has the option in its own settings")
    p.add_argument("--where", help="where in the game's settings the option is, as the player saw it")
    p.add_argument("--note", help="how it was verified, restart needed, what Auto does...")
    p.add_argument("--name", help="the game's name (default: read from the Deck's appmanifest)")
    p.add_argument("--reported-by", default=DEFAULT_REPORTED_BY)
    p.add_argument("--on", type=_iso_date, default=None, help="date of the report, YYYY-MM-DD (default: today)")
    p = writer("update", "change where/note of a registered game")
    p.add_argument("--where")
    p.add_argument("--note")
    writer("remove", "take a game out of the list")
    sub.add_parser("list", parents=[common], help="print the registered games")
    return ap


def _iso_date(text):
    try:
        return datetime.date.fromisoformat(text).isoformat()
    except ValueError:
        raise argparse.ArgumentTypeError("%r is not a date (YYYY-MM-DD)" % text)


def main(argv=None):
    ap = build_parser()
    ns = ap.parse_args(argv)
    path = ns.file or default_file()
    if hasattr(ns, "appid"):
        ns.appid = ns.appid.strip()
    if ns.cmd == "update" and ns.where is None and ns.note is None:
        ap.error("update needs --where and/or --note")
    if ns.cmd == "add":
        if ns.on is None:
            ns.on = datetime.date.today().isoformat()
        if not ns.reported_by.strip():
            ap.error("--reported-by must not be empty")
    try:
        if ns.cmd == "list":
            return cmd_list(ns, path)
        plan = {"add": plan_add, "update": plan_update, "remove": plan_remove}[ns.cmd](ns, path)
        return finish(ns, path, plan)
    except Refused as exc:
        print("refused: %s" % exc, file=sys.stderr)
        if exc.entry:
            print(describe(exc.entry))
        return 1
    except NoName as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2
    except OSError as exc:
        print("refused: could not write %s: %s (the list is unchanged)" % (path, exc), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
