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


def is_running(app, cmdlines):
    path = app["install_path"].rstrip("/") + "/"
    # Proton passes Windows-style paths too (Z:\home\deck\...)
    win = "Z:" + path.replace("/", "\\")
    return any(path in c or win in c for c in cmdlines)
