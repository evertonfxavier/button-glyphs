"""Applies, restores and re-applies glyph styles; keeps backups and per-game settings.

Files we write carry a trailer (last ALIGN bytes of the file) that says which style
was applied and the SHA-256 of the original it was built from. That makes the state
self-describing: no trailer means the file is an original (fresh install, game
update or Steam "verify files"), so the chosen style is applied again.
"""
import hashlib
import json
import os
import shutil
import struct
import threading
import time

from . import image, steam
from .forge import ALIGN
from .profiles import PROFILES

CONFIG_VERSION = 1
STYLES = ("original", "ps", "xbox")
STYLE_LABEL = {"original": "Original", "ps": "PlayStation", "xbox": "Xbox"}
TRAILER_MAGIC = b"BUTTONGLYPHS\x01"
TMP_SUFFIX = ".button-glyphs.tmp"


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def read_trailer(path):
    """The trailer dict of a file we patched, or None for any other file."""
    try:
        with open(path, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            if size < ALIGN:
                return None
            f.seek(size - ALIGN)
            tail = f.read(ALIGN)
    except OSError:
        return None
    if not tail.startswith(TRAILER_MAGIC):
        return None
    try:
        n = struct.unpack_from("<I", tail, len(TRAILER_MAGIC))[0]
        start = len(TRAILER_MAGIC) + 4
        return json.loads(tail[start:start + n].decode("utf-8"))
    except (ValueError, struct.error):
        return None


def write_trailer(path, info):
    body = json.dumps(info, sort_keys=True).encode("utf-8")
    block = TRAILER_MAGIC + struct.pack("<I", len(body)) + body
    if len(block) > ALIGN:
        raise ValueError("trailer too large")
    with open(path, "r+b") as f:
        f.seek(0, os.SEEK_END)
        size = f.tell()
        f.write(b"\0" * ((-size) % ALIGN))
        f.write(block + b"\0" * (ALIGN - len(block)))
        f.flush()
        os.fsync(f.fileno())


class IconLoader:
    """Loads the bundled 128 px PNG icons and scales them to glyph boxes (cached)."""

    def __init__(self, glyph_dir):
        self.glyph_dir = glyph_dir
        self.cache = {}

    def __call__(self, name, box_w, box_h):
        key = (name, box_w, box_h)
        if key not in self.cache:
            rgba, w, h = image.read_png(os.path.join(self.glyph_dir, name + ".png"))
            self.cache[key] = image.fit_icon(rgba, w, h, box_w, box_h)
        return self.cache[key]


class Engine:
    def __init__(self, home, settings_dir, runtime_dir, glyph_dir, version="dev", logger=None,
                 profiles=None, proc_root="/proc"):
        self.home = home
        self.config_path = os.path.join(settings_dir, "config.json")
        self.backup_root = os.path.join(runtime_dir, "backups")
        self.icons = IconLoader(glyph_dir)
        self.version = version
        self.log = logger
        self.profiles = PROFILES if profiles is None else profiles
        self.proc_root = proc_root
        self.lock = threading.RLock()
        os.makedirs(settings_dir, exist_ok=True)
        os.makedirs(self.backup_root, exist_ok=True)

    # ------------------------------------------------------------------ config

    def load_config(self):
        try:
            with open(self.config_path, encoding="utf-8") as f:
                cfg = json.load(f)
            if isinstance(cfg, dict) and isinstance(cfg.get("games"), dict):
                cfg.setdefault("version", CONFIG_VERSION)
                return cfg
        except (OSError, ValueError):
            pass
        return {"version": CONFIG_VERSION, "games": {}}

    def save_config(self, cfg):
        tmp = self.config_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2, sort_keys=True)
        os.replace(tmp, self.config_path)

    def _game_cfg(self, cfg, appid):
        return cfg["games"].setdefault(appid, {"style": "original"})

    def _info(self, msg, *args):
        if self.log:
            self.log.info(msg, *args)

    # ------------------------------------------------------------------ paths

    def target_path(self, app, profile):
        return os.path.join(app["install_path"], profile.file)

    def backup_path(self, appid, profile):
        return os.path.join(self.backup_root, appid, os.path.basename(profile.file))

    # ------------------------------------------------------------------ listing

    def list_games(self):
        with self.lock:
            cfg = self.load_config()
            apps = steam.installed_apps(self.home)
            cmdlines = steam.running_install_paths(self.proc_root)
            games = []
            for appid, app in apps.items():
                profile = self.profiles.get(appid)
                game = {
                    "appid": appid,
                    "name": app["name"],
                    "supported": profile is not None,
                    "buildid": app["buildid"],
                }
                if profile:
                    game.update(self._status(app, profile, cfg, cmdlines))
                games.append(game)
            games.sort(key=lambda g: (not g["supported"], g["name"].lower()))
            return games

    def _status(self, app, profile, cfg, cmdlines):
        gcfg = cfg["games"].get(app["appid"], {})
        desired = gcfg.get("style", "original")
        path = self.target_path(app, profile)
        trailer = read_trailer(path)
        applied = trailer["style"] if trailer else "original"
        out = {
            "style": desired,
            "applied": applied,
            "has_backup": os.path.exists(self.backup_path(app["appid"], profile)),
            "last_event": gcfg.get("last_event", ""),
            "roles": [label for _, label in profile.roles],
        }
        if not os.path.exists(path):
            out.update(state="error", message="%s not found" % profile.file)
        elif steam.is_running(app, cmdlines):
            out.update(state="running", message="Game is running; changes apply when it closes")
        elif not steam.is_ready(app):
            out.update(state="updating", message="Steam is updating this game")
        elif applied != desired:
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired])
        elif trailer and trailer.get("revision", 1) != profile.revision:
            out.update(state="pending", message="Improved %s glyphs will be applied"
                       % STYLE_LABEL[desired])
        elif gcfg.get("error"):
            out.update(state="error", message=gcfg["error"])
        else:
            out.update(state="ok", message="%s glyphs active" % STYLE_LABEL[applied])
        return out

    # ------------------------------------------------------------------ actions

    def set_style(self, appid, style):
        if style not in STYLES:
            raise ValueError("unknown style %r" % style)
        if appid not in self.profiles:
            raise ValueError("game %s is not supported" % appid)
        with self.lock:
            cfg = self.load_config()
            gcfg = self._game_cfg(cfg, appid)
            gcfg["style"] = style
            gcfg.pop("error", None)
            self.save_config(cfg)
            return self.sync(appid, reason="user")

    def sync_all(self, reason="check"):
        """Bring every configured game to its chosen style (used on load and periodically)."""
        results = []
        with self.lock:
            cfg = self.load_config()
            for appid, gcfg in cfg["games"].items():
                if appid in self.profiles and gcfg.get("style", "original") != "original":
                    results.append(self.sync(appid, reason=reason))
        return results

    def sync(self, appid, reason="check"):
        """Make the game's file match the configured style. Returns an action result."""
        logs, errors = [], []
        with self.lock:
            cfg = self.load_config()
            gcfg = self._game_cfg(cfg, appid)
            profile = self.profiles[appid]
            app = steam.installed_apps(self.home).get(appid)
            result = {"ok": False, "appid": appid, "logs": logs, "errors": errors, "changed": False}
            if not app:
                errors.append("game is not installed")
                return result
            if steam.is_running(app, steam.running_install_paths(self.proc_root)):
                logs.append("game is running; will apply after it closes")
                result["ok"] = True
                return result
            if not steam.is_ready(app):
                logs.append("Steam is installing/updating the game; will apply afterwards")
                result["ok"] = True
                return result
            try:
                changed = self._sync_file(app, profile, gcfg, reason, logs)
                gcfg.pop("error", None)
                result.update(ok=True, changed=changed)
            except Exception as exc:  # surfaced to the UI, logged with traceback by caller
                gcfg["error"] = str(exc)
                errors.append(str(exc))
                if self.log:
                    self.log.exception("sync %s failed", appid)
            self.save_config(cfg)
            return result

    def _sync_file(self, app, profile, gcfg, reason, logs):
        appid = app["appid"]
        desired = gcfg.get("style", "original")
        target = self.target_path(app, profile)
        backup = self.backup_path(appid, profile)
        trailer = read_trailer(target)
        binfo = gcfg.get("backup") or {}
        update_note = ""

        if trailer is None:
            # The live file is an original. Back it up if it is a new one (first run or update).
            sha = sha256_file(target)
            if binfo.get("sha256") != sha or not os.path.exists(backup):
                previous = binfo.get("buildid")
                self._make_backup(target, backup)
                binfo = {"sha256": sha, "size": os.path.getsize(backup), "buildid": app["buildid"],
                         "created": int(time.time())}
                gcfg["backup"] = binfo
                if previous and previous != app["buildid"]:
                    update_note = "build %s -> %s" % (previous, app["buildid"])
                    gcfg["last_event"] = "Game updated (%s)" % update_note
                    logs.append(gcfg["last_event"])
                logs.append("original backed up")
            if desired == "original":
                logs.append("already original")
                return False
            source = target
        else:
            source_sha = trailer.get("source_sha256")
            if binfo.get("sha256") != source_sha and os.path.exists(backup) \
                    and sha256_file(backup) == source_sha:
                # settings were lost but the backup is the right original: adopt it
                binfo = {"sha256": source_sha, "size": os.path.getsize(backup),
                         "buildid": trailer.get("buildid", ""), "created": int(time.time())}
                gcfg["backup"] = binfo
            if (trailer.get("style") == desired and source_sha == binfo.get("sha256")
                    and trailer.get("revision", 1) == profile.revision):
                logs.append("%s already applied" % STYLE_LABEL[desired])
                return False
            if not os.path.exists(backup) or binfo.get("sha256") != source_sha:
                raise RuntimeError("backup of the original is missing; use Steam > Verify files")
            source = backup

        if desired == "original":
            self._replace_from(backup, target, binfo["sha256"])
            logs.append("original restored")
            gcfg["last_event"] = "Original restored"
            return True

        problems = profile.check(source)
        if problems:
            raise RuntimeError("this game version is not supported yet: " + "; ".join(problems[:3]))
        tmp = target + TMP_SUFFIX
        try:
            self._copy(source, tmp)
            profile.patch(tmp, desired, self.icons)
            write_trailer(tmp, {
                "style": desired, "appid": appid, "buildid": app["buildid"],
                "source_sha256": binfo["sha256"], "plugin": self.version,
                "revision": profile.revision,
                "applied": int(time.time()),
            })
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        if reason == "user":
            gcfg["last_event"] = "%s applied" % STYLE_LABEL[desired]
        elif trailer is None:
            gcfg["last_event"] = "%s re-applied after update (%s)" % (
                STYLE_LABEL[desired], update_note or "build %s" % app["buildid"])
        logs.append("%s applied" % STYLE_LABEL[desired])
        self._info("%s: %s applied (%s)", appid, desired, reason)
        return True

    # ------------------------------------------------------------------ file ops

    @staticmethod
    def _need_space(path, size):
        free = shutil.disk_usage(os.path.dirname(path)).free
        if free < size + (64 << 20):
            raise RuntimeError("not enough free space (%d MB needed)" % (size >> 20))

    def _copy(self, src, dst):
        self._need_space(dst, os.path.getsize(src))
        shutil.copyfile(src, dst)

    def _make_backup(self, target, backup):
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        tmp = backup + ".tmp"
        self._copy(target, tmp)
        os.replace(tmp, backup)

    def _replace_from(self, src, target, sha):
        if sha256_file(src) != sha:
            raise RuntimeError("backup is corrupted (checksum mismatch); use Steam > Verify files")
        tmp = target + TMP_SUFFIX
        try:
            self._copy(src, tmp)
            os.replace(tmp, target)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
