"""Steam library discovery: libraries, installed apps, update state, running games."""
import os
import re

# Steam tools that show up as "installed apps" but are not games.
TOOL_NAME = re.compile(
    r"^(Proton\b|Steam Linux Runtime|Steamworks Common Redistributables|SteamVR\b)", re.I
)
TOOL_APPIDS = {"228980", "1070560", "1391110", "1628350", "4183110"}

STATE_FULLY_INSTALLED = 4


def parse_vdf(text):
    """Minimal parser for Valve's text KeyValues (enough for libraryfolders/appmanifest)."""
    tokens = re.findall(r'"((?:[^"\\]|\\.)*)"|([{}])', text)
    root = {}
    stack = [root]
    key = None
    for quoted, brace in tokens:
        if brace == "{":
            child = {}
            stack[-1][key] = child
            stack.append(child)
            key = None
        elif brace == "}":
            if len(stack) > 1:
                stack.pop()
            key = None
        elif key is None:
            key = quoted
        else:
            stack[-1][key] = quoted.replace('\\\\', '\\')
            key = None
    return root


def steam_roots(home):
    seen, roots = set(), []
    for cand in (".local/share/Steam", ".steam/steam", ".steam/root"):
        path = os.path.join(home, cand)
        if os.path.isdir(os.path.join(path, "steamapps")):
            real = os.path.realpath(path)
            if real not in seen:
                seen.add(real)
                roots.append(path)
    return roots


def library_paths(home):
    """All Steam library folders (internal, SD card, extra drives)."""
    paths, seen = [], set()
    for root in steam_roots(home):
        candidates = [root]
        vdf = os.path.join(root, "steamapps", "libraryfolders.vdf")
        try:
            with open(vdf, encoding="utf-8", errors="replace") as f:
                data = parse_vdf(f.read()).get("libraryfolders", {})
            for entry in data.values():
                if isinstance(entry, dict) and entry.get("path"):
                    candidates.append(entry["path"])
        except OSError:
            pass
        for path in candidates:
            real = os.path.realpath(path)
            if real not in seen and os.path.isdir(os.path.join(path, "steamapps")):
                seen.add(real)
                paths.append(path)
    return paths


def installed_apps(home):
    """Installed apps across every library, tools filtered out."""
    apps = {}
    for lib in library_paths(home):
        steamapps = os.path.join(lib, "steamapps")
        try:
            names = os.listdir(steamapps)
        except OSError:
            continue
        for fname in names:
            if not (fname.startswith("appmanifest_") and fname.endswith(".acf")):
                continue
            try:
                with open(os.path.join(steamapps, fname), encoding="utf-8", errors="replace") as f:
                    state = parse_vdf(f.read()).get("AppState", {})
            except OSError:
                continue
            appid = state.get("appid", "")
            name = state.get("name", "")
            if not appid or appid in TOOL_APPIDS or TOOL_NAME.match(name):
                continue
            installdir = state.get("installdir", "")
            apps[appid] = {
                "appid": appid,
                "name": name,
                "library": lib,
                "install_path": os.path.join(steamapps, "common", installdir),
                "buildid": state.get("buildid", ""),
                "state_flags": int(state.get("StateFlags", "0") or 0),
                "manifest": os.path.join(steamapps, fname),
            }
    return apps


def is_ready(app):
    """Fully installed and not being updated/validated right now."""
    return app["state_flags"] == STATE_FULLY_INSTALLED and os.path.isdir(app["install_path"])


def running_install_paths(proc_root="/proc"):
    """Command lines of running processes (used to tell whether a game is open)."""
    cmdlines = []
    try:
        pids = [p for p in os.listdir(proc_root) if p.isdigit()]
    except OSError:
        return cmdlines
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "cmdline"), "rb") as f:
                cmd = f.read().replace(b"\0", b" ").decode("utf-8", "replace")
        except OSError:
            continue
        if cmd:
            cmdlines.append(cmd)
    return cmdlines


MEDIA_ROOT = "/run/media"   # where the Deck mounts SD cards: /run/media/<user>/<label> (older: /run/media/<label>)


