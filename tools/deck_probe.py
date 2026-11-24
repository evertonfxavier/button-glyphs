#!/usr/bin/env python3
"""READ-ONLY facts about one supported game on the Steam Deck, in one SSH round trip.

    python3 tools/deck_probe.py <appid> [--json out.json] [--plugin 'Button Glyphs'] [--dry-run-request]

Run `.claude/scripts/deck.sh --check` first (exit 2 here means the Deck cannot be reached).

Two modes live in this file:

  Remote mode  The text between the REMOTE markers below is a self-contained, stdlib-only, read-only program.
               The host mode sends it (with the plugin's own steam.py and a JSON request) on STDIN of
               `deck.sh 'python3 -'`, so the command line names no game path. It never writes, never calls sudo,
               never follows a symlink out of the install folder, reads each pinned file at most size+1 bytes,
               and stops at a deadline. It prints ONE JSON object.
  Host mode    Builds the request from the registered profile (the same tables the engine uses: pinned size and
               SHA-256 per file, region offsets for giant archives), runs the remote part, prints PASS / WARN /
               FAIL lines and exits 0 (no FAIL), 1 (a FAIL) or 2 (Deck unreachable).

What it reports: the Steam library path as written in libraryfolders.vdf and the appmanifest (installdir,
buildid, StateFlags, TargetBuildID, SizeOnDisk), every other spelling of the install folder the plugin's running
guard knows, the filesystem type and mount options that hold the game (journaling or not), free space, owner and
mode of the install folder and the files, the SHA-256 of every pinned file (or region range) against the pinned
table (match / differ / missing / not-regular), the processes that mention the game (path in any spelling, the
Z:\\ form, AppId=<id>) and whether the plugin's own `steam.is_running` sees each of them, the installed plugin
(version, flags), its config.json (only the games' styles and last_event, plus the file's SHA-256), the size of
its backups, leftover *.button-glyphs.tmp files, the Python version and the uptime.
"""
import argparse
import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "py_modules"))

# ===== REMOTE BEGIN (self-contained; this text is what runs on the Deck) =====
import hashlib
import json
import os
import re
import stat
import sys
import time
import types

SCHEMA = 1
TMP_SUFFIX = ".button-glyphs.tmp"
MARK_OFF, MARK_MAGIC = 0x20, b"BGLYPHS\x01"
MAX_CMD = 160
MAX_WALK = 20000


def _names(uid, gid):
    try:
        import pwd
        user = pwd.getpwuid(uid).pw_name
    except Exception:
        user = str(uid)
    try:
        import grp
        group = grp.getgrgid(gid).gr_name
    except Exception:
        group = str(gid)
    return user, group


def _owner(st):
    user, group = _names(st.st_uid, st.st_gid)
    return {"user": user, "group": group, "mode": "%o" % stat.S_IMODE(st.st_mode)}


def _unescape(text):
    return re.sub(r"\\([0-7]{3})", lambda m: chr(int(m.group(1), 8)), text)


def parse_mountinfo(text):
    """[{mount, options, fstype, source, super_options}] from /proc/self/mountinfo text."""
    out = []
    for line in text.splitlines():
        parts = line.split(" ")
        if "-" not in parts:
            continue
        i = parts.index("-")
        if i < 6 or len(parts) < i + 3:
            continue
        out.append({"mount": _unescape(parts[4]), "options": parts[5], "fstype": parts[i + 1],
                    "source": _unescape(parts[i + 2]), "super_options": parts[i + 3] if len(parts) > i + 3 else ""})
    return out


def mount_of(path, mounts):
    """The mount that holds `path`: the longest mount point that is a prefix of it (later lines win ties)."""
    best = None
    for m in mounts:
        mp = m["mount"].rstrip("/") or "/"
        if path == mp or path.startswith(mp.rstrip("/") + "/") or mp == "/":
            if best is None or len(mp) >= len(best["mount"].rstrip("/") or "/"):
                best = m
    return best


def _inside(real, base):
    return real == base or real.startswith(base.rstrip(os.sep) + os.sep)


def _hash_exact(path, size, expected, deadline, actual=None):
    """('match'|'differ'|'unreadable'|'timeout', detail): at most size+1 bytes are read, never through a link.

    `actual`, when given, receives the digest of a file that read completely (to recognise our own renders)."""
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(path, flags)
    except OSError as exc:
        return "unreadable", exc.strerror or str(exc)
    h, left = hashlib.sha256(), size + 1
    try:
        while left > 0:
            if time.time() > deadline:
                return "timeout", "deadline reached"
            block = os.read(fd, min(1 << 20, left))
            if not block:
                break
            h.update(block)
            left -= len(block)
    except OSError as exc:
        return "unreadable", exc.strerror or str(exc)
    finally:
        os.close(fd)
    if size + 1 - left != size:
        return "differ", "the file changed while it was read"
    if actual is not None:
        actual["sha256"] = h.hexdigest()
    return ("match" if h.hexdigest() == expected else "differ"), ""


