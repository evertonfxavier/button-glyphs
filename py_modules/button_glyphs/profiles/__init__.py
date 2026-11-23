"""Registry of supported games, keyed by Steam appid."""
from .ac1 import PROFILE as AC1
from .charlie_murder import PROFILE as CHARLIE_MURDER

PROFILES = {p.appid: p for p in (AC1, CHARLIE_MURDER)}
