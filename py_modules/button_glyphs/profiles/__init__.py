"""Registry of supported games, keyed by Steam appid."""
from .ac1 import PROFILE as AC1
from .charlie_murder import PROFILE as CHARLIE_MURDER
from .quantum_break import PROFILE as QUANTUM_BREAK
from .saint_seiya import PROFILE as SAINT_SEIYA
from .ten_second_ninja_x import PROFILE as TEN_SECOND_NINJA_X

PROFILES = {p.appid: p for p in (AC1, CHARLIE_MURDER, QUANTUM_BREAK, SAINT_SEIYA,
                                 TEN_SECOND_NINJA_X)}