def inspect_file(install, base_real, rel, size, sha, deadline):
    path = os.path.join(install, *rel.split("/"))
    row = {"file": rel}
    try:
        st = os.lstat(path)
    except FileNotFoundError:
        row["state"] = "missing"
        return row
    except OSError as exc:
        row.update(state="unreadable", detail=exc.strerror or str(exc))
        return row
    row.update(size=st.st_size, owner=_owner(st))
    if not stat.S_ISREG(st.st_mode):
        row.update(state="not-regular", detail="symlink" if stat.S_ISLNK(st.st_mode) else
                   "directory" if stat.S_ISDIR(st.st_mode) else "special file")
        return row
    if not _inside(os.path.realpath(path), base_real):
        row.update(state="not-regular", detail="reached through a link that leaves the install folder")
        return row
    if sha is None:
        row["state"] = "present"
    elif size is not None and st.st_size != size:
        row.update(state="differ", detail="size %d, pinned %d" % (st.st_size, size))
    else:
        actual = {}
        row["state"], detail = _hash_exact(path, size, sha, deadline, actual)
        if detail:
            row["detail"] = detail
        if row["state"] == "differ" and actual.get("sha256"):
            row["actual"] = actual["sha256"]
    return row


def inspect_regions(archive, regions, deadline, max_bytes):
    """Hash only the pinned byte ranges of a giant archive (never the whole file, never through a link)."""
    out = {"archive": {}, "rows": []}
    try:
        st = os.lstat(archive)
    except OSError as exc:
        out["archive"] = {"state": "missing" if isinstance(exc, FileNotFoundError) else "unreadable"}
        return out
    if not stat.S_ISREG(st.st_mode):
        out["archive"] = {"state": "not-regular"}
        return out
    out["archive"] = {"state": "present", "size": st.st_size, "owner": _owner(st)}
    flags = os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0)
    try:
        fd = os.open(archive, flags)
    except OSError as exc:
        out["archive"]["state"] = "unreadable"
        out["archive"]["detail"] = exc.strerror or str(exc)
        return out
    spent = 0
    try:
        for r in regions:
            row = {"key": r["key"]}
            if r["offset"] + r["size"] > st.st_size:
                row["state"] = "differ"
                row["detail"] = "the archive ends before this region"
            elif spent + r["size"] > max_bytes or time.time() > deadline:
                row["state"] = "skipped"
                row["detail"] = "read budget or deadline reached"
            else:
                h, left, pos = hashlib.sha256(), r["size"], r["offset"]
                head = b""
                while left > 0:
                    block = os.pread(fd, min(1 << 20, left), pos)
                    if not block:
                        break
                    if pos == r["offset"]:
                        head = block[:128]
                    h.update(block)
                    pos += len(block)
                    left -= len(block)
                spent += r["size"]
                row["state"] = "match" if left == 0 and h.hexdigest() == r["sha256"] else "differ"
                row["marker"] = len(head) >= MARK_OFF + 8 and head[MARK_OFF:MARK_OFF + 8] == MARK_MAGIC
            out["rows"].append(row)
    finally:
        os.close(fd)
    return out


def leftovers(install, folders):
    found, seen = [], [0]

    def scan(directory, depth):
        try:
            with os.scandir(directory) as it:
                entries = sorted(it, key=lambda e: e.name)
        except OSError:
            return
        for e in entries:
            seen[0] += 1
            if seen[0] > MAX_WALK:
                return
            if e.name.endswith(TMP_SUFFIX):
                found.append(os.path.relpath(e.path, install))
            if depth < 2 and e.is_dir(follow_symlinks=False):
                scan(e.path, depth + 1)

    for folder in sorted(set(folders) | {""}):
        scan(os.path.join(install, folder) if folder else install, 0)
    return sorted(set(found))


def tree_size(path, limit=MAX_WALK):
    files = size = 0
    seen = 0
    for base, dirs, names in os.walk(path):
        for n in names:
            seen += 1
            if seen > limit:
                return {"files": files, "bytes": size, "truncated": True}
            try:
                size += os.lstat(os.path.join(base, n)).st_size
                files += 1
            except OSError:
                pass
    return {"files": files, "bytes": size, "truncated": False}


