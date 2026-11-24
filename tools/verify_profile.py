#!/usr/bin/env python3
"""Verify a registered profile on COPIES of the game's real files, with the real Engine, offline.

    python3 tools/verify_profile.py <appid> <dir> [--styles ps,xbox] [--json report.json]
                                    [--only name,name] [--keep] [--quick] [--sheet DIR]

<dir> holds real copies of what the profile touches (the same layout tools/clone_validate.py expects):

  files    (loose files)  the files at their install-relative paths, or the folder the profile calls
                          its own (e.g. Songs for a Hero: the Content folder itself). Everything else in
                          the folder is copied too, so "the untouched files stay identical" is proven.
  regions  (giant archive) one file per region, named like the profile's region key with "/" as "_"
                          (`prompts_hud75.bin`; `r<i>.bin` in profile order is accepted too). A SPARSE
                          stand-in archive is built from them (holes elsewhere, random sentinels around
                          every region), so a 56 GB archive costs nothing. --archive-size sets its length.
  entries / il            the archive(s) / the executable at their install-relative paths.
  config                  the pinned data file and the save at its path inside the Proton prefix.

Everything runs on a clone in a temp folder (never on <dir>), through a fake Steam library, with the REAL
Engine, the REAL profile and its REAL bundled art. Exit status 0 only if every probe passes. A probe that does
not apply to a kind says SKIP and why; it never passes silently. Probes (in this order):

  probe             what it proves                                                    files regions entries il config
  pinned            size + SHA-256 of every copy match the profile's table              x     x      check  check  x
  cycle             ps, ps (no-op), original, ps, xbox, ps, original: WHOLE-tree map    x     x      x      x      x
                    after each step; original = the start; only the profile's units
                    change; markers/headers; backups == originals; no stray temp
  revision-upgrade  an older revision / older art is rebuilt from the backups, equals    x     x      x      x      -
                    a fresh apply, and Original is still exact
  running           game running (path, alias mount, Z:\\, AppId only) defers with         x     x      x      x      x
                    writes forbidden; AppId=<id>0 does not; the watcher applies after
  steam-verify      1 / some / all units put back like Steam "verify files" -> pending    x     x      x      x      -
                    -> watcher re-applies -> equals a fresh apply
  new-build         new build id + restored files -> re-applied with the build note;     x     x      x      x      refuse
                    an update still downloading (StateFlags) defers
  unknown-build     1 byte changed in a pristine file -> refused, NOTHING written, no     x     x      x      x      x
                    backup folder, error status; Original leaves it alone
  assets            missing/truncated/empty/garbage/wrong-size picture -> nothing         x     x      -      -      -
                    written, error shown, Original still exact
  crash             power cut at write event k (0, 1, middle, last-1, last) of apply     x     x      x      x      x
                    AND of restore, as an exception and as a HARD os._exit(9) in a
                    child process (torn write); every unit stays recoverable, the next
                    sync gives the exact tree, Original gives the exact start
  symlinks          link / directory / 3 GB sparse file in place of a game file is        x     -      -      -      -
                    not treated as ours
  ledger            lost/damaged ownership ledger, a pixel-only update under Original,   x     -      -      -      -
                    older drawing + lost ledger: refused or left alone, never guessed
  timings           first ps, warm ps, original, preflight cold/warm, idle watcher tick  x     x      x      x      x
  visual            original-vs-patched contact sheets (needs --sheet and Pillow)         x     x      -      -      -
  catalog           tools/build_button_catalog.py build --check                           x     x      x      x      x

The entry-level (forge) kind is driven at file granularity per write event; torn writes inside one forge
entry are covered by tests/test_entries.py. The config kind has no art and no Steam-verify step.
"""
import argparse
import contextlib
import copy
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))

import clone_validate  # noqa: E402
import verify_worlds as vw  # noqa: E402
from button_glyphs import files_patch, image  # noqa: E402

PASS, FAIL, SKIP = "PASS", "FAIL", "SKIP"
PROBES = ["pinned", "cycle", "revision-upgrade", "running", "steam-verify", "new-build", "unknown-build",
          "assets", "crash", "symlinks", "ledger", "timings", "visual", "catalog"]
LARGE_PROFILE = 16 << 20          # above this many pinned bytes the default timing sample is 1


class Skip(Exception):
    pass


class Check:
    """Collects the failed expectations of one probe."""

    def __init__(self):
        self.fails, self.count = [], 0
        self._notes = {}

    def ok(self, cond, message):
        self.count += 1
        if not cond:
            self.fails.append(message)
        return bool(cond)

    def note(self, message):
        self._notes[message] = self._notes.get(message, 0) + 1

    @property
    def notes(self):
        return ["%s%s" % (m, " (x%d)" % n if n > 1 else "") for m, n in self._notes.items()]


def diff(a, b):
    return sorted(k for k in set(a) | set(b) if a.get(k) != b.get(k))


def short(items, n=4):
    items = list(items)
    return ", ".join(str(i) for i in items[:n]) + (" (+%d more)" % (len(items) - n) if len(items) > n else "")


# ----------------------------------------------------------------------------------------- context


class Ctx:
    """What every probe shares: the profile, the resolved source, a work folder, memoised reference trees."""

    def __init__(self, appid, src_dir, profiles, args, work):
        self.appid = appid
        self.args = args
        self.work = Path(work)
        self.raw_profile = profiles[appid]
        self.kind = vw.KINDS[vw.kind_name(self.raw_profile)](self.raw_profile, args)
        self.kind.resolve(src_dir)
        self.src_dir = Path(src_dir)
        self.use_cache = not args.no_render_cache and hasattr(self.raw_profile, "render")
        self.profile = vw.CachedProfile(self.raw_profile) if self.use_cache else self.raw_profile
        self.installdir = args.installdir
        self.buildid = getattr(self.kind, "build", None) or "1000001"
        self.styles = list(args.styles) if args.styles else clone_validate.profile_styles(self.raw_profile)
        if not self.styles:
            raise vw.SourceError("profile has no patch style")
        self.native = getattr(self.raw_profile, "native_style", None) if self.kind.name in ("files", "regions") else None
        self._refs = {}
        self.worlds = []

    # ---- worlds
    def world(self, name, **kw):
        w = vw.World(self, name, **kw)
        self.worlds.append(w)
        return w

    def drop(self, w):
        shutil.rmtree(w.root, ignore_errors=True)
        if w in self.worlds:
            self.worlds.remove(w)

    def drop_all(self):
        for w in list(self.worlds):
            self.drop(w)

    def effective(self, style):
        return "original" if style == "original" or style == self.native else style

    def ref(self, style):
        """The tree a clean world reaches after applying `style` (memoised)."""
        if style not in self._refs:
            w = self.world("ref-" + style)
            w.set(style)
            self._refs[style] = w.tree()
            self.drop(w)
        return self._refs[style]

    @property
    def style0(self):
        return self.styles[0]

    @property
    def units(self):
        return self.kind.units()

    def changed_keys(self, a, b):
        derived = {"archive"}
        return [k for k in diff(a, b) if k not in derived]


