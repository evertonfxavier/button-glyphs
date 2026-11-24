#!/usr/bin/env python3
"""Feeds the project's memory: CLAUDE.md (generic lessons) and docs/games/<appid>-*.md (per game).

    learn.py add -c formats -s generic -w AC1 "Scimitar format code 4 is DXT3, not DXT5."
    learn.py add -c verification -s game --appid 15100 "Back texture is a plain disc; use unknown."
    learn.py list                    show the lessons block
    learn.py check                   validate format and size budget (exit 1 on problems)
    learn.py none --note "why"       record "retrospective done, nothing new to learn"
    learn.py report                  sizes and near-duplicates, to help consolidate
    learn.py replace --match "text" "new lesson"   rewrite one lesson (consolidation)
    learn.py remove  --match "text"                delete one lesson (obsolete)

Generic lessons live between the LESSONS markers of CLAUDE.md (the workspace-level guide: looked up in
the project folder first, then one level up), one bullet each, under a fixed
category heading. They are rules of thumb that held for more than one game or that cost real
time once. Facts about one game belong to its docs/games page ("--scope game"). The block has
a size budget: past SOFT_LIMIT `add` warns, at HARD_LIMIT it refuses until the block is
consolidated (agent `retro-curator`).
"""
import argparse
import datetime
import glob
import os
import re
import sys
from pathlib import Path

START = "<!-- LESSONS:START"
END = "<!-- LESSONS:END -->"
CATEGORIES = [
    ("process", "Process & collaboration"),
    ("strategies", "Choosing & building strategies"),
    ("formats", "Formats & reverse engineering"),
    ("verification", "Verification & testing"),
    ("deck", "Steam Deck operations"),
    ("design", "Design & safety"),
    ("research", "Research & sources"),
]
TITLE = dict(CATEGORIES)
SOFT_LIMIT = 45      # bullets: warn
HARD_LIMIT = 60      # bullets: refuse
MAX_TEXT = 320
DUPLICATE = 0.70     # Jaccard similarity of words that makes a new lesson a duplicate
NEAR = 0.50          # shown by `report`


class LearnError(Exception):
    pass


def words(text):
    return {w for w in re.findall(r"[a-z0-9_]{4,}", text.lower())}


def strip_tag(line):
    """The lesson text of a bullet, without the leading '- ' and the trailing '_(source, date)_'."""
    return re.sub(r"\s*_\([^)]*\)_$", "", line[2:] if line.startswith("- ") else line)


def similarity(a, b):
    wa, wb = words(a), words(b)
    return len(wa & wb) / len(wa | wb) if wa and wb else 0.0


