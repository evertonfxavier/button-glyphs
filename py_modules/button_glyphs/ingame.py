"""Games that already offer an Xbox/PlayStation button option in their own settings.

The plugin never touches such a game: it only lists it, so the UI can say "set it in the game". The list is one
hand-maintained file (ingame_games.json, shipped inside py_modules) read both by the engine and by
tools/gen_supported_games.py. Every entry is a report, not something Button Glyphs verified: `reported_by` and
`reported_on` say who claimed it and when.
"""
import datetime
import json
import os

PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ingame_games.json")
REQUIRED = ("appid", "name", "reported_by", "reported_on")
OPTIONAL = ("where", "note")


class InGameError(ValueError):
    pass


def validate(entries):
    """The entries as a list of dicts (optional fields defaulted to ""), or InGameError naming the problem."""
    if not isinstance(entries, list):
        raise InGameError("the list of in-game games must be a JSON list")
    seen, out = set(), []
    for i, entry in enumerate(entries):
        where = "entry %d" % i
        if not isinstance(entry, dict):
            raise InGameError("%s is not an object" % where)
        unknown = set(entry) - set(REQUIRED) - set(OPTIONAL)
        if unknown:
            raise InGameError("%s has unknown fields: %s" % (where, ", ".join(sorted(unknown))))
        for key in REQUIRED:
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                raise InGameError("%s: '%s' must be a non-empty string" % (where, key))
        for key in OPTIONAL:
            if not isinstance(entry.get(key, ""), str):
                raise InGameError("%s: '%s' must be a string" % (where, key))
        appid = entry["appid"]
        if not appid.isascii() or not appid.isdigit():
            raise InGameError("%s: appid %r must be digits only" % (where, appid))
        if appid in seen:
            raise InGameError("%s: appid %s is listed twice" % (where, appid))
        seen.add(appid)
        try:
            datetime.date.fromisoformat(entry["reported_on"])
        except ValueError:
            raise InGameError("%s: reported_on %r must be an ISO date (YYYY-MM-DD)" % (where, entry["reported_on"]))
        out.append({**{k: "" for k in OPTIONAL}, **entry})
    return out


def load(path=PATH):
    """Validated entries from the file; InGameError (or OSError) when it is unusable."""
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except ValueError as exc:
        raise InGameError("%s is not valid JSON: %s" % (os.path.basename(path), exc))
    return validate(data)


def by_appid(path=PATH):
    return {e["appid"]: e for e in load(path)}
