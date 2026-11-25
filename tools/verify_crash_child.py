#!/usr/bin/env python3
"""The child process of the `crash` probe of tools/verify_profile.py: it dies on purpose.

    verify_crash_child.py '<json spec>'

It builds a real Engine over an existing fake world and asks it for one style while a CrashInjector
kills the process with os._exit(9) at the chosen write event (a torn write leaves half of the bytes
behind). Nothing is cleaned up, like a power cut. The parent then looks at what is left on disk.
Exit status 9 = it died as planned; 0 = it finished without reaching that write event.
"""
import json
import os
import sys
from pathlib import Path

TOOLS = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOLS))


def main(argv):
    spec = json.loads(argv[1])
    for path in spec.get("sys_path", []):
        sys.path.insert(0, path)
    import verify_worlds as vw
    from button_glyphs import engine
    profile = vw.load_profiles(spec.get("factory"), spec.get("arg"))[spec["appid"]]
    if spec.get("vehicle") and getattr(profile, "overlay_level", False) and profile.vehicle != spec["vehicle"]:
        import copy
        profile = copy.copy(profile)
        profile.vehicle = spec["vehicle"]
        profile._memo = {}
    if spec.get("memo_file"):                     # fast mode: the parent already built the overlay; replay only the writes
        import pickle
        with open(spec["memo_file"], "rb") as f:
            memo = pickle.load(f)
        vw.FAST_BUILD[0] = True
        vw.HARNESS_MEMO["outputs"].update(memo["outputs"])
        vw.HARNESS_MEMO["preflight"].update(memo["preflight"])
        profile = vw.CachedProfile(profile)
    root = spec["root"]
    eng = engine.Engine(root, os.path.join(root, "settings"), os.path.join(root, "runtime"), str(vw.GLYPHS),
                        profiles={spec["appid"]: profile}, proc_root=os.path.join(root, "proc"))
    with vw.CrashInjector(spec["roots"], k=spec["k"], hard=True):
        eng.set_style(spec["appid"], spec["style"])
    print("child finished without dying")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