def _media_aliases(path):
    """Other names of the same mounted library under MEDIA_ROOT (`<user>/<label>` and `<label>`).

    Only a path that really lives under MEDIA_ROOT and has a `steamapps` folder is looked at, and an
    alias counts only if it exists and is the very same folder as the mount we started from, so a
    different volume of the same name is never taken for it.
    """
    root = MEDIA_ROOT.rstrip("/") + "/"
    if not path.startswith(root):
        return []
    parts = path[len(root):].split("/")
    if "steamapps" not in parts:
        return []
    cut = parts.index("steamapps")
    mount, tail = parts[:cut], "/".join(parts[cut:])
    if len(mount) == 2:                                    # <user>/<label>  ->  <label>
        others = [[mount[1]]]
    elif len(mount) == 1:                                  # <label>  ->  <user>/<label> for each folder here
        try:
            users = sorted(os.listdir(root))
        except OSError:
            users = []
        others = [[u, mount[0]] for u in users if u != mount[0]]
    else:
        return []
    home = root + "/".join(mount)
    found = []
    for other in others:
        candidate = root + "/".join(other)
        try:
            if os.path.isdir(candidate) and os.path.samefile(candidate, home):
                found.append(candidate + "/" + tail)
        except OSError:
            continue
    return found


def install_forms(install_path):
    """Every name the install folder may be running under, each with a trailing '/'.

    The path as Steam reports it (always first, as before), its realpath, and the mount aliases of
    either. Only ever adds names: it can make `is_running` see more, never less. Names are compared
    exactly as they are (no Unicode normalisation) on both sides.
    """
    path = install_path.rstrip("/")
    names = [path]
    try:
        # Cheap and hang-proof: only the install path itself, MEDIA_ROOT (local) and paths named like our own
        # volume under it are touched; no folder of any mounted volume is ever listed.
        if path:
            real = os.path.realpath(path)
            if real != path:
                names.append(real)
        for base in list(names):
            for alias in _media_aliases(base):
                if alias not in names:
                    names.append(alias)
    except (OSError, ValueError):
        pass                                               # the plain form is always there; never fail the guard
    return [n + "/" for n in names]


def _windows_forms(forms):
    """Case-insensitive patterns for the Windows-style names Proton prints, for ANY drive letter and either slash.

    Wine maps `Z:` to the root of the filesystem, but a Steam library (or an SD card) is also mapped to a letter of its
    own (`S:\\steamapps\\common\\<install dir>\\Bin\\Game.exe`), so the drive letter is not known: every form of the
    install folder is matched with `<letter>:` in front, and so is its `steamapps\\common\\<install dir>\\` tail (the path
    relative to the library root). Only ever adds names: `is_running` keeps every earlier match.
    """
    out = []
    for f in forms:
        parts = [p for p in f.split("/") if p]
        if not parts:
            continue
        out.append(re.compile(r"[A-Za-z]:[\\/]+" + r"[\\/]+".join(re.escape(p) for p in parts) + r"[\\/]", re.I))
        if "steamapps" in parts:
            tail = parts[parts.index("steamapps"):]
            if len(tail) >= 3 and tail[1].lower() == "common":
                out.append(re.compile(r"[A-Za-z]:[\\/]+" + r"[\\/]+".join(re.escape(p) for p in tail) + r"[\\/]", re.I))
    return out


def is_running(app, cmdlines):
    """True when a process runs from the game's folder (under any of its names) or is its Steam launcher.

    Proton passes Windows-style paths too (Z:\\home\\deck\\..., or `S:\\steamapps\\common\\<dir>\\...` for a library
    mapped to its own drive letter: any letter, either slash, any case), and the launcher's command line carries
    `AppId=<appid>` as a token of its own (never a part of a longer number).
    """
    forms = install_forms(app["install_path"])
    wins = ["Z:" + f.replace("/", "\\") for f in forms]
    drives = _windows_forms(forms)
    appid = str(app.get("appid", ""))
    token = re.compile(r"(?<!\S)AppId=%s(?!\S)" % re.escape(appid)) if appid.isdigit() else None
    for c in cmdlines:
        if (any(f in c for f in forms) or any(w in c for w in wins) or any(d.search(c) for d in drives)
                or (token and token.search(c))):
            return True
    return False