def _cmdlines(proc_root):
    try:
        pids = sorted(p for p in os.listdir(proc_root) if p.isdigit())
    except OSError:
        return
    for pid in pids:
        try:
            with open(os.path.join(proc_root, pid, "cmdline"), "rb") as f:
                raw = f.read(65536)
        except OSError:
            continue
        cmd = raw.replace(b"\0", b" ").decode("utf-8", "replace").strip()
        if cmd:
            yield pid, cmd


def _loader_user(proc_root):
    """The user the Decky loader runs as (uid of the first process whose command line names it)."""
    for pid, cmd in _cmdlines(proc_root):
        if "PluginLoader" in cmd or "plugin_loader" in cmd:
            try:
                with open(os.path.join(proc_root, pid, "status")) as f:
                    for line in f:
                        if line.startswith("Uid:"):
                            uid = int(line.split()[1])
                            return {"uid": uid, "user": _names(uid, 0)[0]}
            except (OSError, ValueError):
                continue
    return None


LEDGER_LINE = re.compile(rb"([0-9a-f]{64}) [0-9]+")


def ledger_hashes(backup_dir, limit=1 << 20):
    """SHA-256s in the plugin's render ledger (renders.log): what the plugin itself wrote. Read-only, bounded;
    a torn last line is ignored exactly as the plugin ignores it."""
    try:
        with open(os.path.join(backup_dir, "renders.log"), "rb") as f:
            raw = f.read(limit)
    except OSError:
        return set()
    return {m.group(1).decode("ascii") for m in (LEDGER_LINE.fullmatch(l) for l in raw.split(b"\n")[:-1]) if m}


def backup_shas(backup_dir, max_files=200, max_bytes=16 << 20):
    """{sha256 of each *.orig backup}: the originals the plugin kept (read-only, bounded, regular files only)."""
    found = set()
    try:
        names = sorted(n for n in os.listdir(backup_dir) if n.endswith(".orig"))[:max_files]
    except OSError:
        return found
    for name in names:
        path = os.path.join(backup_dir, name)
        try:
            st = os.lstat(path)
            if not stat.S_ISREG(st.st_mode) or st.st_size > max_bytes:
                continue
            with open(path, "rb") as f:
                found.add(hashlib.sha256(f.read(max_bytes + 1)).hexdigest())
        except OSError:
            continue
    return found


def _read_json(path, limit=1 << 20):
    try:
        with open(path, "rb") as f:
            raw = f.read(limit)
        return json.loads(raw.decode("utf-8")), hashlib.sha256(raw).hexdigest()
    except (OSError, ValueError):
        return None, None


