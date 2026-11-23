"""Registry of supported games, keyed by Steam appid."""
from .ac1 import PROFILE as AC1

PROFILES = {p.appid: p for p in (AC1,)}