# ----------------------------------------------------------------------------------------- probes


def probe_pinned(ctx):
    ok, total, bad = ctx.kind.pinned(ctx.src_dir)
    chk = Check()
    chk.ok(not bad and ok == total, "%d mismatch(es): %s" % (len(bad), short(bad)))
    for note in ctx.kind.notes:
        chk.note(note)
    return chk, "%d/%d %s" % (ok, total, "ok" if ctx.kind.name in ("files", "regions", "config") else "pass checks")


def cycle_sequence(ctx):
    s0 = ctx.style0
    seq = [s0, s0, "original", s0] + [s for s in ctx.styles[1:]]
    if ctx.native and ctx.native not in ctx.styles:
        seq.append(ctx.native)
    return seq + [s0, "original"]


def probe_cycle(ctx):
    chk = Check()
    w = ctx.world("cycle")
    start, patched, steps = w.start, {}, []
    seq = cycle_sequence(ctx)
    changed_n = 0
    prev_style, prev_tree = None, start
    for style in seq:
        eff = ctx.effective(style)
        t0 = time.perf_counter()
        r = w.set(style)
        dt = time.perf_counter() - t0
        st = w.status()
        t = w.tree()
        steps.append({"style": style, "changed": bool(r.get("changed")), "state": st["state"], "ms": round(dt * 1000, 1)})
        chk.ok(r["ok"] and st["state"] == "ok", "%s: ok=%s state=%s (%s)" % (style, r["ok"], st["state"], short(r["errors"] + [st["message"]], 1)))
        if eff == "original":
            chk.ok(t == start, "%s: tree is not the start (%s)" % (style, short(ctx.changed_keys(start, t))))
        else:
            expected = {ctx.kind.key(u) for u in ctx.kind.expected_changed(w, eff)}
            if eff not in patched:
                got = set(ctx.changed_keys(start, t))
                chk.ok(got == expected, "%s changed %d, expected %d: extra %s, missing %s" % (
                    style, len(got), len(expected), short(sorted(got - expected)), short(sorted(expected - got))))
                changed_n = max(changed_n, len(got))
                for problem in ctx.kind.check_markers(w, eff, t):
                    chk.ok(False, "%s: %s" % (style, problem))
                for problem in ctx.kind.check_backups(w) if expected else []:
                    chk.ok(False, "%s: %s" % (style, problem))
                for other, other_tree in patched.items():
                    chk.ok(t != other_tree or not expected, "%s output equals %s output" % (style, other))
                patched[eff] = t
            else:
                chk.ok(t == patched[eff], "%s again: tree differs from its first apply (%s)" % (style, short(diff(t, patched[eff]))))
            if prev_style == style:
                chk.ok(not r.get("changed"), "%s twice: the second apply reported a change" % style)
        chk.ok(bool(r.get("changed")) == (t != prev_tree), "%s: changed flag is %s but the tree %s" % (
            style, r.get("changed"), "changed" if t != prev_tree else "did not change"))
        chk.ok(not w.temps(), "%s: stray temp files %s" % (style, short(w.temps())))
        prev_style, prev_tree = style, t
    ctx.drop(w)
    return chk, "%d steps, %d %s(s) changed, %d identical" % (len(seq), changed_n, ctx.kind.unit_noun, len(start) - changed_n)