def remote_probe(req, steam, home=None, proc_root="/proc", mountinfo="/proc/self/mountinfo",
                 uptime_file="/proc/uptime", media_root=None, now=None):
    """Everything the host needs, as one dict. Read-only."""
    started = now if now is not None else time.time()
    deadline = started + float(req.get("deadline", 120))
    home = home or os.path.expanduser("~")
    if media_root is not None:
        steam.MEDIA_ROOT = media_root
    appid = str(req["appid"])
    out = {"schema": SCHEMA, "ok": True, "appid": appid, "python": sys.version.split()[0]}
    try:
        with open(uptime_file) as f:
            out["uptime_s"] = float(f.read().split()[0])
    except (OSError, ValueError, IndexError):
        out["uptime_s"] = None
    try:
        out["me"] = _names(os.getuid(), os.getgid())[0]
    except AttributeError:
        out["me"] = None
    out["loader"] = _loader_user(proc_root)

    # ---- Steam: libraries as written, the manifest, the install folder
    libraries = steam.library_paths(home)
    vdf_paths = []
    for root in steam.steam_roots(home):
        try:
            with open(os.path.join(root, "steamapps", "libraryfolders.vdf"), encoding="utf-8", errors="replace") as f:
                vdf_paths += re.findall(r'"path"\s+"([^"]*)"', f.read())
        except OSError:
            pass
    st = {"libraries": libraries, "vdf_paths": sorted(set(vdf_paths)), "installed": False}
    app = steam.installed_apps(home).get(appid)
    install = None
    if app:
        install = app["install_path"]
        manifest = {}
        try:
            with open(app["manifest"], encoding="utf-8", errors="replace") as f:
                manifest = steam.parse_vdf(f.read()).get("AppState", {})
        except OSError:
            pass
        st.update(installed=True, library=app["library"], manifest=app["manifest"], name=app["name"],
                  installdir=manifest.get("installdir", ""), buildid=app["buildid"],
                  StateFlags=app["state_flags"], TargetBuildID=manifest.get("TargetBuildID", ""),
                  SizeOnDisk=manifest.get("SizeOnDisk", ""), install_path=install,
                  install_exists=os.path.isdir(install), realpath=os.path.realpath(install),
                  guard_forms=steam.install_forms(install), ready=steam.is_ready(app))
        st["aliases"] = [f.rstrip("/") for f in st["guard_forms"] if f.rstrip("/") != install.rstrip("/")]
        st["samefile_aliases"] = []
        for alias in st["aliases"]:
            try:
                if os.path.samefile(alias, install):
                    st["samefile_aliases"].append(alias)
            except OSError:
                pass
    out["steam"] = st
    if not app or not os.path.isdir(install):
        out["files"] = {"rows": []}
        out["error"] = "the game is not installed (no appmanifest_%s.acf in any library)" % appid
        out["ok"] = False
        return out
    base_real = os.path.realpath(install)

    # ---- filesystem
    try:
        with open(mountinfo, encoding="utf-8", errors="replace") as f:
            mount = mount_of(base_real, parse_mountinfo(f.read()))
    except OSError:
        mount = None
    fs = dict(mount or {"mount": None, "fstype": None, "source": None, "options": "", "super_options": ""})
    try:
        sv = os.statvfs(install)
        fs.update(free_bytes=sv.f_bavail * sv.f_frsize, total_bytes=sv.f_blocks * sv.f_frsize)
    except OSError:
        pass
    out["fs"] = fs

    # ---- owner / mode of the install folder and the touched folders
    folders = sorted(set(req.get("folders") or []) | {os.path.dirname(f["file"]) for f in req.get("files", [])
                                                     if "/" in f["file"]})
    rows_dirs = []
    for folder in [""] + folders:
        p = os.path.join(install, *folder.split("/")) if folder else install
        try:
            info = os.lstat(p)
            rows_dirs.append(dict(_owner(info), folder=folder or "<install>"))
        except OSError:
            rows_dirs.append({"folder": folder or "<install>", "user": None, "group": None, "mode": None})
    out["dirs"] = rows_dirs

    # ---- the pinned files
    files = [inspect_file(install, base_real, f["file"], f.get("size"), f.get("sha256"), deadline)
             for f in req.get("files", [])]
    out["files"] = {"rows": files, "match": sum(1 for r in files if r["state"] == "match"),
                    "total": len(files)}
    if req.get("archive") and req.get("regions"):
        arch = os.path.join(install, *req["archive"].split("/"))
        if _inside(os.path.realpath(arch), base_real) or not os.path.exists(arch):
            out["regions"] = inspect_regions(arch, req["regions"], deadline, int(req.get("max_bytes", 256 << 20)))
        else:
            out["regions"] = {"archive": {"state": "not-regular", "detail": "reached through a link that leaves the install folder"},
                              "rows": []}
        out["regions"]["total"] = len(req["regions"])
        out["regions"]["match"] = sum(1 for r in out["regions"]["rows"] if r["state"] == "match")
        out["regions"]["marked"] = sum(1 for r in out["regions"]["rows"] if r.get("marker"))
    if req.get("save"):
        saves = []
        for lib in libraries:
            p = os.path.join(lib, "steamapps", "compatdata", appid, "pfx", *req["save"].split("/"))
            try:
                info = os.lstat(p)
                saves.append({"library": lib, "size": info.st_size, "regular": stat.S_ISREG(info.st_mode),
                              "owner": _owner(info)})
            except OSError:
                pass
        out["saves"] = saves

    # ---- processes that mention the game, and what the plugin's guard says about each
    forms = [f for f in st["guard_forms"]]
    name = st.get("installdir") or os.path.basename(install)
    token = re.compile(r"(?<!\S)AppId=%s(?!\S)" % re.escape(appid))
    marker_posix, marker_win = "steamapps/common/" + name, "steamapps\\common\\" + name
    procs = []
    for pid, cmd in _cmdlines(proc_root):
        if pid == str(os.getpid()):
            continue
        why = []
        if any(f in cmd for f in forms):
            why.append("path")
        if marker_posix in cmd or marker_win in cmd:
            why.append("folder name")
        if "Z:" in cmd and name in cmd:
            why.append("Z:\\ form")
        if token.search(cmd):
            why.append("AppId")
        if why:
            procs.append({"pid": int(pid), "why": sorted(set(why)), "cmdline": " ".join(cmd.split())[:MAX_CMD],
                          "guard": bool(steam.is_running(app, [cmd]))})
    out["running"] = procs

    # ---- leftovers, plugin, config, backups
    out["leftovers"] = leftovers(install, folders)
    plugin = {"name": req.get("plugin"), "installed": False}
    if req.get("plugin"):
        pdir = os.path.join(home, "homebrew", "plugins", req["plugin"])
        pkg, _ = _read_json(os.path.join(pdir, "package.json"))
        manifest, _ = _read_json(os.path.join(pdir, "plugin.json"))
        plugin["installed"] = os.path.isdir(pdir)
        plugin["version"] = (pkg or {}).get("version")
        plugin["flags"] = (manifest or {}).get("flags")
        cfg_path = os.path.join(home, "homebrew", "settings", req["plugin"], "config.json")
        cfg, cfg_sha = _read_json(cfg_path)
        plugin["config_sha256"] = cfg_sha
        game = ((cfg or {}).get("games") or {}).get(appid) or {}
        plugin["game"] = {k: game.get(k) for k in ("style", "last_event", "error") if k in game}
        plugin["games"] = {a: {"style": (g or {}).get("style"), "last_event": (g or {}).get("last_event")}
                           for a, g in sorted(((cfg or {}).get("games") or {}).items()) if isinstance(g, dict)}
        bdir = os.path.join(home, "homebrew", "data", req["plugin"], "backups", appid)
        plugin["backups"] = dict(tree_size(bdir), exists=os.path.isdir(bdir)) if os.path.isdir(bdir) else {"exists": False}
        try:
            plugin["config_owner"] = _owner(os.lstat(cfg_path))["user"]
        except (OSError, TypeError, KeyError):
            plugin["config_owner"] = None
        fdir = os.path.join(bdir, "files")
        ledger = ledger_hashes(fdir)
        plugin["ledger"] = {"hashes": len(ledger)}
        pinned_shas = {f["sha256"] for f in req.get("files", []) if f.get("sha256")}
        kept = backup_shas(fdir)
        plugin["backup_originals"] = {"files": len(kept), "pinned_match": len(kept & pinned_shas)}
        for row in out["files"]["rows"]:                      # a differing file whose hash the plugin recorded is OURS
            if row.get("state") == "differ" and row.get("actual") in ledger:
                row["state"] = "patched"
                row["detail"] = "its hash is in the plugin's render ledger"
        out["files"]["patched"] = sum(1 for r in out["files"]["rows"] if r["state"] == "patched")
    out["plugin"] = plugin
    out["seconds"] = round(time.time() - started, 2)
    return out


