"""Serious Sam HD: The First Encounter (Croteam, Serious Engine 3.5) - Steam appid 41000, build 5820849.

Add-file overlay (see serious_hd.py for the whole story, the signed-archive uncertainty and the placement rules).
Replaces A / B / X / Y: the 32x32 ovals, the four 64x64 XBoxButton discs, the same four baked cells in
`Textures/GlobalAtlas.rsc` (256x128 block) and the controls-screen pad picture `GamepadPerspective.tex`.
`VEHICLE` is the one line to change ("gro" = our archive, "loose" = loose files) if the game ignores the extra archive.
"""
from .serious_hd import make_profile

APPID = "41000"
VEHICLE = "gro"

PROFILE = make_profile(APPID, vehicle=VEHICLE)
