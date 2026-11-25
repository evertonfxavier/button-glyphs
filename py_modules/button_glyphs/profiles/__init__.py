"""Registry of supported games, keyed by Steam appid."""
from .ac1 import PROFILE as AC1
from .charlie_murder import PROFILE as CHARLIE_MURDER
from .quantum_break import PROFILE as QUANTUM_BREAK
from .saint_seiya import PROFILE as SAINT_SEIYA
from .serious_sam_2 import PROFILE as SERIOUS_SAM_2
from .serious_sam_hd_tfe import PROFILE as SERIOUS_SAM_HD_TFE
from .serious_sam_hd_tse import PROFILE as SERIOUS_SAM_HD_TSE
from .songs_for_a_hero import PROFILE as SONGS_FOR_A_HERO
from .ten_second_ninja_x import PROFILE as TEN_SECOND_NINJA_X

PROFILES = {p.appid: p for p in (AC1, CHARLIE_MURDER, QUANTUM_BREAK, SAINT_SEIYA,
                                 SERIOUS_SAM_2, SERIOUS_SAM_HD_TFE, SERIOUS_SAM_HD_TSE, SONGS_FOR_A_HERO,
                                 TEN_SECOND_NINJA_X)}