def remote_main(steam_source, request_json):
    """Entry point of the script sent to the Deck: prints one JSON object (errors included)."""
    steam = types.ModuleType("steam")
    try:
        exec(compile(steam_source, "steam.py", "exec"), steam.__dict__)
        env = os.environ
        result = remote_probe(json.loads(request_json), steam,
                              home=env.get("DECK_PROBE_HOME") or None,
                              proc_root=env.get("DECK_PROBE_PROC") or "/proc",
                              mountinfo=env.get("DECK_PROBE_MOUNTINFO") or "/proc/self/mountinfo",
                              media_root=env.get("DECK_PROBE_MEDIA") or None)
    except Exception as exc:
        result = {"schema": SCHEMA, "ok": False, "error": "%s: %s" % (type(exc).__name__, exc)}
    print(json.dumps(result, sort_keys=True))
# ===== REMOTE END =====

STEAM_PY = ROOT / "py_modules" / "button_glyphs" / "steam.py"
DECK_SH = ROOT.parent / ".claude" / "scripts" / "deck.sh"
JOURNALED = ("ext4", "ext3", "btrfs", "xfs", "f2fs", "zfs", "jfs", "reiserfs")
UNSAFE_FS = ("exfat", "vfat", "fat", "msdos", "ntfs", "ntfs3", "fuseblk", "fuse", "ext2")
PASS, WARN, FAIL = "PASS", "WARN", "FAIL"
MIN_FREE = 64 << 20


def remote_source():
    """The text between the REMOTE markers of this very file."""
    text = Path(__file__).read_text(encoding="utf-8")
    start = text.index("# ===== REMOTE BEGIN")
    start = text.index("\n", start) + 1
    end = text.index("# ===== REMOTE END")
    return text[start:end]


