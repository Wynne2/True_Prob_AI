"""
SQLite storage for accounts, credits, billing state and audit logs.

Credits are SaaS usage units. They are not money: they cannot be bought back,
transferred, withdrawn or wagered. The balance is the sum of the ledger.

shortcut: SQLite on one disk, one web process. Move to Postgres when the app
needs a second web instance or more than a few thousand active users.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator, Optional

ROOT = Path(__file__).resolve().parents[1]

PLANS: dict[str, dict] = {
    "free": {"name": "Free", "price": 0, "credits": 20},
    "pro": {"name": "Pro", "price": 29, "credits": 200},
    "power": {"name": "Power", "price": 79, "credits": 1000},
}
# What one action costs. Opening something already unlocked is free.
COSTS = {"prop": 1, "advisor": 3, "board": 5, "arbitrage": 5}
ACTIVE_STATUSES = ("active", "trialing")
PERIOD_SECONDS = 30 * 86400  # free-plan credit period

SCHEMA = """
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY,
    email TEXT NOT NULL UNIQUE,
    pw_hash TEXT NOT NULL,
    created_at REAL NOT NULL,
    verified_at REAL,
    is_admin INTEGER NOT NULL DEFAULT 0,
    session_version INTEGER NOT NULL DEFAULT 0,
    region TEXT NOT NULL DEFAULT '',
    plan TEXT NOT NULL DEFAULT 'free',
    sub_status TEXT NOT NULL DEFAULT '',
    stripe_customer_id TEXT,
    stripe_subscription_id TEXT,
    period_end REAL,
    cancel_at_period_end INTEGER NOT NULL DEFAULT 0,
    credits_reset_at REAL NOT NULL,
    deleted_at REAL
);
CREATE TABLE IF NOT EXISTS tokens (
    token_hash TEXT PRIMARY KEY,
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL
);
CREATE TABLE IF NOT EXISTS ledger (
    id INTEGER PRIMARY KEY,
    user_id INTEGER NOT NULL,
    delta INTEGER NOT NULL,
    reason TEXT NOT NULL,
    ref TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS ledger_user ON ledger(user_id, id);
CREATE TABLE IF NOT EXISTS unlocks (
    user_id INTEGER NOT NULL,
    kind TEXT NOT NULL,
    ref TEXT NOT NULL,
    created_at REAL NOT NULL,
    PRIMARY KEY (user_id, kind, ref)
);
CREATE TABLE IF NOT EXISTS stripe_events (id TEXT PRIMARY KEY, type TEXT, received_at REAL);
CREATE TABLE IF NOT EXISTS audit (
    id INTEGER PRIMARY KEY, actor_id INTEGER, action TEXT NOT NULL, target TEXT, detail TEXT, at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS events (id INTEGER PRIMARY KEY, name TEXT NOT NULL, user_id INTEGER, at REAL NOT NULL);
CREATE INDEX IF NOT EXISTS events_name ON events(name, at);
CREATE TABLE IF NOT EXISTS flags (key TEXT PRIMARY KEY, value TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS announcements (id INTEGER PRIMARY KEY, body TEXT NOT NULL, created_at REAL NOT NULL, active INTEGER NOT NULL DEFAULT 1);
CREATE TABLE IF NOT EXISTS watchlist (
    user_id INTEGER NOT NULL, key TEXT NOT NULL, label TEXT NOT NULL, created_at REAL NOT NULL,
    PRIMARY KEY (user_id, key)
);
CREATE TABLE IF NOT EXISTS advisor_runs (
    id INTEGER PRIMARY KEY, user_id INTEGER NOT NULL, slate TEXT NOT NULL, status TEXT NOT NULL,
    body TEXT NOT NULL DEFAULT '', created_at REAL NOT NULL
);
"""


def db_path() -> Path:
    return Path(os.environ.get("TRUEPROB_DB") or ROOT / "data" / "app.db")


@contextmanager
def connect() -> Iterator[sqlite3.Connection]:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction() -> Iterator[sqlite3.Connection]:
    """One writer at a time (BEGIN IMMEDIATE), so a balance check and its deduction cannot interleave."""
    with connect() as conn:
        conn.execute("BEGIN IMMEDIATE")
        try:
            yield conn
            conn.execute("COMMIT")
        except BaseException:
            conn.execute("ROLLBACK")
            raise


def init() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)


# ---------------------------------------------------------------------------
# Passwords and one-time tokens
# ---------------------------------------------------------------------------

# OWASP's scrypt row with the same 16 MiB as before: N=2^14, r=8, p=5. Stored as scrypt$N$r$p$salt$digest.
SCRYPT_PARAMS = (2**14, 8, 5)
_LEGACY_PARAMS = (2**14, 8, 1)  # hashes written as scrypt$salt$digest


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    n, r, p = SCRYPT_PARAMS
    digest = hashlib.scrypt(password.encode(), salt=salt, n=n, r=r, p=p)
    return f"scrypt${n}${r}${p}${salt.hex()}${digest.hex()}"


def _parse_hash(stored: str) -> tuple[tuple[int, int, int], str, str]:
    parts = stored.split("$")
    if len(parts) == 3:
        return _LEGACY_PARAMS, parts[1], parts[2]
    _, n, r, p, salt, digest = parts
    params = (int(n), int(r), int(p))
    if params[0] > 2**15 or params[1] > 8 or params[2] > 16:  # a tampered row must not cost unbounded memory or time
        raise ValueError("scrypt parameters out of range")
    return params, salt, digest


def check_password(password: str, stored: str) -> bool:
    try:
        (n, r, p), salt, digest = _parse_hash(stored)
        got = hashlib.scrypt(password.encode(), salt=bytes.fromhex(salt), n=n, r=r, p=p)
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got.hex(), digest)


def needs_rehash(stored: str) -> bool:
    """True for a hash made with older parameters; it is replaced at the next successful sign-in."""
    try:
        return _parse_hash(stored)[0] != SCRYPT_PARAMS
    except (ValueError, TypeError):
        return False


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def make_token(user_id: int, kind: str, ttl: int) -> str:
    """Single-use token (email verification, password reset). Only its hash is stored."""
    token = secrets.token_urlsafe(32)
    with connect() as conn:
        conn.execute("INSERT INTO tokens VALUES (?,?,?,?,NULL)", (_token_hash(token), user_id, kind, time.time() + ttl))
    return token


def use_token(token: str, kind: str) -> Optional[int]:
    """Consume a token; returns its user id, or None when unknown, expired or already used."""
    with transaction() as conn:
        row = conn.execute("SELECT * FROM tokens WHERE token_hash=? AND kind=?", (_token_hash(token or ""), kind)).fetchone()
        if not row or row["used_at"] or row["expires_at"] < time.time():
            return None
        conn.execute("UPDATE tokens SET used_at=? WHERE token_hash=?", (time.time(), row["token_hash"]))
        return int(row["user_id"])


def void_tokens(user_id: int, kind: str) -> None:
    """Make every unused token of this kind for this user unusable."""
    with connect() as conn:
        conn.execute("UPDATE tokens SET used_at=? WHERE user_id=? AND kind=? AND used_at IS NULL", (time.time(), user_id, kind))


# ---------------------------------------------------------------------------
# Users
# ---------------------------------------------------------------------------

def admin_emails() -> set[str]:
    return {e.strip().lower() for e in (os.environ.get("ADMIN_EMAILS") or "").split(",") if e.strip()}


def create_user(email: str, password: str, region: str = "") -> Optional[int]:
    """New free account with its first credit grant. None when the email is taken."""
    email = email.strip().lower()
    now = time.time()
    try:
        with transaction() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, pw_hash, created_at, is_admin, region, credits_reset_at) VALUES (?,?,?,?,?,?)",
                (email, hash_password(password), now, int(email in admin_emails()), region[:40], now),
            )
            uid = int(cur.lastrowid)
            conn.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
                         (uid, PLANS["free"]["credits"], "Free plan credits", "signup", now))
            return uid
    except sqlite3.IntegrityError:
        return None


def get_user(user_id: Optional[int] = None, *, email: Optional[str] = None, customer: Optional[str] = None):
    col, val = ("id", user_id) if user_id is not None else ("email", (email or "").strip().lower()) if email is not None \
        else ("stripe_customer_id", customer)
    if val in (None, ""):
        return None
    with connect() as conn:
        return conn.execute(f"SELECT * FROM users WHERE {col}=? AND deleted_at IS NULL", (val,)).fetchone()


def update_user(user_id: int, **fields) -> None:
    allowed = {"pw_hash", "verified_at", "is_admin", "session_version", "plan", "sub_status", "stripe_customer_id",
               "stripe_subscription_id", "period_end", "cancel_at_period_end", "credits_reset_at", "region"}
    fields = {k: v for k, v in fields.items() if k in allowed}
    if fields:
        with connect() as conn:
            conn.execute(f"UPDATE users SET {', '.join(k + '=?' for k in fields)} WHERE id=?", (*fields.values(), user_id))


def effective_plan(user) -> str:
    """The plan whose entitlements apply now: a paid plan only counts while its subscription is active."""
    return user["plan"] if user["plan"] in PLANS and user["plan"] != "free" and user["sub_status"] in ACTIVE_STATUSES else "free"


def delete_user(user_id: int) -> None:
    """Erase the account: personal data and activity go; the row stays as an anonymous tombstone for ledgers."""
    with transaction() as conn:
        conn.execute(
            "UPDATE users SET email=?, pw_hash='', deleted_at=?, stripe_customer_id=NULL, stripe_subscription_id=NULL, "
            "region='', session_version=session_version+1 WHERE id=?",
            (f"deleted-{user_id}-{secrets.token_hex(4)}", time.time(), user_id),
        )
        for table in ("tokens", "watchlist", "advisor_runs", "unlocks"):
            conn.execute(f"DELETE FROM {table} WHERE user_id=?", (user_id,))
        conn.execute("UPDATE events SET user_id=NULL WHERE user_id=?", (user_id,))


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

def _balance(conn, user_id: int) -> int:
    return int(conn.execute("SELECT COALESCE(SUM(delta),0) FROM ledger WHERE user_id=?", (user_id,)).fetchone()[0])


def balance(user_id: int) -> int:
    with connect() as conn:
        return _balance(conn, user_id)


def is_unlocked(user_id: int, kind: str, ref: str) -> bool:
    with connect() as conn:
        return conn.execute("SELECT 1 FROM unlocks WHERE user_id=? AND kind=? AND ref=?", (user_id, kind, ref)).fetchone() is not None


def spend(user_id: int, kind: str, ref: str, label: str = "") -> str:
    """
    Pay for one thing once. Returns "ok" (charged), "already" (unlocked before, no charge)
    or "insufficient". The check, the unlock row and the ledger row are one transaction,
    so two simultaneous requests cannot both spend the same credits or double-charge.
    """
    cost = COSTS[kind]
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM unlocks WHERE user_id=? AND kind=? AND ref=?", (user_id, kind, ref)).fetchone():
            return "already"
        if _balance(conn, user_id) < cost:
            return "insufficient"
        now = time.time()
        conn.execute("INSERT INTO unlocks VALUES (?,?,?,?)", (user_id, kind, ref, now))
        conn.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
                     (user_id, -cost, label or kind, f"{kind}:{ref}", now))
        return "ok"


def refund(user_id: int, kind: str, ref: str, why: str) -> bool:
    """Give back a charge whose work failed. Does nothing unless that charge exists and was not refunded."""
    with transaction() as conn:
        if not conn.execute("DELETE FROM unlocks WHERE user_id=? AND kind=? AND ref=?", (user_id, kind, ref)).rowcount:
            return False
        conn.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
                     (user_id, COSTS[kind], f"Refund: {why}"[:200], f"refund:{kind}:{ref}", time.time()))
        return True


def grant_period(user_id: int, plan: str, ref: str, period_end: Optional[float] = None) -> bool:
    """
    Start a credit period: the balance becomes the plan's allowance (unused credits do not roll over).
    ``ref`` makes it idempotent: the same invoice or period is granted once.
    """
    credits = PLANS[plan]["credits"]
    with transaction() as conn:
        if conn.execute("SELECT 1 FROM ledger WHERE user_id=? AND ref=?", (user_id, f"grant:{ref}")).fetchone():
            return False
        now = time.time()
        conn.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
                     (user_id, credits - _balance(conn, user_id), f"{PLANS[plan]['name']} plan credits for the new period",
                      f"grant:{ref}", now))
        conn.execute("UPDATE users SET credits_reset_at=?, period_end=COALESCE(?, period_end) WHERE id=?",
                     (now, period_end, user_id))
        return True


def refresh_free_credits(user) -> None:
    """Free accounts get a new allowance every 30 days, on their next visit after it is due."""
    if effective_plan(user) == "free" and time.time() - float(user["credits_reset_at"]) >= PERIOD_SECONDS:
        period = int((time.time() - float(user["created_at"])) // PERIOD_SECONDS)
        grant_period(user["id"], "free", f"free:{user['id']}:{period}")


def next_reset(user) -> Optional[tuple[float, int]]:
    """(when, credits): the next time the balance is set to an allowance, or None when that date is not known yet."""
    if effective_plan(user) != "free":
        if not user["period_end"]:
            return None
        plan = "free" if user["cancel_at_period_end"] else user["plan"]
        return float(user["period_end"]), PLANS[plan]["credits"]
    return float(user["credits_reset_at"]) + PERIOD_SECONDS, PLANS["free"]["credits"]


def adjust_credits(admin_id: int, user_id: int, delta: int, note: str) -> None:
    with transaction() as conn:
        now = time.time()
        conn.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
                     (user_id, delta, f"Adjustment by support: {note}"[:200], f"admin:{admin_id}", now))
        conn.execute("INSERT INTO audit (actor_id, action, target, detail, at) VALUES (?,?,?,?,?)",
                     (admin_id, "credits.adjust", str(user_id), json.dumps({"delta": delta, "note": note}), now))


def period_usage(user) -> dict[str, dict]:
    """What this period's credits went on: per kind, how many charges, credits spent and credits refunded."""
    out = {k: {"times": 0, "spent": 0, "refunded": 0} for k in COSTS}
    with connect() as conn:
        rows = conn.execute("SELECT delta, ref FROM ledger WHERE user_id=? AND created_at>=?",
                            (user["id"], float(user["credits_reset_at"]))).fetchall()
    for delta, ref in rows:
        kind, _, rest = ref.partition(":")
        if delta < 0 and kind in out:
            out[kind]["times"] += 1
            out[kind]["spent"] -= delta
        elif kind == "refund" and rest.split(":")[0] in out:
            out[rest.split(":")[0]]["refunded"] += delta
    return {k: v for k, v in out.items() if v["times"]}


def history(user_id: int, limit: int = 50) -> list:
    with connect() as conn:
        return conn.execute("SELECT * FROM ledger WHERE user_id=? ORDER BY id DESC LIMIT ?", (user_id, limit)).fetchall()


# ---------------------------------------------------------------------------
# Small tables
# ---------------------------------------------------------------------------

def audit(actor_id: Optional[int], action: str, target: str = "", detail: str = "") -> None:
    with connect() as conn:
        conn.execute("INSERT INTO audit (actor_id, action, target, detail, at) VALUES (?,?,?,?,?)",
                     (actor_id, action, target, detail[:500], time.time()))


def track(name: str, user_id: Optional[int] = None) -> None:
    """Product analytics: an event name and (optionally) who. No page content, no IP address."""
    with connect() as conn:
        conn.execute("INSERT INTO events (name, user_id, at) VALUES (?,?,?)", (name, user_id, time.time()))


def flag(key: str, default: bool = True) -> bool:
    """Feature switch: an admin-set value wins, then FEATURE_<KEY> in the environment, then the default."""
    with connect() as conn:
        row = conn.execute("SELECT value FROM flags WHERE key=?", (key,)).fetchone()
    raw = row["value"] if row else os.environ.get(f"FEATURE_{key.upper()}")
    return default if raw in (None, "") else str(raw).strip().lower() not in {"0", "false", "no", "off"}


def set_flag(admin_id: int, key: str, value: bool) -> None:
    with connect() as conn:
        conn.execute("INSERT INTO flags VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, "1" if value else "0"))
    audit(admin_id, "flag.set", key, str(value))


def query(sql: str, args: tuple = ()) -> list:
    with connect() as conn:
        return conn.execute(sql, args).fetchall()


def execute(sql: str, args: tuple = ()) -> int:
    with connect() as conn:
        return conn.execute(sql, args).lastrowid or 0
