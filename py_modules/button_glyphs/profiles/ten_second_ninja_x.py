"""10 Second Ninja X (GameMaker Studio 1.4, Windows build under Proton) - Steam appid 435790.

The game already ships PlayStation 4, Xbox 360, Xbox One and Vita button sprites in data.win
(523 spr_XboxButtons, 524 spr_PS4Buttons, 525 spr_XboneButtons, 526 Vita). It keeps the
sprite index it draws as `global.buttons` and persists it in the save, in the INI section
[storyfile]:

    buttons="523.000000"

There is no pad detection: the default on PC is 523 (Xbox 360) and a stored value wins over
the default. This profile writes 524 (PlayStation) or 523 (Xbox) into that line. The save
lives in the Proton prefix, not in the game folder, and the prefix may be in another Steam
library than the game. The indices belong to one build, so that build is pinned by the size
and SHA-256 of data.win.
"""
from .config_value import ConfigValueProfile

PROFILE = ConfigValueProfile(
    appid="435790",
    name="10 Second Ninja X",
    save="drive_c/users/steamuser/AppData/Local/_10_Second_Ninja_X_Test/asyncsavegroup/10SNX.sav",
    section="storyfile",
    key="buttons",
    values={"ps": "524.000000", "xbox": "523.000000"},
    value_pattern=r"[0-9]{3}\.[0-9]{6}",
    builds={
        "1638978": {
            "file": "data.win",
            "size": 140512524,
            "sha256": "cf7fdc3320da683f8d189104c3888fb098832c7edabad51f29a7ba2e19a5a008",
        },
    },
    revision=1,
    roles=[
        ("y", "Y / △ (top)"),
        ("x", "X / □ (left)"),
        ("b", "B / ○ (right)"),
        ("a", "A / ✕ (bottom)"),
    ],
    original_hint=("The game's own setting: Xbox 360 buttons unless the player changed the "
                   "Button UI option in the game. While PlayStation or Xbox is chosen here, that "
                   "in-game option is reset to the chosen style when the game is closed."),
    notes=("Edits one value of 10SNX.sav (the save inside the Proton prefix); the first time, a "
           "copy of the whole save is kept. If the in-game option is changed while PlayStation "
           "or Xbox is chosen here, the value is put back when the game is closed."),
)