def build_request(profile, appid, plugin="Button Glyphs"):
    """What the Deck must look at, taken from the registered profile's own tables (nothing retyped)."""
    req = {"appid": appid, "plugin": plugin, "files": [], "folders": [], "deadline": 120}
    if getattr(profile, "files_level", False):
        req["files"] = [{"file": e["file"], "size": e["size"], "sha256": e["sha256"]} for e in profile.files]
    elif getattr(profile, "region_level", False):
        req["archive"] = profile.file
        req["regions"] = [{"key": r["key"], "offset": r["offset"], "size": r["size"], "sha256": r["sha256"]}
                          for r in profile.regions]
        req["folders"] = [os.path.dirname(profile.file)] if "/" in profile.file else []
    elif getattr(profile, "config_level", False):
        req["files"] = [{"file": b["file"], "size": b["size"], "sha256": b["sha256"]} for b in profile.builds.values()]
        req["save"] = profile.file
    elif getattr(profile, "entry_level", False):
        req["files"] = [{"file": t["file"], "size": None, "sha256": None} for t in profile.targets()]
    else:
        req["files"] = [{"file": profile.file, "size": None, "sha256": None}]
    return req


def build_script(request, steam_source=None, remote=None):
    """The program sent on stdin: the remote part, then one call with the plugin's steam.py and the request."""
    steam_source = steam_source if steam_source is not None else STEAM_PY.read_text(encoding="utf-8")
    remote = remote if remote is not None else remote_source()
    return "%s\nremote_main(%r, %r)\n" % (remote, steam_source, json.dumps(request, sort_keys=True))


