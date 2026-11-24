"""Charlie Murder (Ska Studios, Linux/FNA port by flibitijibibo) - Steam appid 405290.

The port draws Xbox prompts by default and PlayStation prompts (the game's own art:
✕ ○ □ △, L1/R1/L2/R2/L3/R3, OPTIONS/SHARE, red D-pad) when `flibitPort.UpdateInput`
sees a DualShock 4 GUID ("4c05c405" / "4c05cc09"). Both sets already exist in
gfx/attribs.xnb and the font tables, so nothing is redrawn: we only change the pad
type that UpdateInput stores when a controller connects.

    IL_006a  call GamePad::GetGUIDEXT ; stloc.1
    ...      ldloc.1 ; ldstr "4c05c405" ; callvirt String::Equals ; brtrue.s A
             ldloc.1 ; ldstr "4c05cc09" ; callvirt String::Equals ; brfalse.s B
    A:       ldsfld gamepadTypes ; ldloc.0 ; ldc.i4.1 ; stelem.i4 ; br.s END   (DS4  -> 1 = PS)
    B:       ldsfld gamepadTypes ; ldloc.0 ; ldc.i4.0 ; stelem.i4 ; br.s END   (else -> 0 = Xbox)

PlayStation: B stores 1 as well.  Xbox: A stores 0 as well.  Original: untouched, so the
game keeps choosing by GUID.  Everything the player sees (button text, HUD, tutorials,
attribute screen) goes through `GetButtonIdx` / `DrawButtonAttrib`, which read this table.
"""
from .il_patch import IlPatchProfile

# callvirt Equals ; brfalse.s | ldsfld T ; ldloc.0 ; ldc.i4.1 ; stelem.i4 ; br.s | ldsfld T ; ldloc.0 ; ldc.i4.0 ; stelem.i4
PATTERN = (
    rb"\x6f....\x2c."
    rb"\x7e(....)\x06\x17\x9e\x2b."
    rb"\x7e\1\x06\x16\x9e"
)
# offsets inside the match: 7 bytes of prefix, then 7e+token(5) 06 [17] 9e 2b xx, 7e+token(5) 06 [16] 9e
DS4_STORES = 13         # `ldc.i4.1` in the DualShock branch
OTHER_STORES = 23       # `ldc.i4.0` in the fallback branch
LDC_I4_0, LDC_I4_1 = 0x16, 0x17

PROFILE = IlPatchProfile(
    appid="405290",
    support="full",
    name="Charlie Murder",
    file="CharlieMurder.exe",
    revision=1,
    roles=[
        ("y", "Y / △ (top)"),
        ("x", "X / □ (left)"),
        ("b", "B / ○ (right)"),
        ("a", "A / ✕ (bottom)"),
    ],
    pattern=PATTERN,
    anchors=["4c05cc09".encode("utf-16le"), "4c05c405".encode("utf-16le")],
    edits={
        "ps": {OTHER_STORES: LDC_I4_1},
        "xbox": {DS4_STORES: LDC_I4_0},
    },
    original_hint=("Automatic: the game shows PlayStation prompts only for a DualShock 4 "
                   "and Xbox prompts for any other controller, including the Deck."),
    notes="Patches CharlieMurder.exe (1 byte); the original is backed up first.",
)
