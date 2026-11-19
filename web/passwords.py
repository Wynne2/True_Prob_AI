"""
Password blocklist (NIST SP 800-63B-4, 3.1.1.2): reject commonly used and context-specific passwords.

Deliberately small. Online guessing is already rate limited, so this only has to catch what an
attacker tries first: the most common 10+ character passwords, a word from them with digits or
symbols on the end, the site's name and the account's own email.
"""

from __future__ import annotations

import re

MIN_LENGTH, MAX_LENGTH = 10, 200

# Common passwords of 10+ characters, as people type them.
COMMON = frozenset("""
1234567890 0123456789 0987654321 1234567891 12345678910 1122334455 1111111111 0000000000 123123123123
1q2w3e4r5t 1qaz2wsx3edc zaq12wsxcde qwertyuiop asdfghjkl1 qwerty1234 qwerty12345 qwerty123456 qwertyuiop1
abcdefghij abcd123456 abc1234567 a1b2c3d4e5 123456789a 123456789q passw0rd123 p@ssw0rd123 iloveyou12
""".split())

# Words that are weak with digits or symbols on the end ("basketball23!", "password2026").
WORDS = frozenset("""
password passwords passw0rd p@ssw0rd letmein welcome iloveyou sunshine princess football basketball baseball
superman batman starwars whatever trustno1 monkey dragon master shadow qwerty qwertyuiop changeme secret
lebronjames michaeljordan kobebryant stephcurry lakers celtics warriors knicks bulls nba sportsbook
gambling betting parlay
""".split())

SITE = ("trueprob", "trueprobai", "true prob", "true prob ai", "true_prob_ai", "true-prob-ai")


def _bare(text: str) -> str:
    """Lower case without the digits, symbols and spaces people add to the end of a word."""
    return re.sub(r"[\d\W_]+$", "", text.strip().lower())


def problem(password: str, email: str = "") -> str | None:
    """Why this password is not accepted, in words for the form; None when it is fine."""
    if not MIN_LENGTH <= len(password) <= MAX_LENGTH:
        return f"Use a password of at least {MIN_LENGTH} characters."
    low, bare = password.strip().lower(), _bare(password)
    email = email.strip().lower()
    context = {email, email.split("@")[0], *SITE} - {""}
    if (low in COMMON or bare in WORDS or bare in COMMON or low in context or bare in context
            or len(set(low)) <= 2):
        return ("That password is on our list of common or easy-to-guess passwords (or is your email or our name). "
                "Choose another; three or four unrelated words make a strong one.")
    return None
