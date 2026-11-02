"""
Player-name keys for matching the same player across data sources.

Exchanges and sportsbooks disagree on accents ("Dončić" vs "Doncic"),
punctuation ("De'Aaron"), and generational suffixes ("Jr.").
"""

from __future__ import annotations

import re
import unicodedata

_SUFFIXES = {"jr", "sr", "ii", "iii", "iv", "v"}


def _ascii_tokens(name: str) -> list[str]:
    folded = unicodedata.normalize("NFKD", name or "")
    folded = "".join(ch for ch in folded if not unicodedata.combining(ch))
    folded = folded.lower().replace("'", "").replace("\u2019", "").replace(".", "")
    tokens = [t for t in re.split(r"[^a-z0-9]+", folded) if t]
    while len(tokens) > 2 and tokens[-1] in _SUFFIXES:
        tokens.pop()
    return tokens


def player_key(name: str) -> str:
    """Stable underscore slug, e.g. 'Luka Dončić' -> 'luka_doncic'."""
    return "_".join(_ascii_tokens(name))


def compact_name(name: str) -> str:
    """Separator-free key, e.g. 'Jaren Jackson Jr.' -> 'jarenjackson'."""
    return "".join(_ascii_tokens(name))