class Lessons:
    def __init__(self, root, state_dir=None):
        self.root = Path(root).resolve()  # a relative root made `.parent` meaningless
        self.claude = self._find_claude()
        if state_dir:
            self.state = Path(state_dir)
        else:
            workspace_state = self.root.parent / ".claude" / ".state"
            self.state = workspace_state if workspace_state.parent.is_dir() else self.root / ".claude" / ".state"

    def _find_claude(self):
        """The guide file holding the lessons block: in the project folder if it has one, otherwise the
        CLAUDE.md of the enclosing workspace (the normal case: the guide lives at workspace scope)."""
        for candidate in (self.root / "CLAUDE.md", self.root.parent / "CLAUDE.md"):
            try:
                if START in candidate.read_text():
                    return candidate
            except OSError:
                pass
        return self.root / "CLAUDE.md"

    # ------------------------------------------------------------------ parsing

    def _split(self):
        if not self.claude.exists():
            raise LearnError("%s not found" % self.claude)
        text = self.claude.read_text()
        a = text.find(START)
        b = text.find(END)
        if a < 0 or b < 0 or b < a:
            raise LearnError("CLAUDE.md has no LESSONS:START ... LESSONS:END block")
        head_end = text.index("\n", a) + 1
        return text[:head_end], text[head_end:b], text[b:]

    def parse(self):
        """{category key: [bullet lines]} from the block, plus lines that did not parse."""
        _, body, _ = self._split()
        data = {key: [] for key, _ in CATEGORIES}
        stray = []
        current = None
        by_title = {title: key for key, title in CATEGORIES}
        for line in body.splitlines():
            if not line.strip():
                continue
            if line.startswith("### "):
                current = by_title.get(line[4:].strip())
                if current is None:
                    stray.append(line)
            elif line.startswith("- ") and current:
                data[current].append(line)
            else:
                stray.append(line)
        return data, stray

    def _write(self, data):
        head, _, tail = self._split()
        out = [""]
        for key, title in CATEGORIES:
            if data[key]:
                out += ["### " + title, *data[key], ""]
        self.claude.write_text(head + "\n".join(out) + tail)

    def count(self):
        data, _ = self.parse()
        return sum(len(v) for v in data.values())

    # ------------------------------------------------------------------ actions

    def add(self, text, category, scope="generic", source="", appid=None, force=False):
        text = " ".join(text.split())
        if not text:
            raise LearnError("empty lesson")
        if len(text) > MAX_TEXT:
            raise LearnError("lesson is %d characters; keep it under %d (one idea, one line). Put "
                             "the long version in docs/games/ and link it." % (len(text), MAX_TEXT))
        if category not in TITLE:
            raise LearnError("unknown category %r; use one of: %s" % (category, ", ".join(TITLE)))
        today = datetime.date.today().isoformat()
        tag = ("%s, %s" % (source, today)) if source else today
        bullet = "- %s _(%s)_" % (text, tag)

        if scope == "game":
            return self._add_game(text, bullet, appid)

        data, stray = self.parse()
        if stray and not force:
            raise LearnError("the lessons block has lines that do not follow the format (run "
                             "`learn.py check`): %s" % stray[0][:80])
        for line in (l for v in data.values() for l in v):
            if similarity(text, strip_tag(line)) >= DUPLICATE:
                self._stamp("duplicate: " + text[:60])
                return "Already known (not added): " + line
        total = sum(len(v) for v in data.values())
        if total >= HARD_LIMIT and not force:
            raise LearnError("the lessons block has %d bullets (limit %d). Consolidate first: run "
                             "the agent `retro-curator` (or /learn consolidate)." % (total, HARD_LIMIT))
        data[category].append(bullet)
        self._write(data)
        self._stamp("added [%s] %s" % (category, text[:60]))
        note = ""
        if total + 1 > SOFT_LIMIT:
            note = "\nNote: %d/%d bullets - consolidate soon (/learn consolidate)." % (total + 1, HARD_LIMIT)
        return "Added to CLAUDE.md [%s]: %s%s" % (category, bullet, note)

    def _add_game(self, text, bullet, appid):
        if not appid:
            raise LearnError("--scope game needs --appid")
        pages = sorted(glob.glob(str(self.root / "docs" / "games" / ("%s-*.md" % appid))))
        if not pages:
            raise LearnError("no docs/games/%s-*.md page; create it first (tools/scaffold_profile.py "
                             "or docs/games/_template.md)" % appid)
        page = Path(pages[0])
        lines = page.read_text().rstrip("\n").split("\n")
        if "## Learnings" not in lines:
            lines += ["", "## Learnings", ""]
        else:
            existing = lines[lines.index("## Learnings") + 1:]
            for line in existing:
                if line.startswith("- ") and similarity(text, strip_tag(line)) >= DUPLICATE:
                    self._stamp("duplicate (game): " + text[:60])
                    return "Already in %s (not added): %s" % (page.name, line)
        # keep the section last: append at the end of the file
        while lines and lines[-1] == "":
            lines.pop()
        lines.append(bullet)
        page.write_text("\n".join(lines) + "\n")
        self._stamp("added game %s: %s" % (appid, text[:60]))
        return "Added to %s: %s" % (page.name, bullet)

    def _find_one(self, data, match):
        needle = match.lower()
        hits = [(k, l) for k, v in data.items() for l in v if needle in l.lower()]
        if not hits:
            raise LearnError("no lesson contains %r" % match)
        if len(hits) > 1:
            raise LearnError("%r matches %d lessons, be more specific:\n  %s" % (
                match, len(hits), "\n  ".join(h[1][:90] for h in hits)))
        return hits[0]

    def replace(self, match, text, category=None, source="consolidated"):
        """Rewrite the single lesson containing `match` (optionally moving it to another category)."""
        text = " ".join(text.split())
        if not text or len(text) > MAX_TEXT:
            raise LearnError("new text must be 1-%d characters" % MAX_TEXT)
        data, _ = self.parse()
        key, line = self._find_one(data, match)
        if category and category not in TITLE:
            raise LearnError("unknown category %r" % category)
        data[key].remove(line)
        data[category or key].append("- %s _(%s, %s)_" % (text, source, datetime.date.today().isoformat()))
        self._write(data)
        self._stamp("replaced: " + text[:60])
        return "Replaced: %s" % line[:100]

    def remove(self, match):
        data, _ = self.parse()
        key, line = self._find_one(data, match)
        data[key].remove(line)
        self._write(data)
        self._stamp("removed: " + line[:60])
        return "Removed: %s" % line[:100]

    def check(self):
        problems = []
        try:
            data, stray = self.parse()
        except LearnError as exc:
            return [str(exc)], 0
        problems += ["not a bullet under a known heading: %s" % s[:70] for s in stray]
        total = sum(len(v) for v in data.values())
        if total > HARD_LIMIT:
            problems.append("%d bullets, over the limit of %d - consolidate" % (total, HARD_LIMIT))
        for key, lines in data.items():
            for line in lines:
                if len(line) > MAX_TEXT + 60:
                    problems.append("too long (%d chars): %s..." % (len(line), line[:60]))
        return problems, total

    def report(self):
        data, stray = self.parse()
        lines = ["bullets per category:"]
        for key, title in CATEGORIES:
            lines.append("  %-13s %2d  %s" % (key, len(data[key]), title))
        flat = [(k, l) for k, v in data.items() for l in v]
        pairs = []
        for i in range(len(flat)):
            for j in range(i + 1, len(flat)):
                s = similarity(strip_tag(flat[i][1]), strip_tag(flat[j][1]))
                if s >= NEAR:
                    pairs.append((s, flat[i][1], flat[j][1]))
        lines.append("total %d / limit %d (soft %d)" % (len(flat), HARD_LIMIT, SOFT_LIMIT))
        if pairs:
            lines.append("near-duplicates (merge candidates):")
            for s, a, b in sorted(pairs, reverse=True):
                lines += ["  %.2f  %s" % (s, a[:100]), "        %s" % b[:100]]
        return "\n".join(lines)

    # ------------------------------------------------------------------ retrospective stamp

    def _stamp(self, note):
        self.state.mkdir(parents=True, exist_ok=True)
        (self.state / "last_retro").touch()
        with open(self.state / "retro.log", "a") as f:
            f.write("%s %s\n" % (datetime.datetime.now().isoformat(timespec="seconds"), note))

    def none(self, note):
        self._stamp("nothing new: " + (note or "-"))
        return "Retrospective recorded: nothing new to learn."


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    sub = ap.add_subparsers(dest="cmd", required=True)
    add = sub.add_parser("add")
    add.add_argument("text")
    add.add_argument("-c", "--category", default="process", choices=[k for k, _ in CATEGORIES])
    add.add_argument("-s", "--scope", default="generic", choices=["generic", "game"])
    add.add_argument("-w", "--source", default="", help="where it was learned (game or task)")
    add.add_argument("--appid")
    add.add_argument("--force", action="store_true")
    rep = sub.add_parser("replace")
    rep.add_argument("text")
    rep.add_argument("--match", required=True)
    rep.add_argument("-c", "--category", choices=[k for k, _ in CATEGORIES])
    rep.add_argument("-w", "--source", default="consolidated")
    rem = sub.add_parser("remove")
    rem.add_argument("--match", required=True)
    sub.add_parser("list")
    sub.add_parser("check")
    sub.add_parser("report")
    none = sub.add_parser("none")
    none.add_argument("--note", default="")
    args = ap.parse_args(argv)

    lessons = Lessons(args.root, os.environ.get("BG_STATE_DIR"))
    try:
        if args.cmd == "add":
            print(lessons.add(args.text, args.category, args.scope, args.source, args.appid, args.force))
        elif args.cmd == "replace":
            print(lessons.replace(args.match, args.text, args.category, args.source))
        elif args.cmd == "remove":
            print(lessons.remove(args.match))
        elif args.cmd == "list":
            data, _ = lessons.parse()
            for key, title in CATEGORIES:
                if data[key]:
                    print("### " + title)
                    print("\n".join(data[key]))
        elif args.cmd == "check":
            problems, total = lessons.check()
            print("%d lessons" % total)
            for p in problems:
                print("PROBLEM:", p)
            return 1 if problems else 0
        elif args.cmd == "report":
            print(lessons.report())
        elif args.cmd == "none":
            print(lessons.none(args.note))
    except LearnError as exc:
        print("error:", exc, file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
