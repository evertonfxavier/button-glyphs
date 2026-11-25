"""Serious Sam HD: The Second Encounter (Croteam, Serious Engine 3.5) - Steam appid 41010, build 5821931.

Add-file overlay (see serious_hd.py for the whole story, the signed-archive uncertainty and the placement rules).
Replaces A / B / X / Y, START, BACK (SHARE), the shoulders (L1 / R1), the triggers (L2 / R2) and the stick-click shapes
(L3 / R3, unconfirmed in game): the 32x32 ovals, the 64x64 XBox* textures (BACK and the right trigger exist twice, under
`SeriousSamHD` and `SeriousSamHD_TSE`: both are replaced), the 14 baked cells of `Textures/GlobalAtlas.rsc` (512x512 first
block; the Thumbstick* cells and everything else stay identical) and the controls-screen pad picture.
`VEHICLE` is the one line to change ("gro" = our archive, "loose" = loose files) if the game ignores the extra archive.
"""
from .serious_hd import make_profile

APPID = "41010"
VEHICLE = "gro"

PROFILE = make_profile(APPID, vehicle=VEHICLE)