def judge(res, req, local_version=None):
    """[(level, topic, message)] from the remote result. Pure: tests feed canned results."""
    lines = []

    def add(level, topic, msg):
        lines.append((level, topic, msg))

    if "steam" not in res:
        add(FAIL, "probe", res.get("error") or "the remote probe returned nothing usable")
        return lines
    steam = res.get("steam", {})
    if not steam.get("installed"):
        add(FAIL, "install", res.get("error") or "the game is not installed")
        return lines
    add(PASS, "install", "%s: build %s, installdir %r, library %s" % (
        steam.get("name"), steam.get("buildid"), steam.get("installdir"), steam.get("library")))
    flags = steam.get("StateFlags")
    target = str(steam.get("TargetBuildID") or "").strip()
    if target in ("0",):                                    # Steam writes 0 for "no update target"
        target = ""
    if flags != 4 or (target and target != str(steam.get("buildid"))):
        add(WARN, "steam state", "StateFlags %s, TargetBuildID %s (an update or verification is pending; the plugin defers)"
            % (flags, steam.get("TargetBuildID") or "-"))
    else:
        add(PASS, "steam state", "StateFlags 4 (fully installed), no update pending")
    if steam.get("samefile_aliases"):
        add(PASS, "path spellings", "%d other spelling(s) of the install folder known to the guard: %s" % (
            len(steam["samefile_aliases"]), ", ".join(steam["samefile_aliases"][:3])))
    # hashes
    rows = res.get("files", {}).get("rows", [])
    pinned = [r for r in rows if r.get("state") != "present"]
    patched = [r for r in pinned if r["state"] == "patched"]
    bad = [r for r in pinned if r["state"] not in ("match", "patched")]
    if req.get("files") and any(f.get("sha256") for f in req["files"]):
        total = sum(1 for f in req["files"] if f.get("sha256"))
        if not bad and patched:
            kept = (res.get("plugin") or {}).get("backup_originals") or {}
            style = ((res.get("plugin") or {}).get("game") or {}).get("style")
            note = "%d original, %d patched by the plugin (hash in its render ledger); originals kept in backups: %s" % (
                total - len(patched), len(patched),
                "%d/%d" % (kept.get("pinned_match", 0), len(patched)) if kept else "unknown")
            if style == "original":
                add(WARN, "hashes", note + "; the config says Original, so the watcher will restore them")
            elif kept and kept.get("pinned_match", 0) < len(patched):
                add(WARN, "hashes", note + "; some backups do not match the pinned originals")
            else:
                add(PASS, "hashes", note)
        elif bad:
            add(FAIL, "hashes", "%d of %d pinned file(s) do not match: %s" % (
                len(bad), total, "; ".join("%s %s%s" % (r["file"], r["state"], " (%s)" % r["detail"] if r.get("detail") else "")
                                           for r in bad[:4])))
        else:
            add(PASS, "hashes", "%d/%d pinned files match size and SHA-256" % (total - len(bad), total))
    elif rows:
        absent = [r for r in rows if r["state"] != "present"]
        add(FAIL if absent else PASS, "files", "%d/%d profile files present (no pinned hash for this strategy)"
            % (len(rows) - len(absent), len(rows)))
    reg = res.get("regions")
    if reg is not None:
        arch = reg.get("archive", {})
        if arch.get("state") != "present":
            add(FAIL, "regions", "archive %s" % arch.get("state"))
        else:
            badr = [r for r in reg["rows"] if r["state"] != "match"]
            marked = reg.get("marked", 0)
            if badr and marked:
                add(WARN, "regions", "%d/%d regions differ and %d carry the plugin's marker: they are patched, not original"
                    % (len(badr), reg["total"], marked))
            elif badr:
                add(FAIL, "regions", "%d/%d regions do not match: %s" % (
                    len(badr), reg["total"], "; ".join("%s %s" % (r["key"], r["state"]) for r in badr[:4])))
            else:
                add(PASS, "regions", "%d/%d region ranges match size and SHA-256" % (reg["match"], reg["total"]))
    # filesystem
    fs = res.get("fs", {})
    fstype = (fs.get("fstype") or "").lower()
    if not fstype:
        add(WARN, "filesystem", "could not read the mount that holds the game")
    elif fstype in JOURNALED:
        add(PASS, "filesystem", "%s on %s (journaled; rename is atomic), options %s" % (
            fstype, fs.get("mount"), fs.get("options")))
    elif fstype in UNSAFE_FS:
        add(WARN, "filesystem", "%s on %s: no journal, a power cut can leave a half-written file; ownership and "
            "permissions are fake (options %s)" % (fstype, fs.get("mount"), fs.get("options")))
    else:
        add(WARN, "filesystem", "%s on %s: not a filesystem this tool knows" % (fstype, fs.get("mount")))
    if "ro" in (fs.get("options") or "").split(","):
        add(FAIL, "filesystem", "mounted read-only")
    # space
    need = sum(f.get("size") or 0 for f in req.get("files", [])) + sum(r["size"] for r in req.get("regions", []))
    free = fs.get("free_bytes")
    if free is not None:
        if free < need + MIN_FREE:
            add(FAIL, "free space", "%.0f MB free, the apply needs about %.0f MB" % (free / 1e6, (need + MIN_FREE) / 1e6))
        elif free < (1 << 30):
            add(WARN, "free space", "%.0f MB free (the apply needs about %.0f MB)" % (free / 1e6, (need + MIN_FREE) / 1e6))
        else:
            add(PASS, "free space", "%.1f GB free (the apply needs about %.0f MB)" % (free / 1e9, (need + MIN_FREE) / 1e6))
    # owner
    owners = {}
    for r in rows:
        if r.get("owner"):
            owners["%s:%s %s" % (r["owner"]["user"], r["owner"]["group"], r["owner"]["mode"])] = \
                owners.get("%s:%s %s" % (r["owner"]["user"], r["owner"]["group"], r["owner"]["mode"]), 0) + 1
    loader = res.get("loader") or {}
    users = {k.split(":")[0] for k in owners}
    if owners:
        flags = (res.get("plugin") or {}).get("flags") or []
        if "root" in flags or "_root" in flags:               # Decky runs a plugin as root only when it asks for it
            who = "root"
        else:
            who = (res.get("plugin") or {}).get("config_owner") or res.get("me") or loader.get("user")
        if who and users and users != {who}:
            add(WARN, "owner", "files are owned by %s, the plugin runs as %s: a rewritten file would change owner (%s)" % (
                ", ".join(sorted(users)), who, "; ".join("%s x%d" % kv for kv in sorted(owners.items()))))
        else:
            add(PASS, "owner", "%s (plugin user: %s)" % ("; ".join("%s x%d" % kv for kv in sorted(owners.items())),
                                                       who or "unknown"))
    # running guard
    procs = res.get("running", [])
    unseen = [p for p in procs if not p.get("guard")]
    if unseen:
        add(FAIL, "running guard", "the plugin's guard does NOT see %d process(es) that mention the game: %s" % (
            len(unseen), "; ".join("pid %d %s [%s]" % (p["pid"], p["cmdline"][:90], "/".join(p["why"])) for p in unseen[:2])))
    elif procs:
        add(WARN, "running guard", "the game is running now (%d process(es), all seen by the guard): changes wait until it closes"
            % len(procs))
    else:
        add(PASS, "running guard", "no process mentions the game; the guard forms are %s" % ", ".join(
            f for f in steam.get("guard_forms", [])[:3]))
    # leftovers
    left = res.get("leftovers", [])
    add(WARN if left else PASS, "leftovers", ("%d *.button-glyphs.tmp file(s): %s" % (len(left), ", ".join(left[:3])))
        if left else "no *.button-glyphs.tmp left in the touched folders")
    # plugin
    plugin = res.get("plugin", {})
    if not plugin.get("installed"):
        add(WARN, "plugin", "%r is not installed on the Deck" % plugin.get("name"))
    else:
        v = plugin.get("version")
        if local_version and v != local_version:
            add(WARN, "plugin", "installed version %s, this repo is %s" % (v, local_version))
        else:
            add(PASS, "plugin", "version %s, flags %s" % (v, plugin.get("flags")))
        game = plugin.get("game") or {}
        style = game.get("style", "original")
        if game.get("error"):
            add(FAIL, "plugin state", "the plugin recorded an error for this game: %s" % game["error"])
        elif reg is not None and style != "original" and reg.get("marked", 0) == 0:
            add(WARN, "plugin state", "style %s is chosen but no region carries the marker: pending (the watcher will apply it)" % style)
        elif (style != "original" and rows and not bad and not patched and not req.get("save")      # a saved-value game changes the save, never the pinned file
          and any(f.get("sha256") for f in req.get("files", []))):
            add(WARN, "plugin state", "style %s is chosen but every file is still the pinned original: pending" % style)
        else:
            add(PASS, "plugin state", "style %s, last event %r" % (style, game.get("last_event", "")))
        b = plugin.get("backups") or {}
        if b.get("exists"):
            add(PASS, "backups", "%d file(s), %.1f MB" % (b.get("files", 0), b.get("bytes", 0) / 1e6))
    return lines


