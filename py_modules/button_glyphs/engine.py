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

from . import image, ingame, steam
from . import entries
from . import region_patch
from . import files_patch
from . import overlay_patch
from .trailer import TRAILER_MAGIC, read_trailer, write_trailer  # noqa: F401  (re-exported)
from .profiles import PROFILES
from .profiles.config_value import MAX_FILE as MAX_SAVE, ConfigValueError

CONFIG_VERSION = 1
STYLES = ("original", "ps", "xbox")
STYLE_LABEL = {"original": "Original", "ps": "PlayStation", "xbox": "Xbox"}
TMP_SUFFIX = ".button-glyphs.tmp"
OVERLAY_RETRY_SECONDS = 600     # after a failed build the watcher waits this long (an explicit choice retries at once)


def sha256_file(path, chunk=1 << 20):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        while True:
            block = f.read(chunk)
            if not block:
                break
            h.update(block)
    return h.hexdigest()


def fsync_dir(path):
    """Flush a folder's entries (a rename) to disk; best effort where the OS does not allow it."""
    try:
        fd = os.open(path, os.O_RDONLY)
    except OSError:
        return
    try:
        os.fsync(fd)
    except OSError:
        pass
    finally:
        os.close(fd)


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
                 profiles=None, proc_root="/proc", ingame_games=None):
        self.home = home
        self.config_path = os.path.join(settings_dir, "config.json")
        self.backup_root = os.path.join(runtime_dir, "backups")
        self.icons = IconLoader(glyph_dir)
        self.version = version
        self.log = logger
        self.profiles = PROFILES if profiles is None else profiles
        self.proc_root = proc_root
        self.ingame_games = self._load_ingame() if ingame_games is None else ingame_games
        self.lock = threading.RLock()
        os.makedirs(settings_dir, exist_ok=True)
        os.makedirs(self.backup_root, exist_ok=True)

    def _load_ingame(self):
        """Games with a built-in button option (listed only, never touched); a bad file must not break the listing."""
        try:
            return ingame.by_appid()
        except (OSError, ValueError):
            if self.log:
                self.log.exception("cannot read the list of in-game games")
            return {}

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
            f.flush()
            os.fsync(f.fileno())        # the data is on disk before the rename makes it the config
        os.replace(tmp, self.config_path)
        fsync_dir(os.path.dirname(self.config_path))

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
                    game["support"] = getattr(profile, "support", "partial")
                elif appid in self.ingame_games:
                    entry = self.ingame_games[appid]
                    game["support"] = "ingame"
                    how = " ".join(x for x in (entry["where"], entry["note"]) if x)
                    if how:
                        game["how"] = how
                games.append(game)
            games.sort(key=lambda g: (not g["supported"], g["name"].lower()))
            return games

    def _status(self, app, profile, cfg, cmdlines):
        if getattr(profile, "region_level", False):
            return self._status_regions(app, profile, cfg, cmdlines)
        if getattr(profile, "files_level", False):
            return self._status_files(app, profile, cfg, cmdlines)
        if getattr(profile, "overlay_level", False):
            return self._status_overlay(app, profile, cfg, cmdlines)
        if getattr(profile, "config_level", False):
            return self._status_config(app, profile, cfg, cmdlines)
        gcfg = cfg["games"].get(app["appid"], {})
        desired = gcfg.get("style", "original")
        path = self.target_path(app, profile)
        trailer = read_trailer(path)
        applied = trailer["style"] if trailer else "original"
        rows = None
        if getattr(profile, "entry_level", False):
            rows = entries.plan(profile, app["install_path"], desired)
            applied = entries.applied_style(rows)
            trailer = None
        out = {
            "style": desired,
            "applied": applied,
            "has_backup": rows is not None or os.path.exists(self.backup_path(app["appid"], profile)),
            "last_event": gcfg.get("last_event", ""),
            "roles": [label for _, label in profile.roles],
            "original_hint": getattr(profile, "original_hint", ""),
        }
        if not os.path.exists(path):
            out.update(state="error", message="%s not found" % profile.file,
                       code="file_missing", args={"file": profile.file})
        elif steam.is_running(app, cmdlines):
            out.update(state="running", message="Game is running; changes apply when it closes",
                       code="running")
        elif not steam.is_ready(app):
            out.update(state="updating", message="Steam is updating this game", code="updating")
        elif gcfg.get("error"):
            out.update(state="error", message=gcfg["error"])
        elif rows is not None and not all(r[4] for r in rows):
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired],
                       code="will_apply", args={"style": desired})
        elif rows is None and applied != desired:
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired],
                       code="will_apply", args={"style": desired})
        elif trailer and trailer.get("revision", 1) != profile.revision:
            out.update(state="pending", message="Improved %s glyphs will be applied"
                       % STYLE_LABEL[desired], code="will_improve", args={"style": desired})
        elif gcfg.get("error"):
            out.update(state="error", message=gcfg["error"])
        else:
            out.update(state="ok", message="%s glyphs active" % STYLE_LABEL[applied],
                       code="active", args={"style": applied})
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
                if appid in self.profiles and (gcfg.get("style", "original") != "original"
                                               or self._restore_pending(appid, gcfg)
                                               or self._files_restore_pending(appid)
                                               or self._overlay_restore_pending(appid)
                                               or self._marker_restore_pending(appid)):
                    results.append(self.sync(appid, reason=reason))
        return results

    def _restore_pending(self, appid, gcfg):
        """A config-value game set to Original whose recorded value is not back yet (the game
        was running when Original was chosen): the watcher finishes the restore."""
        return (getattr(self.profiles[appid], "config_level", False)
                and gcfg.get("original_value") is not None)

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
        if getattr(profile, "region_level", False):
            return self._sync_regions(app, profile, gcfg, reason, logs)
        if getattr(profile, "files_level", False):
            return self._sync_files(app, profile, gcfg, reason, logs)
        if getattr(profile, "overlay_level", False):
            return self._sync_overlay(app, profile, gcfg, reason, logs)
        if getattr(profile, "config_level", False):
            return self._sync_config(app, profile, gcfg, reason, logs)
        if getattr(profile, "entry_level", False):
            return self._sync_entries(app, profile, gcfg, reason, logs)
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

    # ------------------------------------------------------------------ region-level strategy

    @staticmethod
    def _effective(profile, style):
        """The style to draw: the game's own look (its native style) means "restore"."""
        return "original" if style in ("original", profile.native_style) else style

    def _region_backup_dir(self, appid):
        return os.path.join(self.backup_root, appid, "regions")

    def _status_regions(self, app, profile, cfg, cmdlines):
        gcfg = cfg["games"].get(app["appid"], {})
        desired = gcfg.get("style", "original")
        want = self._effective(profile, desired)
        path = self.target_path(app, profile)
        out = {
            "style": desired,
            "applied": desired,
            "has_backup": os.path.isdir(self._region_backup_dir(app["appid"])),
            "last_event": gcfg.get("last_event", ""),
            "roles": [label for _, label in profile.roles],
            "original_hint": getattr(profile, "original_hint", ""),
        }
        if not os.path.exists(path):
            out.update(state="error", message="%s not found" % profile.file,
                       code="file_missing", args={"file": profile.file})
            return out
        try:
            rows = region_patch.plan(profile, app["install_path"], want)
        except OSError as exc:
            out.update(state="error", message="cannot read %s: %s" % (profile.file, exc),
                       code="cannot_read", args={"file": profile.file, "reason": str(exc)})
            return out
        current = region_patch.applied_style(rows)
        out["applied"] = profile.native_style if current == "original" else current
        if steam.is_running(app, cmdlines):
            out.update(state="running", message="Game is running; changes apply when it closes",
                       code="running")
        elif not steam.is_ready(app):
            out.update(state="updating", message="Steam is updating this game", code="updating")
        elif gcfg.get("error"):                    # an error outranks "pending" (else it hides forever)
            out.update(state="error", message=gcfg["error"])
        elif not all(r[3] for r in rows):
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired],
                       code="will_apply", args={"style": desired})
        else:
            out.update(state="ok", message="%s glyphs active" % STYLE_LABEL[desired],
                       code="active", args={"style": desired})
        return out

    def _sync_regions(self, app, profile, gcfg, reason, logs):
        """Bring every texture region of the archive to the chosen style, in place."""
        appid = app["appid"]
        desired = gcfg.get("style", "original")
        want = self._effective(profile, desired)
        install = app["install_path"]
        backup_dir = self._region_backup_dir(appid)
        rows = region_patch.plan(profile, install, want)
        if all(r[3] for r in rows):
            logs.append("%s already applied" % STYLE_LABEL[desired])
            return False

        todo = [r for r in rows if not r[3]]
        problems = region_patch.check(profile, install, backup_dir, todo)
        if want == "original":
            # only regions that carry our marker need work, and they need their backup
            problems = [p for p in problems if "backup" in p]
            if problems:
                raise RuntimeError("cannot restore: " + "; ".join(problems[:3]))
            region_patch.revert(profile, install, backup_dir, rows)
            gcfg["last_event"] = "Original restored"
            logs.append("original restored")
            gcfg["applied_buildid"] = app["buildid"]
            return True

        if problems:
            raise RuntimeError("this game version is not supported yet: " + "; ".join(problems[:3]))
        preflight = getattr(profile, "preflight", None)
        if preflight:                              # bundled art unusable -> fail before any write
            preflight()
        region_patch.ensure_backups(profile, install, backup_dir, todo)
        region_patch.apply(profile, install, backup_dir, rows, want)

        previous = gcfg.get("applied_buildid")
        gcfg["applied_buildid"] = app["buildid"]
        if reason == "user":
            gcfg["last_event"] = "%s applied" % STYLE_LABEL[desired]
            logs.append("%s applied" % STYLE_LABEL[desired])
        else:
            note = ("build %s -> %s" % (previous, app["buildid"])
                    if previous and previous != app["buildid"] else "build %s" % app["buildid"])
            gcfg["last_event"] = "%s re-applied (%s)" % (STYLE_LABEL[desired], note)
            logs.append(gcfg["last_event"])
        self._info("%s: %s applied to %d region(s) (%s)", appid, desired,
                   len(todo), reason)
        return True

    # ------------------------------------------------------------------ files-level strategy

    def _files_backup_dir(self, appid):
        return os.path.join(self.backup_root, appid, "files")

    def _status_files(self, app, profile, cfg, cmdlines):
        gcfg = cfg["games"].get(app["appid"], {})
        desired = gcfg.get("style", "original")
        want = self._effective(profile, desired)
        backup_dir = self._files_backup_dir(app["appid"])
        out = {
            "style": desired,
            "applied": desired,
            "has_backup": os.path.isdir(backup_dir),
            "last_event": gcfg.get("last_event", ""),
            "roles": [label for _, label in profile.roles],
            "original_hint": getattr(profile, "original_hint", ""),
        }
        try:
            rows = files_patch.plan(profile, app["install_path"], backup_dir)
        except Exception as exc:                   # one game's trouble must never fail the whole listing
            if self.log:
                self.log.exception("status of %s: cannot read its files", app["appid"])
            out.update(state="error", message="cannot read %s: %s" % (profile.file, exc),
                       code="cannot_read", args={"file": profile.file, "reason": str(exc)})
            return out
        out["applied"] = files_patch.applied_style(rows, profile.native_style, profile.patch_styles[0])
        refusal = files_patch.check(rows, want)
        # has this plugin handled the game before? (backups, or a recorded apply in the config)
        touched = out["has_backup"] or bool(gcfg.get("applied_buildid") or gcfg.get("last_event"))
        notice = files_patch.differs(rows, touched)
        todo = [r for r in rows if not files_patch.is_ok(r, want)]
        if steam.is_running(app, cmdlines):
            out.update(state="running", message="Game is running; changes apply when it closes",
                       code="running")
        elif not steam.is_ready(app):
            out.update(state="updating", message="Steam is updating this game", code="updating")
        elif gcfg.get("error"):                    # an error outranks "pending" (else it hides forever)
            out.update(state="error", message=gcfg["error"])
        elif refusal:
            out.update(state="error", message=refusal)
        elif notice:                               # files that are not ours and not the supported build:
            # an error if we may have left art behind, else a notice (an "ok" with a message, like the
            # config strategy's "Start the game once": the status model has no separate notice level)
            out.update(state="error" if touched else "ok", message=notice)
        elif todo and want == "original":
            out.update(state="pending", message="%s will be restored"
                       % ("Original" if desired == "original" else STYLE_LABEL[desired] + " glyphs"),
                       code="will_restore", args={"style": desired})
        elif todo and all(r.state == files_patch.STALE for r in todo):
            out.update(state="pending", message="Improved %s glyphs will be applied" % STYLE_LABEL[desired],
                       code="will_improve", args={"style": desired})
        elif todo:
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired],
                       code="will_apply", args={"style": desired})
        else:
            out.update(state="ok", message="%s glyphs active" % STYLE_LABEL[desired],
                       code="active", args={"style": desired})
        return out

    def _marker_restore_pending(self, appid):
        """An entry / region / single-file game set to Original whose file still carries OUR marker
        (Original was cut by a power loss, or the game was running when it was chosen): the watcher
        finishes the restore. Reacts only to states we wrote (entries patched/half/partial, regions
        patched/writing, our trailer); unknown, strange or missing files never trigger a sync."""
        profile = self.profiles[appid]
        if (getattr(profile, "files_level", False) or getattr(profile, "config_level", False)
                or getattr(profile, "overlay_level", False)):
            return False
        app = steam.installed_apps(self.home).get(appid)
        if not app or not steam.is_ready(app):
            return False
        try:
            if getattr(profile, "entry_level", False):
                rows = entries.plan(profile, app["install_path"], "original")
                return any(r[2] in (entries.PATCHED, entries.HALF, entries.PARTIAL) for r in rows)
            path = self.target_path(app, profile)
            if getattr(profile, "region_level", False):
                with open(path, "rb") as f:
                    return any(region_patch.region_state(f, r)[0] in (region_patch.PATCHED, region_patch.WRITING)
                               for r in profile.regions)
            return read_trailer(path) is not None
        except Exception:                          # the watcher must go on with the other games
            if self.log:
                self.log.exception("watcher: cannot read the files of %s", appid)
            return False

    def _files_restore_pending(self, appid):
        """A loose-files game set to Original whose files still hold our drawing (the game was
        running when Original was chosen): the watcher finishes the restore."""
        profile = self.profiles[appid]
        if not getattr(profile, "files_level", False):
            return False
        app = steam.installed_apps(self.home).get(appid)
        if not app or not steam.is_ready(app):
            return False
        try:
            rows = files_patch.plan(profile, app["install_path"], self._files_backup_dir(appid))
        except Exception:                          # the watcher must go on with the other games
            if self.log:
                self.log.exception("watcher: cannot read the files of %s", appid)
            return False
        return any(r.state in (files_patch.PATCHED, files_patch.STALE) for r in rows)

    def _sync_files(self, app, profile, gcfg, reason, logs):
        """Bring every loose file of the profile to the chosen style, one whole file at a time.

        The files carry no marker, so their state is read from SHA-256 (files_patch.py). Order:
        plan -> check ALL files (any missing/unknown file refuses the whole run, nothing written)
        -> preflight of the bundled art -> backups of every pristine file (verified) -> render all
        -> replace each file atomically. Undo writes the verified backups back in reverse order.
        A file that holds an older drawing of ours is rebuilt from its backup, never from itself.
        """
        appid = app["appid"]
        desired = gcfg.get("style", "original")
        want = self._effective(profile, desired)
        install = app["install_path"]
        backup_dir = self._files_backup_dir(appid)
        files_patch.clean_temps(profile, install, backup_dir)
        rows = files_patch.plan(profile, install, backup_dir)
        if all(files_patch.is_ok(r, want) for r in rows):
            logs.append("%s already applied" % STYLE_LABEL[desired])
            return False

        refusal = files_patch.check(rows, want)
        if refusal:
            raise RuntimeError(refusal)
        if want == "original":
            files_patch.revert(profile, install, backup_dir, rows)
            gcfg["last_event"] = "Original restored"
            logs.append("original restored")
            gcfg["applied_buildid"] = app["buildid"]
            return True

        preflight = getattr(profile, "preflight", None)
        if preflight:                              # bundled art unusable -> fail before any write
            preflight()
        files_patch.ensure_backups(profile, install, backup_dir, rows)
        files_patch.apply(profile, install, backup_dir, rows, want)

        previous = gcfg.get("applied_buildid")
        gcfg["applied_buildid"] = app["buildid"]
        if reason == "user":
            gcfg["last_event"] = "%s applied" % STYLE_LABEL[desired]
            logs.append("%s applied" % STYLE_LABEL[desired])
        else:
            note = ("build %s -> %s" % (previous, app["buildid"])
                    if previous and previous != app["buildid"] else "build %s" % app["buildid"])
            gcfg["last_event"] = "%s re-applied (%s)" % (STYLE_LABEL[desired], note)
            logs.append(gcfg["last_event"])
        self._info("%s: %s applied to %d file(s) (%s)", appid, desired,
                   sum(1 for r in rows if not files_patch.is_ok(r, want)), reason)
        return True

    # ------------------------------------------------------------------ overlay-level strategy

    def _overlay_backup_dir(self, appid):
        return os.path.join(self.backup_root, appid, "overlay")

    def _status_overlay(self, app, profile, cfg, cmdlines):
        gcfg = cfg["games"].get(app["appid"], {})
        desired = gcfg.get("style", "original")
        want = self._effective(profile, desired)
        backup_dir = self._overlay_backup_dir(app["appid"])
        out = {
            "style": desired,
            "applied": desired,
            "has_backup": os.path.isdir(backup_dir),
            "last_event": gcfg.get("last_event", ""),
            "roles": [label for _, label in profile.roles],
            "original_hint": getattr(profile, "original_hint", ""),
        }
        try:
            plan = overlay_patch.plan(profile, app["install_path"], backup_dir, want)
        except Exception as exc:                   # one game's trouble must never fail the whole listing
            if self.log:
                self.log.exception("status of %s: cannot read its files", app["appid"])
            out.update(state="error", message="cannot read %s: %s" % (profile.file, exc),
                       code="cannot_read", args={"file": profile.file, "reason": str(exc)})
            return out
        out["applied"] = overlay_patch.applied_style(plan, profile.native_style, profile.patch_styles[0])
        refusal = overlay_patch.check(plan, want)
        notice = overlay_patch.notice(plan) if want == "original" else None
        todo = overlay_patch.todo(plan, want)
        stray = want == "original" and bool(plan.stray_dirs)
        if steam.is_running(app, cmdlines):
            out.update(state="running", message="Game is running; changes apply when it closes",
                       code="running")
        elif not steam.is_ready(app):
            out.update(state="updating", message="Steam is updating this game", code="updating")
        elif gcfg.get("error"):                    # an error outranks "pending" (else it hides forever)
            out.update(state="error", message=gcfg["error"])
        elif refusal:
            out.update(state="error", message=refusal.message)
            if refusal.code == "foreign_file":
                out.update(code="foreign_file", args={"file": refusal.args["file"]})
            elif refusal.code == "cannot_read":
                out.update(code="cannot_read", args={"file": refusal.args["file"], "reason": refusal.args["reason"]})
            elif refusal.code == "unsupported_build":
                out.update(code="unsupported_build", args={"detail": refusal.args["detail"]})
        elif notice:                               # a file with our name that is not ours: left alone, said so
            out.update(state="ok", message=notice.message, code="foreign_file", args={"file": notice.args["file"]})
        elif (todo or stray) and want == "original":
            out.update(state="pending", message="%s will be restored"
                       % ("Original" if desired == "original" else STYLE_LABEL[desired] + " glyphs"),
                       code="will_restore", args={"style": desired})
        elif todo and all(r.state == overlay_patch.STALE for r in todo):
            out.update(state="pending", message="Improved %s glyphs will be applied" % STYLE_LABEL[desired],
                       code="will_improve", args={"style": desired})
        elif todo:
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired],
                       code="will_apply", args={"style": desired})
        else:
            out.update(state="ok", message="%s glyphs active" % STYLE_LABEL[desired],
                       code="active", args={"style": desired})
        return out

    def _overlay_restore_pending(self, appid):
        """An overlay game set to Original whose added file(s) are still there (the game was running when Original
        was chosen, or a power cut stopped the removal): the watcher finishes it. Reacts only to files WE wrote
        (their hash is in the ledger); a file that is not ours never triggers a sync."""
        profile = self.profiles[appid]
        if not getattr(profile, "overlay_level", False):
            return False
        app = steam.installed_apps(self.home).get(appid)
        if not app or not steam.is_ready(app):
            return False
        try:
            plan = overlay_patch.plan(profile, app["install_path"], self._overlay_backup_dir(appid), "original")
        except Exception:                          # the watcher must go on with the other games
            if self.log:
                self.log.exception("watcher: cannot read the files of %s", appid)
            return False
        return overlay_patch.removable(plan)

    def _sync_overlay(self, app, profile, gcfg, reason, logs):
        """Bring the game to the chosen style by adding (or removing) our overlay file(s).

        Order: clean temps -> plan (reads the game's own entries, checks every pin, builds and proves every output in
        memory; an unknown build or a file that is not ours refuses with nothing written) -> preflight of the art ->
        ledger -> atomic writes. Original deletes what is ours; the game's own files are never opened for writing.
        """
        appid = app["appid"]
        desired = gcfg.get("style", "original")
        want = self._effective(profile, desired)
        install = app["install_path"]
        backup_dir = self._overlay_backup_dir(appid)
        overlay_patch.clean_temps(profile, install, backup_dir)
        plan = overlay_patch.plan(profile, install, backup_dir, want)      # cheap facts only
        if want != "original" and plan.problems:
            # the game is not the build the pins describe (an update), or cannot be read. A file of ours that is PROVEN ours
            # (ledger / build record) was built from the OLD originals and, loaded last, would keep overriding the new
            # ones: remove it (the Original path: hashes re-checked, reverse order), then say so. Never a file we cannot
            # prove ours, never on a plain read failure.
            problem = plan.problems[0]
            if problem.code == "unsupported_build" and overlay_patch.removable(plan):
                overlay_patch.revert(profile, install, backup_dir, plan)
                gcfg["last_event"] = "Overlay removed: the game files changed"
                logs.append("overlay removed: the game is not the supported build")
                self._info("%s: overlay removed, the game is not the supported build", appid)
                raise RuntimeError("%s The overlay added by this plugin was removed, so the game shows its own prompts."
                                   % problem.message)
            raise RuntimeError(problem.message)
        if overlay_patch.needs_adoption(plan) and (reason == "user" or not gcfg.get("error")):
            # a file with our name that no record explains: it is ours only if it equals today's output (full build).
            # The watcher tries this once, not every minute (a failed try leaves the error that stops it).
            overlay_patch.build_plan(profile, install, backup_dir, plan,
                                     [want] if want != "original" else list(profile.patch_styles))
        if overlay_patch.idle(plan, want):
            if plan.built.get(want):                # adopted: the files are today's output, only the records were lost
                plan.fps = overlay_patch._fingerprints(profile, profile.patch_styles)
                overlay_patch.apply(profile, install, backup_dir, plan, want)
            logs.append("%s already applied" % STYLE_LABEL[desired])
            return False

        refusal = overlay_patch.check(plan, want)
        if refusal:
            raise RuntimeError(refusal.message)
        if want == "original":
            overlay_patch.revert(profile, install, backup_dir, plan)
            gcfg["last_event"] = "Original restored"
            logs.append("original restored")
            gcfg["applied_buildid"] = app["buildid"]
            return True

        if reason != "user" and gcfg.get("overlay_retry_after", 0) > time.time():
            raise RuntimeError(gcfg.get("error") or "the last try failed; the watcher waits before trying again")
        try:                                       # the heavy part (drawing, DXT encoding) and the write: never every minute
            overlay_patch.check_room(profile, install)      # cheap, from the pinned sizes, BEFORE anything is built
            preflight = getattr(profile, "preflight", None)
            if preflight:                          # bundled art unusable -> fail before anything is built or written
                preflight()
            plan.fps = overlay_patch._fingerprints(profile, profile.patch_styles)
            overlay_patch.build_plan(profile, install, backup_dir, plan, [want])
            refusal = overlay_patch.check(plan, want)
            if refusal:
                raise RuntimeError(refusal.message)
            if overlay_patch.idle(plan, want):     # the files are today's output; only the records were missing
                overlay_patch.apply(profile, install, backup_dir, plan, want)
                gcfg.pop("overlay_retry_after", None)
                logs.append("%s already applied" % STYLE_LABEL[desired])
                return False
            overlay_patch.apply(profile, install, backup_dir, plan, want)
        except Exception:                          # ANY failure (build, full disk, OSError): the watcher waits, a user choice retries
            gcfg["overlay_retry_after"] = time.time() + OVERLAY_RETRY_SECONDS
            raise
        gcfg.pop("overlay_retry_after", None)

        previous = gcfg.get("applied_buildid")
        gcfg["applied_buildid"] = app["buildid"]
        if reason == "user":
            gcfg["last_event"] = "%s applied" % STYLE_LABEL[desired]
            logs.append("%s applied" % STYLE_LABEL[desired])
        else:
            note = ("build %s -> %s" % (previous, app["buildid"])
                    if previous and previous != app["buildid"] else "build %s" % app["buildid"])
            gcfg["last_event"] = "%s re-applied (%s)" % (STYLE_LABEL[desired], note)
            logs.append(gcfg["last_event"])
        self._info("%s: %s applied (%s file(s)) (%s)", appid, desired,
                   len(overlay_patch.todo(plan, want)), reason)
        return True

    # ------------------------------------------------------------------ entry-level strategy

    def _sync_entries(self, app, profile, gcfg, reason, logs):
        """Bring every archive of an entry-level profile to the chosen style.

        1. undo files that are not as wanted (back to the exact original bytes)
        2. check every file that must change, while all of them are original
        3. patch them
        A game that fails the check is therefore left original, never half-patched.
        """
        appid = app["appid"]
        desired = gcfg.get("style", "original")
        rows = entries.plan(profile, app["install_path"], desired)
        todo = [r for r in rows if not r[4]]
        if not todo:
            logs.append("%s already applied" % STYLE_LABEL[desired])
            return False

        want = {t["file"] for t in entries.wanted(profile, desired)}
        migrated = False
        for target, path, state, trailer, _ in todo:
            if state is None:
                raise RuntimeError("%s not found" % target["file"])
            if state == entries.LEGACY:
                self._migrate_legacy(app, profile, path, trailer, gcfg)
                migrated = True
            elif state != entries.ORIGINAL:
                entries.revert(path, state, trailer)
        if migrated:
            logs.append("moved to the new patch format")

        patch = [r for r in todo if r[0]["file"] in want]
        problems = []
        for target, path, *_ in patch:
            problems += profile.check_target(path, target)
        if problems:
            raise RuntimeError("this game version is not supported yet: " + "; ".join(problems[:3]))

        info = {"appid": appid, "buildid": app["buildid"], "plugin": self.version,
                "revision": profile.revision}
        for target, path, *_ in patch:
            entries.apply(path, profile, target, desired, self.icons, info)
            logs.append("patched %s" % target["file"])

        previous = gcfg.get("applied_buildid")
        gcfg["applied_buildid"] = app["buildid"]
        if desired == "original":
            gcfg["last_event"] = "Original restored"
            logs.append("original restored")
        elif reason == "user":
            gcfg["last_event"] = "%s applied" % STYLE_LABEL[desired]
            logs.append("%s applied" % STYLE_LABEL[desired])
        elif migrated:
            gcfg["last_event"] = "%s kept (patch format updated)" % STYLE_LABEL[desired]
            logs.append(gcfg["last_event"])
        else:
            note = ("build %s -> %s" % (previous, app["buildid"])
                    if previous and previous != app["buildid"] else "build %s" % app["buildid"])
            gcfg["last_event"] = "%s re-applied after update (%s)" % (STYLE_LABEL[desired], note)
            logs.append(gcfg["last_event"])
        self._info("%s: %s applied to %d file(s) (%s)", appid, desired, len(patch), reason)
        return True

    def _migrate_legacy(self, app, profile, path, trailer, gcfg):
        """Files patched by v0.1.x carry a whole-file trailer: restore them from that backup."""
        backup = self.backup_path(app["appid"], profile)
        sha = trailer.get("source_sha256")
        if not sha or not os.path.exists(backup) or sha256_file(backup) != sha:
            raise RuntimeError("cannot restore %s from the old backup; use Steam > Verify files"
                               % os.path.basename(path))
        self._replace_from(backup, path, sha)
        os.remove(backup)
        gcfg.pop("backup", None)

    # ------------------------------------------------------------------ config-value strategy

    def _config_backup_path(self, appid, profile):
        return os.path.join(self.backup_root, appid, os.path.basename(profile.file) + ".orig")

    def _record_path(self, appid, profile):
        stem = os.path.splitext(os.path.basename(profile.file))[0]
        return os.path.join(self.backup_root, appid, stem + ".record.json")

    def _persist_game(self, appid, gcfg):
        """Write the game's config entry to disk now (sync only saves when it is done)."""
        cfg = self.load_config()
        cfg["games"][appid] = gcfg
        self.save_config(cfg)

    # The record says what the player had (original_value) and what we wrote (written_value, plus
    # intended_value while a write is in flight). It lives in the game's config entry AND in a small
    # durable file in the runtime folder, so losing config.json does not lose the way back.
    RECORD_KEYS = ("original_value", "written_value", "intended_value")

    def _read_record(self, appid, profile, gcfg):
        """The recorded state as a dict (config first, else the durable file), or None."""
        if gcfg.get("original_value") is not None:
            return {k: gcfg.get(k) for k in self.RECORD_KEYS}
        try:
            with open(self._record_path(appid, profile), encoding="utf-8") as f:
                rec = json.load(f)
        except (OSError, ValueError):
            return None
        if not isinstance(rec, dict) or not isinstance(rec.get("original_value"), str):
            return None
        return {k: rec.get(k) if isinstance(rec.get(k), str) else None for k in self.RECORD_KEYS}

    def _store_record(self, appid, profile, gcfg, rec):
        """Durably record `rec` (file, then config) - always before the save is written."""
        path = self._record_path(appid, profile)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({k: rec.get(k) for k in self.RECORD_KEYS}, f, sort_keys=True)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        fsync_dir(os.path.dirname(path))
        for k in self.RECORD_KEYS:
            if rec.get(k) is None:
                gcfg.pop(k, None)
            else:
                gcfg[k] = rec[k]
        self._persist_game(appid, gcfg)

    def _drop_record(self, appid, profile, gcfg):
        """Forget the record (file first, then config) once Original is done."""
        path = self._record_path(appid, profile)
        try:
            os.remove(path)
            fsync_dir(os.path.dirname(path))
        except FileNotFoundError:
            pass
        for k in self.RECORD_KEYS:
            gcfg.pop(k, None)
        self._persist_game(appid, gcfg)

    def _ours(self, profile, rec):
        """Values the plugin put (or was about to put) into the save, valid ones only."""
        return {v for v in (rec.get("written_value"), rec.get("intended_value"))
                if v is not None and profile.valid_value(v)}

    def _read_save(self, app, profile):
        """(path, offset, value) of the one save file, or RuntimeError with a plain message.

        The prefix is searched in every Steam library: on a Deck the game may be on the SD card
        while its Proton prefix is on the internal drive.
        """
        paths = profile.find_saves(steam.library_paths(self.home))
        if not paths:
            raise RuntimeError("Save file not found: start the game once, then close it")
        if len(paths) > 1:
            raise RuntimeError("The save file exists in %d Steam libraries; not touching it" % len(paths))
        try:
            with open(paths[0], "rb") as f:
                data = f.read(MAX_SAVE + 1)
            offset, value = profile.locate(data)
        except OSError as exc:
            raise RuntimeError("Cannot read the save file: %s" % exc)
        except ConfigValueError as exc:
            raise RuntimeError("The save file is not in the expected layout (%s); not touching it" % exc)
        return paths[0], offset, value

    def _status_config(self, app, profile, cfg, cmdlines):
        appid = app["appid"]
        gcfg = cfg["games"].get(appid, {})
        desired = gcfg.get("style", "original")
        out = {
            "style": desired,
            "applied": "original",
            "has_backup": os.path.exists(self._config_backup_path(appid, profile)),
            "last_event": gcfg.get("last_event", ""),
            "roles": [label for _, label in profile.roles],
            "original_hint": getattr(profile, "original_hint", ""),
        }
        problem, value = None, None
        try:
            _, _, value = self._read_save(app, profile)
            out["applied"] = profile.style_of(value) or "original"
        except RuntimeError as exc:
            problem = str(exc)
        build = (profile.check_build(app["buildid"], app["install_path"], full=False)
                 if desired != "original" else [])
        rec = self._read_record(appid, profile, gcfg)
        # a game that was never configured is not in error because its save is not there yet
        configured = desired != "original" or rec is not None
        if steam.is_running(app, cmdlines):
            out.update(state="running", message="Game is running; changes apply when it closes",
                       code="running")
        elif not steam.is_ready(app):
            out.update(state="updating", message="Steam is updating this game", code="updating")
        elif problem and configured:
            out.update(state="error", message=problem)
        elif problem:
            out.update(state="ok", message=("Start the game once to enable this"
                                            if problem.startswith("Save file not found")
                                            else "Not available yet: " + problem))
            if problem.startswith("Save file not found"):
                out.update(code="start_game_once")
        elif build:
            out.update(state="error", message="this game version is not supported yet: " + build[0],
                       code="unsupported_build", args={"detail": build[0]})
        elif gcfg.get("error"):
            out.update(state="error", message=gcfg["error"])
        elif desired != "original" and value != profile.values[desired]:
            out.update(state="pending", message="%s will be applied" % STYLE_LABEL[desired],
                       code="will_apply", args={"style": desired})
        elif (desired == "original" and rec is not None and value != rec["original_value"]
              and value in self._ours(profile, rec)):
            out.update(state="pending", message="Original will be restored",
                       code="will_restore", args={"style": "original"})
        elif desired == "original":
            out.update(state="ok", message="Original: the game keeps its own setting",
                       code="original_kept")
        else:
            out.update(state="ok", message="%s glyphs active" % STYLE_LABEL[desired],
                       code="active", args={"style": desired})
        return out

    def _sync_config(self, app, profile, gcfg, reason, logs):
        """Set the one stored value of a config-value game to the chosen style, in place.

        Order (power loss safe): check everything -> copy of the save (first change only) ->
        durable record (original, intended) -> write the value bytes -> read back -> record the
        value as written. Undo writes the recorded original back, only when the file still holds
        a value we wrote, and then forgets the record. Nothing is written when any check fails.
        """
        appid = app["appid"]
        desired = gcfg.get("style", "original")
        rec = self._read_record(appid, profile, gcfg)

        if desired == "original":
            if rec is None:
                logs.append("already original")
                return False
            if not profile.valid_value(rec["original_value"]):
                # a broken record must not wedge the game: drop it, leave the save as it is
                self._drop_record(appid, profile, gcfg)
                gcfg["last_event"] = "Original: the recorded value was not valid, the current value was kept"
                logs.append(gcfg["last_event"])
                return False
            path, offset, current = self._read_save(app, profile)
            changed = False
            if current == rec["original_value"]:
                logs.append("original value already in place")
            elif current in self._ours(profile, rec):
                self._write_value(profile, path, offset, current, rec["original_value"])
                changed = True
                logs.append("original restored")
            else:
                logs.append("the value was changed in the game; kept")
            self._drop_record(appid, profile, gcfg)
            gcfg["last_event"] = "Original restored" if changed else "Original: the game's own value kept"
            return changed

        if rec is not None and not profile.valid_value(rec["original_value"]):
            rec = None                      # unusable record: start a new one from what the file holds
        path, offset, current = self._read_save(app, profile)
        stamp = profile.build_stamp(app["buildid"], app["install_path"])
        cached = (stamp is not None and gcfg.get("verified_buildid") == app["buildid"]
                  and gcfg.get("verified_stamp") == stamp)
        problems = profile.check_build(app["buildid"], app["install_path"], full=not cached)
        if problems:
            gcfg.pop("verified_buildid", None)
            gcfg.pop("verified_stamp", None)
            raise RuntimeError("this game version is not supported yet: " + "; ".join(problems[:3]))
        gcfg["verified_buildid"] = app["buildid"]
        gcfg["verified_stamp"] = stamp

        want = profile.values[desired]
        if current == want:
            logs.append("%s already applied" % STYLE_LABEL[desired])
            return False
        if rec is None:
            self._backup_save_once(path, self._config_backup_path(appid, profile))
            rec = {"original_value": current, "written_value": None}
        rec = dict(rec, intended_value=want)
        self._store_record(appid, profile, gcfg, rec)
        self._write_value(profile, path, offset, current, want)
        self._store_record(appid, profile, gcfg, dict(rec, written_value=want, intended_value=None))
        gcfg["last_event"] = ("%s applied" if reason == "user" else "%s re-applied") % STYLE_LABEL[desired]
        logs.append("%s applied" % STYLE_LABEL[desired])
        self._info("%s: %s applied to the save (%s)", appid, desired, reason)
        return True

    def _backup_save_once(self, path, backup):
        """Whole-save copy before the very first change; an existing copy is never replaced."""
        if os.path.exists(backup):
            return
        os.makedirs(os.path.dirname(backup), exist_ok=True)
        tmp = backup + ".tmp"
        try:
            self._copy(path, tmp)
            with open(tmp, "rb") as f:
                os.fsync(f.fileno())
            os.replace(tmp, backup)
        finally:
            if os.path.exists(tmp):
                os.remove(tmp)
        fsync_dir(os.path.dirname(backup))

    @staticmethod
    def _write_value(profile, path, offset, old, new):
        """Replace `old` by `new` (same length) at `offset`: one small write, flushed to disk."""
        old_b, new_b = old.encode("ascii"), new.encode("ascii")
        if len(old_b) != len(new_b):
            raise RuntimeError("the new value has a different length; not touching the save")
        with open(path, "r+b") as f:
            f.seek(offset)
            if f.read(len(old_b)) != old_b:
                raise RuntimeError("the save changed while it was being updated; nothing was written")
            f.seek(offset)
            f.write(new_b)
            f.flush()
            os.fsync(f.fileno())
        with open(path, "rb") as f:
            data = f.read(MAX_SAVE + 1)
        try:
            found = profile.locate(data)
        except ConfigValueError as exc:
            raise RuntimeError("the save could not be read back after the change: %s" % exc)
        if found != (offset, new):
            raise RuntimeError("the save does not hold the new value after the change")

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