def damage(path, mode):
    path = str(path)
    data = vw.read_bytes(path) if os.path.exists(path) else b""
    if mode == "missing":
        os.remove(path)
    elif mode == "truncated":
        with open(path, "wb") as f:
            f.write(data[:len(data) // 2])
    elif mode == "empty file":
        open(path, "wb").close()
    elif mode == "not a png":
        with open(path, "wb") as f:
            f.write(b"GIF89a" + b"\0" * 50)


def probe_assets(ctx):
    if not ctx.kind.has_art or not hasattr(ctx.raw_profile, "preflight"):
        raise Skip("%s profiles draw no bundled art" % ctx.kind.name)
    chk = Check()
    raw, s0 = ctx.raw_profile, ctx.style0
    victim = vw.victim_art(raw)
    modes = ["missing", "truncated", "empty file", "not a png"]
    other = None
    if ctx.kind.name == "files":                    # the picture must be exactly the texture's size
        want = {(e["width"], e["height"]) for e in raw.files}
        for folder, name in vw.art_refs(raw):
            if name != victim:
                _, w_, h_ = image.read_png(os.path.join(str(folder), name + ".png"))
                vw_, vh_ = next((e["width"], e["height"]) for e in raw.files if e["image"] == victim)
                if (w_, h_) != (vw_, vh_):
                    other = name
                    break
        if other:
            modes.append("wrong size")
        else:
            chk.note("wrong-size case skipped: every picture has the same size")
    cases = 0
    for mode in modes:
        slug = mode.replace(" ", "_")
        art = ctx.work / ("art-" + slug)
        vw.copy_art(raw, art)
        if mode == "wrong size":
            shutil.copyfile(art / (other + ".png"), art / (victim + ".png"))
        else:
            damage(art / (victim + ".png"), mode)
        w = ctx.world("assets-" + slug, profile=vw.CachedProfile(vw.fresh_profile(raw, art)))
        r = w.set(s0)
        st = w.status()
        chk.ok(not r["ok"] and w.tree() == w.start and not w.backups_exist() and st["state"] == "error",
               "art %s: ok=%s untouched=%s backups=%s state=%s" % (mode, r["ok"], w.tree() == w.start, w.backups_exist(), st["state"]))
        r2 = w.set("original")
        chk.ok(r2["ok"] and w.tree() == w.start and not w.temps(), "art %s: Original not exact" % mode)
        cases += 1
        ctx.drop(w)
        shutil.rmtree(art, ignore_errors=True)
    art = ctx.work / "art-gone"
    vw.copy_art(raw, art)
    w = ctx.world("assets-gone", profile=vw.CachedProfile(vw.fresh_profile(raw, art)))
    w.set(s0)
    shutil.rmtree(art)
    r = w.set("original", eng=w.engine(profile=vw.fresh_profile(raw, art)))
    chk.ok(r["ok"] and w.tree() == w.start, "art deleted after the patch: Original not exact")
    ctx.drop(w)
    return chk, "%d cases, nothing written" % (cases + 1)


def probe_revision_upgrade(ctx):
    if ctx.kind.name == "config":
        raise Skip("a saved value has no revision or art to upgrade")
    chk = Check()
    raw, s0 = ctx.raw_profile, ctx.style0
    variants = []
    if ctx.kind.has_art:
        art = ctx.work / "art-v2"
        vw.copy_art(raw, art)
        vw.tweak_png(art / (vw.victim_art(raw) + ".png"))
        newer = vw.fresh_profile(raw, art)
        newer.revision = raw.revision + 1
        variants.append(("revision bumped, art changed", newer))
        if ctx.kind.name == "files":
            same = vw.fresh_profile(raw, art)
            variants.append(("same revision, art changed (ledger)", same))
    else:
        newer = copy.copy(raw)
        newer.revision = raw.revision + 1
        variants.append(("revision bumped", newer))
    rewritten = 0
    for label, p2 in variants:
        slug = label.split(",")[0].replace(" ", "_") + ("_same" if "same" in label else "")
        p2c = vw.CachedProfile(p2)
        ref1 = ctx.ref(s0)
        wr = ctx.world("up-ref-" + slug, profile=p2c)
        wr.set(s0)
        ref2 = wr.tree()
        ctx.drop(wr)
        if ref2 == ref1 and p2.revision == raw.revision:
            chk.note("%s: the art change did not alter any render; case skipped" % label)
            continue
        w = ctx.world("up-" + slug)
        w.set(s0)
        v1 = w.tree()
        eng2 = w.engine(profile=p2c)
        st = w.status(eng2)
        chk.ok(st["state"] == "pending", "%s: status before the upgrade is %s, expected pending" % (label, st["state"]))
        res = w.sync_all(eng2)
        chk.ok(bool(res) and res[0]["ok"] and res[0]["changed"], "%s: the watcher did not re-apply (%s)" % (label, res))
        after = w.tree()
        chk.ok(after == ref2, "%s: result differs from a fresh apply of the new profile (%s)" % (label, short(diff(after, ref2))))
        rewritten = max(rewritten, len(ctx.changed_keys(v1, after)))
        if ctx.kind.name == "files":
            want = set(ctx.changed_keys(ref1, ref2))
            chk.ok(set(ctx.changed_keys(v1, after)) == want, "%s: rewrote %s, only %s changed" % (label, short(ctx.changed_keys(v1, after)), short(want)))
        r = w.set("original", eng=eng2)
        chk.ok(r["ok"] and w.tree() == w.start and not w.temps(), "%s: Original after the upgrade is not exact" % label)
        ctx.drop(w)
    if not rewritten and not chk.fails and not variants:
        raise Skip("nothing to upgrade")
    return chk, "%d %s(s) rebuilt, Original exact" % (rewritten, ctx.kind.unit_noun)


GAME_EXE = "Game.exe"


def probe_running(ctx):
    chk = Check()
    s0 = ctx.style0
    appid = ctx.appid

    def defer(w, label, line, style):
        w.start_game(line)
        with vw.forbidding(w) as f:
            r = w.set(style)
        st = w.status()
        ok = r["ok"] and not r["changed"] and f.hits == 0 and st["state"] == "running" \
            and w.tree() == w.start and not w.backups_exist()
        chk.ok(ok, "%s: ok=%s changed=%s write attempts=%d state=%s untouched=%s" % (
            label, r["ok"], r["changed"], f.hits, st["state"], w.tree() == w.start))
        w.stop_game()
        return ok

    w = ctx.world("run")
    inst = str(w.install)
    forms = [("posix path", "reaper SteamLaunch -- proton run %s/%s" % (inst, GAME_EXE)),
             ("Z:\\ path", "Z:" + (inst + "/" + GAME_EXE).replace("/", "\\")),
             ("AppId only", "reaper SteamLaunch AppId=%s -- /usr/bin/true" % appid)]
    deferred = 0
    for label, line in forms:
        deferred += defer(w, label, line, s0)
    # the watcher applies once the game is closed
    res = w.sync_all()
    t = w.tree()
    expected = {ctx.kind.key(u) for u in ctx.kind.expected_changed(w, ctx.effective(s0))}
    chk.ok(bool(res) and res[0]["ok"] and set(ctx.changed_keys(w.start, t)) == expected and w.status()["state"] == "ok",
           "after the game closed the watcher did not apply %s" % s0)
    # a different game whose id merely starts with ours does not defer
    w2 = ctx.world("run-other")
    w2.start_game("reaper SteamLaunch AppId=%s0 -- /usr/bin/true" % appid)
    r = w2.set(s0)
    chk.ok(r["ok"] and set(ctx.changed_keys(w2.start, w2.tree())) == expected, "AppId=%s0 wrongly deferred or failed" % appid)
    w2.stop_game()
    w2.set("original")
    chk.ok(w2.tree() == w2.start, "Original after the AppId=%s0 case is not exact" % appid)
    ctx.drop(w2)
    # Original chosen while the game runs: deferred, then finished by the watcher where the engine does that
    if expected:
        w.start_game(forms[0][1])
        with vw.forbidding(w) as f:
            r = w.set("original")
        chk.ok(r["ok"] and not r["changed"] and f.hits == 0 and w.status()["state"] == "running" and w.tree() == t,
               "Original while running was not deferred cleanly")
        w.stop_game()
        res = w.sync_all()
        if w.tree() != w.start:
            chk.note("Original chosen while the game ran is NOT finished by the watcher (status stays pending until Original is chosen again)")
            w.set("original")
        chk.ok(w.tree() == w.start and not w.temps(), "Original after the game closed is not exact")
    ctx.drop(w)
    # the Deck's SD card: the library path and the process path may be different spellings of one folder
    wa = ctx.world("run-alias", media=True)
    with wa.media_patch():
        alias = str(wa.media / "LX512" / "steamapps" / "common" / ctx.installdir)
        for label, line in (("alias mount, posix", "reaper SteamLaunch -- proton run %s/%s" % (alias, GAME_EXE)),
                            ("alias mount, Z:\\", "Z:" + (alias + "/" + GAME_EXE).replace("/", "\\"))):
            deferred += defer(wa, label, line, s0)
    ctx.drop(wa)
    return chk, "%d/%d deferred, 0 writes" % (deferred, len(forms) + 2)


def backups_of(w):
    """The plugin's backed-up originals (name -> SHA-256); the ledger is rewritten on every apply and is not one."""
    if not w.backups_exist():
        return {}
    return {k: v for k, v in vw.tree_of(w.backup_root).items() if not k.endswith("renders.log")}


def probe_steam_verify(ctx):
    if not ctx.kind.unit_restore:
        raise Skip("the saved value is not part of what Steam verifies")
    chk = Check()
    s0 = ctx.style0
    ref = ctx.ref(s0)
    w0 = ctx.world("verify-probe")
    patched_units = sorted(ctx.kind.expected_changed(w0, ctx.effective(s0)))
    ctx.drop(w0)
    if not patched_units:
        raise Skip("%s changes nothing in this profile" % s0)
    sizes = sorted({1, max(1, len(patched_units) // 2), len(patched_units)})
    for n in sizes:
        w = ctx.world("verify-%d" % n)
        w.set(s0)
        before = backups_of(w)
        for u in patched_units[:n]:
            ctx.kind.restore_unit(w, u)
        pend = w.status()
        res = w.sync_all()
        chk.ok(pend["state"] == "pending", "%d restored: status %s, expected pending (%s)" % (n, pend["state"], pend["message"]))
        chk.ok(bool(res) and res[0]["ok"] and res[0]["changed"], "%d restored: the watcher did not re-apply" % n)
        chk.ok(w.tree() == ref, "%d restored: not equal to a fresh apply (%s)" % (n, short(diff(w.tree(), ref))))
        after = backups_of(w)
        chk.ok(before == after, "%d restored: the backups changed" % n)
        chk.ok(w.status()["state"] == "ok" and not w.temps(), "%d restored: not ok afterwards" % n)
        ctx.drop(w)
    return chk, "restored %s of %d: re-applied == fresh" % ("/".join(str(n) for n in sizes), len(patched_units))


def probe_new_build(ctx):
    chk = Check()
    s0 = ctx.style0
    if ctx.kind.name == "config":
        w = ctx.world("newbuild")
        w.write_manifest("4", "999")
        r = w.set(s0)
        st = w.status()
        chk.ok(not r["ok"] and w.tree() == w.start and st["state"] == "error" and not w.backups_exist(),
               "an unpinned build was not refused cleanly: ok=%s state=%s" % (r["ok"], st["state"]))
        ctx.drop(w)
        return chk, "unpinned build refused, nothing written"
    ref = ctx.ref(s0)
    w0 = ctx.world("newbuild-probe")
    units = sorted(ctx.kind.expected_changed(w0, ctx.effective(s0)))
    ctx.drop(w0)
    if not units:
        raise Skip("%s changes nothing in this profile" % s0)
    w = ctx.world("newbuild")
    w.set(s0)
    old = w.buildid
    new = str(int(old) + 1111) if old.isdigit() else old + "9"
    w.write_manifest("4", new)
    for u in units:
        ctx.kind.restore_unit(w, u)
    pre = w.status()
    res = w.sync_all()
    chk.ok(pre["state"] == "pending", "new build, files restored: status %s, expected pending" % pre["state"])
    chk.ok(bool(res) and res[0]["ok"] and res[0]["changed"], "new build: not re-applied")
    chk.ok(w.tree() == ref, "new build: not equal to a fresh apply (%s)" % short(diff(w.tree(), ref)))
    event = w.cfg().get("last_event", "")
    chk.ok(new in event and (not ctx.kind.note_has_old or "%s -> %s" % (old, new) in event),
           "new build: last_event %r lacks the build note" % event)
    chk.ok(w.status()["state"] == "ok", "new build: not ok afterwards")
    ctx.drop(w)
    w = ctx.world("newbuild-dl")
    w.set(s0)
    w.write_manifest("1026", new)
    for u in units:
        ctx.kind.restore_unit(w, u)
    before = w.tree()
    with vw.forbidding(w) as f:
        w.sync_all()
    chk.ok(f.hits == 0 and w.status()["state"] == "updating" and w.tree() == before,
           "an update still downloading did not defer (hits=%d state=%s)" % (f.hits, w.status()["state"]))
    w.write_manifest("4")
    w.sync_all()
    chk.ok(w.tree() == ref, "after the download finished the watcher did not re-apply")
    ctx.drop(w)
    return chk, "%s -> %s re-applied, 1026 deferred" % (old, new)


def probe_unknown_build(ctx):
    chk = Check()
    s0 = ctx.style0
    w0 = ctx.world("unk-probe")
    cases = ctx.kind.corrupt_cases(w0)
    ctx.drop(w0)
    n = 0
    for i, case in enumerate(cases):
        w = ctx.world("unk-%d" % i)
        vw.apply_corruption(w, case)
        b4 = w.tree()
        r = w.set(s0)
        st = w.status()
        keeps_backup = getattr(ctx.kind, "backup_before_check", False)
        chk.ok(not r["ok"] and w.tree() == b4 and st["state"] == "error" and (keeps_backup or not w.backups_exist()),
               "%s: ok=%s untouched=%s state=%s backups=%s" % (case[0], r["ok"], w.tree() == b4, st["state"], w.backups_exist()))
        if keeps_backup and w.backups_exist():
            chk.note("%s: the engine backs the file up (in its own folder) before refusing it" % case[0])
        with vw.forbidding(w) as f:
            w.sync_all()
        chk.ok(f.hits == 0, "%s: the watcher tried to write" % case[0])
        r2 = w.set("original")
        st2 = w.status()
        chk.ok(r2["ok"] and w.tree() == b4, "%s: Original touched the unknown file (ok=%s)" % (case[0], r2["ok"]))
        if ctx.kind.name == "files":
            chk.ok("not the supported build" in st2["message"],
                   "%s: Original does not report the unknown file (%s: %s)" % (case[0], st2["state"], st2["message"][:60]))
        n += 1
        ctx.drop(w)
    if not n:
        raise Skip("no corruption case for this kind")
    return chk, "%d cases refused, nothing written" % n


def pick_points(n, quick):
    pts = {0, n // 2} if quick else {0, 1, n // 2, n - 2, n - 1}
    return sorted(p for p in pts if 0 <= p < n)


# Kinds whose watcher (`Engine.sync_all`) finishes an interrupted Original by itself: a failure to do so is a FAIL.
# The others (config) keep only a note: their Original is finished by `_restore_pending` when a value was recorded.
WATCHER_RESUMES_ORIGINAL = ("entries", "regions", "il", "files")


def crash_case(ctx, mode, k, hard, scope, ref, chk, label):
    s0 = ctx.style0
    kind = ctx.kind
    w = ctx.world("crash")
    roots = kind.crash_scopes(w)[scope]
    if mode == "restore":
        w.set(s0)
    want = ref if mode == "apply" else w.start
    style = s0 if mode == "apply" else "original"
    if hard:
        spec = {"root": str(w.root), "appid": ctx.appid, "style": style, "k": k, "roots": roots,
                "factory": ctx.args.profiles, "arg": ctx.args.profiles_arg,
                "sys_path": [p for p in sys.path if p and os.path.isdir(p)][:12]}
        p = vw.run_child(spec)
        reached = p.returncode == 9
        if not reached and p.returncode != 0:
            chk.ok(False, "%s: the child failed (%s)" % (label, p.stderr.strip().splitlines()[-1:] or p.returncode))
    else:
        with vw.CrashInjector(roots, k=k) as inj:
            w.set(style)
        reached = inj.count > k
    chk.ok(reached, "%s: write event %d was never reached" % (label, k))
    mid = w.tree()
    bad = [u for u in ctx.units if not kind.unit_ok(w, u, mid, w.start, ref)]
    chk.ok(not bad, "%s: %s is neither of its two whole versions nor recoverable" % (label, short(bad)))
    unit_keys = {kind.key(u) for u in ctx.units}
    foreign = [k_ for k_ in ctx.changed_keys(w.start, mid) if k_ not in unit_keys and not k_.endswith(vw.TMP_SUFFIX)]
    chk.ok(not foreign, "%s: files outside the profile changed: %s" % (label, short(foreign)))
    eng2 = w.engine()
    st = w.status(eng2)
    chk.ok(st["state"] in ("error", "pending") or mid in (want, w.start), "%s: state after the crash is %r" % (label, st["state"]))
    res = w.sync_all(eng2)
    if not res and w.tree() != want:
        if mode == "restore" and ctx.kind.name in WATCHER_RESUMES_ORIGINAL:
            chk.ok(False, "%s: the watcher did not resume a cut Original (config says Original, our marker is still in the file)" % label)
        else:
            chk.note("%s: the watcher does not resume this; the next explicit sync did" % ("%s %s" % (mode, ctx.kind.name)))
        w.sync(eng2)
    chk.ok(w.tree() == want, "%s: after the next sync the tree is not the expected one (%s)" % (label, short(diff(w.tree(), want))))
    chk.ok(not w.game_temps(), "%s: temp files left in the game's folders after the next sync: %s" % (label, short(w.game_temps())))
    if w.plugin_temps():
        chk.note("a temp file stays in the plugin's own folder after a hard crash (harmless, replaced by the next write)")
    chk.ok(w.status(eng2)["state"] == "ok", "%s: status after recovery is %s" % (label, w.status(eng2)["state"]))
    if mode == "apply":
        w.set("original", eng=eng2)
        chk.ok(w.tree() == w.start, "%s: Original after the recovery is not the start (%s)" % (label, short(diff(w.tree(), w.start))))
    ctx.drop(w)


def probe_crash(ctx):
    chk = Check()
    s0 = ctx.style0
    kind = ctx.kind
    ref = ctx.ref(s0)
    wc = ctx.world("crash-count")
    scopes = kind.crash_scopes(wc)
    ctx.drop(wc)
    main = next(iter(scopes))
    counts = {}
    for scope in scopes:
        wcs = ctx.world("crash-count-" + scope)
        roots = kind.crash_scopes(wcs)[scope]
        with vw.CrashInjector(roots) as inj:
            wcs.set(s0)
        counts[(scope, "apply")] = inj.count
        if scope == main:
            with vw.CrashInjector(roots) as inj:
                wcs.set("original")
            counts[(scope, "restore")] = inj.count
        ctx.drop(wcs)
    cases = 0
    if not counts[(main, "apply")]:
        chk.ok(False, "no write event seen while applying: the crash hooks do not see this kind's writes")
    variants = (False, True)
    for mode in ("apply", "restore"):
        n = counts.get((main, mode), 0)
        if mode == "restore" and ctx.native == ctx.style0:
            continue
        for hard in variants:
            for k in pick_points(n, ctx.args.quick):
                label = "%s k=%d/%d %s" % (mode, k, n, "HARD os._exit" if hard else "exception")
                crash_case(ctx, mode, k, hard, main, ref, chk, label)
                cases += 1
    for scope in scopes:
        if scope == main:
            continue
        n = counts[(scope, "apply")]
        pts = pick_points(n, True) if scope == "backup" else pick_points(min(n, 2), True)
        for hard in variants:
            for k in pts:
                label = "apply[%s] k=%d/%d %s" % (scope, k, n, "HARD os._exit" if hard else "exception")
                crash_case(ctx, "apply", k, hard, scope, ref, chk, label)
                cases += 1
    return chk, "%d power cuts recovered" % cases


def probe_symlinks(ctx):
    if ctx.kind.name != "files":
        raise Skip("only loose-file games can have a file replaced by a link")
    chk = Check()
    s0 = ctx.style0
    unit = ctx.units[len(ctx.units) // 2]
    cases = ["symlink to a copy", "symlink to a directory", "directory", "3 GB sparse file"]
    for i, label in enumerate(cases):
        w = ctx.world("sym-%d" % i)
        outside = vw.make_symlink_case(w, label, unit)
        before_out = vw.sha_file(outside) if outside.is_file() else None
        b4 = w.tree()
        t0 = time.perf_counter()
        r = w.set(s0)
        st = w.status()
        dt = time.perf_counter() - t0
        chk.ok(not r["ok"] and st["state"] == "error" and w.tree() == b4 and not w.backups_exist(),
               "%s: not refused cleanly (ok=%s state=%s untouched=%s)" % (label, r["ok"], st["state"], w.tree() == b4))
        r2 = w.set("original")
        chk.ok(r2["ok"] and w.tree() == b4, "%s: Original touched it" % label)
        if before_out:
            chk.ok(vw.sha_file(outside) == before_out, "%s: the link's target was written" % label)
        if "sparse" in label:
            chk.ok(dt < 5, "%s: took %.1f s (it must not be read)" % (label, dt))
        ctx.drop(w)
    # a patched file replaced by a link afterwards
    w = ctx.world("sym-after")
    w.set(s0)
    ref_before = w.tree()
    outside = vw.make_symlink_case(w, "symlink to a copy", unit)
    b4 = w.tree()
    r = w.set(s0)
    chk.ok(not r["ok"] and w.tree() == b4, "patched file replaced by a link: ps not refused untouched")
    w.set("original")
    now = w.tree()
    others = [k for k in vw.tree_of(w.install) if k != unit and k in w.start]
    chk.ok(all(now[k] == w.start[k] for k in others) and os.path.islink(w.install / unit),
           "patched file replaced by a link: Original did not restore the others or touched the link")
    ctx.drop(w)
    return chk, "%d cases not ours, untouched" % (len(cases) + 1)


def probe_ledger(ctx):
    if ctx.kind.name != "files":
        raise Skip("only loose-file games keep an ownership ledger")
    chk = Check()
    raw, s0 = ctx.raw_profile, ctx.style0
    unit = ctx.units[0]
    e = ctx.kind.entry(unit)
    flip_at = e["header"] + 1

    def flip(w, rel, off):
        with open(w.install / rel, "r+b") as f:
            f.seek(off)
            b = f.read(1)
            f.seek(off)
            f.write(bytes([b[0] ^ 1]))

    # (a) pixel-only game update while Original is chosen: nothing written, reported
    w = ctx.world("led-a")
    w.set(s0)
    w.set("original")
    flip(w, unit, flip_at)
    b4 = w.tree()
    with vw.forbidding(w) as f:
        res = w.sync_all()
        r = w.sync()
    st = w.status()
    chk.ok(f.hits == 0 and w.tree() == b4 and st["state"] == "error" and "not the supported build" in st["message"],
           "pixel-only update under Original: hits=%d state=%s msg=%r" % (f.hits, st["state"], st["message"][:50]))
    ctx.drop(w)
    # (b) a pixel changed inside a patched file: unknown, ps refused, Original restores the others only
    w = ctx.world("led-b")
    w.set(s0)
    flip(w, unit, flip_at)
    b4 = w.tree()
    with vw.forbidding(w) as f:
        r = w.set(s0)
    chk.ok(not r["ok"] and w.tree() == b4 and f.hits == 0, "patched file with a changed pixel: ps not refused untouched")
    w.set("original")
    now = w.tree()
    chk.ok(now[unit] == b4[unit] and all(now[k] == w.start[k] for k in w.start if k != unit),
           "patched file with a changed pixel: Original overwrote the new bytes or missed the others")
    ctx.drop(w)
    # (c) ledger deleted after a patch: files equal today's render stay patched, Original exact
    w = ctx.world("led-c")
    w.set(s0)
    ref = w.tree()
    led = files_patch.ledger_path(ctx.kind.backup_dir(w))
    if os.path.exists(led):
        os.remove(led)
    st = w.status()
    r = w.set(s0)
    chk.ok(st["state"] == "ok" and r["ok"] and not r["changed"] and w.tree() == ref, "ledger deleted: files no longer treated as ours")
    r = w.set("original")
    chk.ok(r["ok"] and w.tree() == w.start, "ledger deleted: Original not exact")
    ctx.drop(w)
    # (d) an older drawing and a lost ledger: refused with the hint, nothing written; Verify files then recovers
    art = ctx.work / "art-led"
    vw.copy_art(raw, art)
    vw.tweak_png(art / (e["image"] + ".png"))
    p2 = vw.CachedProfile(vw.fresh_profile(raw, art))
    p2.revision = raw.revision + 1
    w = ctx.world("led-d")
    w.set(s0)
    eng2 = w.engine(profile=p2)
    led = files_patch.ledger_path(ctx.kind.backup_dir(w))
    os.remove(led)
    b4 = w.tree()
    with vw.forbidding(w) as f:
        r = w.set(s0, eng=eng2)
    st = w.status(eng2)
    chk.ok(not r["ok"] and w.tree() == b4 and f.hits == 0 and st["state"] == "error" and "Verify files" in st["message"],
           "older drawing + lost ledger: ok=%s hits=%d state=%s" % (r["ok"], f.hits, st["state"]))
    ctx.kind.restore_unit(w, unit)
    r = w.set(s0, eng=eng2)
    chk.ok(r["ok"] and w.status(eng2)["state"] == "ok", "after Steam verify the new drawing was not applied from the backups")
    ctx.drop(w)
    shutil.rmtree(art, ignore_errors=True)
    return chk, "4 ownership cases, 0 guessed writes"


def probe_timings(ctx):
    chk = Check()
    raw, s0 = ctx.raw_profile, ctx.style0
    n = ctx.args.timings_n or (1 if ctx.args.quick or ctx.kind.pinned_bytes() > LARGE_PROFILE else 5)
    cols = {k: [] for k in ("preflight cold", "preflight warm", "first ps", "original", "warm ps", "idle tick", "status")}
    for i in range(n):
        p = vw.fresh_profile(raw) if hasattr(raw, "pictures") or hasattr(raw, "_icons") else copy.copy(raw)
        w = ctx.world("time-%d" % i, profile=p)
        pre = getattr(p, "preflight", None)
        if pre:
            t0 = time.perf_counter()
            pre()
            cols["preflight cold"].append(time.perf_counter() - t0)
            t0 = time.perf_counter()
            pre()
            cols["preflight warm"].append(time.perf_counter() - t0)
            p = vw.fresh_profile(raw)
            w.eng = w.engine(profile=p)
        for name, style in (("first ps", s0), ("original", "original"), ("warm ps", s0)):
            t0 = time.perf_counter()
            r = w.set(style)
            cols[name].append(time.perf_counter() - t0)
            chk.ok(r["ok"], "%s failed: %s" % (name, short(r["errors"], 1)))
        t0 = time.perf_counter()
        w.sync_all()
        cols["idle tick"].append(time.perf_counter() - t0)
        t0 = time.perf_counter()
        w.status()
        cols["status"].append(time.perf_counter() - t0)
        ctx.drop(w)
    med = {k: round(statistics.median(v) * 1000, 1) for k, v in cols.items() if v}
    for k, v in med.items():
        chk.note("%s: %.1f ms (n=%d)" % (k, v, n))
    chk.data = {"median_ms": med, "n": n}
    return chk, "first ps %s (n=%d)" % (fmt_ms(med.get("first ps", 0)), n)


def fmt_ms(ms):
    return "%.0f ms" % ms if ms < 2000 else "%.1f s" % (ms / 1000)


def decode_unit(ctx, unit, data):
    """(rgba, w, h) of a unit's pixels from its bytes, or None when this kind has no decoder here."""
    kind = ctx.kind
    if kind.name == "files":
        from button_glyphs import xnb
        e = kind.entry(unit)
        return xnb.pixels(data), e["width"], e["height"]
    if kind.name == "regions":
        r = kind.region(unit)
        w, h = r["width"], r["height"]
        body = data[128:]
        if len(body) == w * h * 4:                                  # raw BGRA
            rgba = bytearray(body)
            rgba[0::4], rgba[2::4] = body[2::4], body[0::4]
            return bytes(rgba), w, h
        if len(body) == w * h:                                      # DXT5, one level
            return image.decode(body, w, h, "DXT5"), w, h
    return None


def probe_visual(ctx):
    sheet = ctx.args.sheet
    if not sheet:
        raise Skip("no --sheet folder given")
    if ctx.kind.name not in ("files", "regions"):
        raise Skip("%s games draw no pixels of their own" % ctx.kind.name)
    try:
        from PIL import Image
    except ImportError:
        raise Skip("Pillow is not installed")
    chk = Check()
    s0 = ctx.style0
    w = ctx.world("visual")
    w.set(s0)
    out = []
    for unit in ctx.units[:12]:
        if ctx.kind.name == "files":
            before = vw.read_bytes(ctx.kind.files[unit])
            after = vw.read_bytes(w.install / unit)
        else:
            r = ctx.kind.region(unit)
            before = vw.read_bytes(ctx.kind.files[unit])
            with open(ctx.kind.archive(w), "rb") as f:
                f.seek(r["offset"])
                after = f.read(r["size"])
        a, b = decode_unit(ctx, unit, before), decode_unit(ctx, unit, after)
        if not a or not b:
            continue
        (ra, wa, ha), (rb, _, _) = a, b
        xs = [i % wa for i in range(wa * ha) if ra[i * 4:i * 4 + 4] != rb[i * 4:i * 4 + 4]]
        ys = [i // wa for i in range(wa * ha) if ra[i * 4:i * 4 + 4] != rb[i * 4:i * 4 + 4]]
        if not xs:
            continue
        box = (max(0, min(xs) - 2), max(0, min(ys) - 2), min(wa, max(xs) + 3), min(ha, max(ys) + 3))
        crops = [Image.frombytes("RGBA", (wa, ha), bytes(px)).crop(box) for px in (ra, rb)]
        out.append((unit, crops))
    sheet = Path(sheet)
    sheet.mkdir(parents=True, exist_ok=True)
    paths = []
    for label, bg in (("dark", (40, 40, 48)), ("light", (230, 230, 235))):
        scale = 4
        width = max(1, sum(c[0].width * scale * 2 + 12 for _, c in out[:6]))
        height = max(1, max([c[0].height * scale for _, c in out] + [1]) * 2 + 24)
        img = Image.new("RGB", (min(width, 4000), height), bg)
        x = 4
        for _, crops in out[:6]:
            for row, c in enumerate(crops):
                big = c.resize((c.width * scale, c.height * scale), Image.NEAREST)
                img.paste(big, (x, 4 + row * (height // 2)), big)
            x += crops[0].width * scale + 12
        path = sheet / ("%s_%s.png" % (ctx.appid, label))
        img.save(path)
        paths.append(str(path))
    strip = Image.new("RGB", (max(1, sum(c[0].width * 2 + 6 for _, c in out)), max([c[0].height for _, c in out] + [1]) * 2 + 8), (40, 40, 48))
    x = 2
    for _, crops in out:
        for row, c in enumerate(crops):
            strip.paste(c, (x, 2 + row * (strip.height // 2)), c)
        x += c.width + 6
    path = sheet / ("%s_native.png" % ctx.appid)
    strip.save(path)
    paths.append(str(path))
    ctx.drop(w)
    chk.note("sheets: " + ", ".join(paths))
    chk.data = {"sheets": paths}
    return chk, "%d sheets" % len(paths)


def probe_catalog(ctx):
    chk = Check()
    p = subprocess.run([sys.executable, str(TOOLS / "build_button_catalog.py"), "build", "--check"],
                       capture_output=True, text=True, timeout=300)
    tail = (p.stdout + p.stderr).strip().splitlines()[-1:] or [""]
    chk.ok(p.returncode == 0, "build --check failed: %s" % tail[0][:120])
    return chk, "build --check " + ("ok" if p.returncode == 0 else "failed")


FUNCS = {"pinned": probe_pinned, "cycle": probe_cycle, "revision-upgrade": probe_revision_upgrade,
         "running": probe_running, "steam-verify": probe_steam_verify, "new-build": probe_new_build,
         "unknown-build": probe_unknown_build, "assets": probe_assets, "crash": probe_crash,
         "symlinks": probe_symlinks, "ledger": probe_ledger, "timings": probe_timings,
         "visual": probe_visual, "catalog": probe_catalog}


# ----------------------------------------------------------------------------------------- driver


def make_args(**kw):
    ns = argparse.Namespace(styles=None, json=None, only=None, keep=False, quick=False, sheet=None,
                            archive_size=None, buildid="0", installdir="Game \u00dcnicode", profiles=None,
                            profiles_arg=None, no_render_cache=False, timings_n=None)
    for k, v in kw.items():
        setattr(ns, k, v)
    return ns


def run(appid, src_dir, profiles=None, args=None, log=print):
    """Run the probes; returns the report dict ({ok, probes: [...], ...}). Never writes outside a temp folder."""
    args = args or make_args()
    profiles = profiles or clone_validate.PROFILES
    report = {"appid": appid, "ok": False, "probes": [], "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
              "kept": None}
    if appid not in profiles:
        report["error"] = "appid %s has no registered profile" % appid
        return report
    work = vw.new_tempdir()
    report["kept"] = str(work) if args.keep else None
    try:
        try:
            ctx = Ctx(appid, src_dir, profiles, args, work)
        except vw.SourceError as exc:
            report["error"] = str(exc)
            return report
        report.update(kind=ctx.kind.name, name=ctx.raw_profile.name, revision=ctx.raw_profile.revision,
                      styles=ctx.styles)
        wanted = [p for p in PROBES if not args.only or p in args.only]
        unknown = [p for p in (args.only or []) if p not in PROBES]
        if unknown:
            report["error"] = "unknown probe(s): %s (known: %s)" % (", ".join(unknown), ", ".join(PROBES))
            return report
        stop = None
        for name in wanted:
            entry = {"name": name, "status": PASS, "number": "", "seconds": 0.0, "details": [], "data": {}}
            t0 = time.perf_counter()
            if stop:
                entry.update(status=SKIP, number="", details=[stop])
            else:
                try:
                    chk, number = FUNCS[name](ctx)
                    entry["number"] = number
                    entry["details"] = chk.fails[:8] + ["note: " + n for n in chk.notes[:8]]
                    entry["data"] = getattr(chk, "data", {})
                    if chk.fails:
                        entry["status"] = FAIL
                except Skip as exc:
                    entry.update(status=SKIP, details=[str(exc)])
                except Exception as exc:                      # a probe that crashes is a failed probe, never a pass
                    entry.update(status=FAIL, number="error",
                                 details=["%s: %s" % (type(exc).__name__, exc)] + traceback.format_exc().strip().splitlines()[-3:])
                finally:
                    ctx.drop_all()
            entry["seconds"] = round(time.perf_counter() - t0, 2)
            if name == "pinned" and entry["status"] == FAIL:
                stop = "skipped: the copies do not match the pinned table"
            report["probes"].append(entry)
            log("%-17s %-5s %-34s %6.1fs" % (name, entry["status"], entry["number"][:34], entry["seconds"]))
            for d in entry["details"][:6] if entry["status"] != PASS else []:
                log("    - " + d)
        report["ok"] = all(e["status"] != FAIL for e in report["probes"]) and any(e["status"] == PASS for e in report["probes"])
    finally:
        if not args.keep:
            shutil.rmtree(work, ignore_errors=True)
    return report


def main(argv=None):
    ap = argparse.ArgumentParser(description="Verify a profile on copies of the real files (see the module docstring).")
    ap.add_argument("appid")
    ap.add_argument("src_dir")
    ap.add_argument("--styles", help="comma separated, default: the profile's styles")
    ap.add_argument("--json", help="write the report here")
    ap.add_argument("--only", help="comma separated probe names: " + ",".join(PROBES))
    ap.add_argument("--keep", action="store_true", help="keep the temp folder and print its path")
    ap.add_argument("--quick", action="store_true", help="fewer crash points, one timing sample")
    ap.add_argument("--sheet", help="folder for the contact sheets (needs Pillow)")
    ap.add_argument("--archive-size", type=int, help="length of the sparse stand-in archive (regions)")
    ap.add_argument("--buildid", default="0")
    ap.add_argument("--installdir", default="Game \u00dcnicode")
    ap.add_argument("--profiles", help="module:function returning {appid: profile} (tests use it)")
    ap.add_argument("--profiles-arg", help="argument for that function")
    ap.add_argument("--no-render-cache", action="store_true", help="never reuse a render (slow)")
    ap.add_argument("--timings-n", type=int)
    ns = ap.parse_args(argv)
    ns.styles = ns.styles.split(",") if ns.styles else None
    ns.only = ns.only.split(",") if ns.only else None
    profiles = vw.load_profiles(ns.profiles, ns.profiles_arg)
    report = run(ns.appid, ns.src_dir, profiles, ns)
    if ns.json:
        with open(ns.json, "w", encoding="utf-8") as f:
            json.dump(report, f, indent=2, sort_keys=True, ensure_ascii=False)
            f.write("\n")
    if report.get("error"):
        print("ERROR:", report["error"], file=sys.stderr)
    fails = [p["name"] for p in report["probes"] if p["status"] == FAIL]
    print("RESULT:", "PASS" if report["ok"] else "FAIL", "(%d probes, %d failed%s)" % (
        len(report["probes"]), len(fails), ": " + ", ".join(fails) if fails else ""))
    if report.get("kept"):
        print("work folder kept at", report["kept"])
    return 0 if report["ok"] else (2 if report.get("error") else 1)


if __name__ == "__main__":
    sys.exit(main())