def summarize(lines):
    return "\n".join("%-4s  %-14s %s" % (lvl, topic, msg) for lvl, topic, msg in lines)


def local_version():
    try:
        return json.loads((ROOT / "package.json").read_text(encoding="utf-8")).get("version")
    except (OSError, ValueError):
        return None


def run_remote(script, deck_sh=None, timeout=300):
    """Send `script` on stdin of `deck.sh 'python3 -'`. The command text names no game path."""
    deck_sh = str(deck_sh or os.environ.get("DECK_SH") or DECK_SH)
    # a first, stdin-less call opens (or reuses) the SSH master so the real call cannot lose its stdin to a login
    subprocess.run([deck_sh, "true"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=timeout)
    return subprocess.run([deck_sh, "python3 -"], input=script, capture_output=True, text=True, timeout=timeout)


def parse_result(stdout):
    for line in reversed(stdout.strip().splitlines()):
        line = line.strip()
        if line.startswith("{"):
            return json.loads(line)
    raise ValueError("the Deck answered without a JSON object")


def check_reachable(deck_sh=None):
    deck_sh = str(deck_sh or os.environ.get("DECK_SH") or DECK_SH)
    p = subprocess.run([deck_sh, "--check"], stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=60)
    return p.returncode == 0, (p.stdout + p.stderr).strip()


def main(argv=None):
    from button_glyphs.profiles import PROFILES
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("appid")
    ap.add_argument("--json", help="write the request, the remote result and the verdict here")
    ap.add_argument("--plugin", default="Button Glyphs")
    ap.add_argument("--dry-run-request", action="store_true", help="print the request JSON and exit (no connection)")
    ns = ap.parse_args(argv)
    if ns.appid not in PROFILES:
        print("appid %s has no registered profile" % ns.appid, file=sys.stderr)
        return 1
    req = build_request(PROFILES[ns.appid], ns.appid, ns.plugin)
    if ns.dry_run_request:
        print(json.dumps(req, indent=2, sort_keys=True))
        return 0
    reachable, why = check_reachable()
    if not reachable:
        print("Deck unreachable: %s\nWake it up and run .claude/scripts/deck.sh --check; nothing was done." % why,
              file=sys.stderr)
        return 2
    done = run_remote(build_script(req))
    if done.returncode != 0 and not done.stdout.strip():
        print("remote run failed (exit %d): %s" % (done.returncode, done.stderr.strip()[-300:]), file=sys.stderr)
        return 2
    try:
        res = parse_result(done.stdout)
    except ValueError as exc:
        print("%s: %s" % (exc, done.stdout.strip()[-200:]), file=sys.stderr)
        return 1
    lines = judge(res, req, local_version())
    print(summarize(lines))
    verdict = FAIL if any(l[0] == FAIL for l in lines) else WARN if any(l[0] == WARN for l in lines) else PASS
    print("RESULT: %s  (%d PASS, %d WARN, %d FAIL)" % (verdict, *(sum(1 for l in lines if l[0] == k) for k in (PASS, WARN, FAIL))))
    if ns.json:
        with open(ns.json, "w", encoding="utf-8") as f:
            json.dump({"request": req, "remote": res, "checks": [list(l) for l in lines], "verdict": verdict},
                      f, indent=2, sort_keys=True)
            f.write("\n")
    return 1 if verdict == FAIL else 0


if __name__ == "__main__":
    sys.exit(main())
