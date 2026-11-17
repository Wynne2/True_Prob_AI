"""
True Prob AI web app (Starlette, server-rendered).

Run locally:   uvicorn web.app:app --port 8000
Environment:   see docs/DEPLOY.md. Nothing here places a bet or a trade.
"""

from __future__ import annotations

import base64
import csv
import hashlib
import io
import json
import logging
import os
import re
import secrets
import time
from collections import defaultdict, deque
from pathlib import Path
from urllib.parse import urlencode

from markdown_it import MarkdownIt
from starlette.applications import Starlette
from starlette.concurrency import run_in_threadpool
from starlette.exceptions import HTTPException
from starlette.middleware import Middleware
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.middleware.sessions import SessionMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response
from starlette.routing import Mount, Route
from starlette.staticfiles import StaticFiles
from starlette.templating import Jinja2Templates

import config  # noqa: F401  (loads .env)
from web import billing, content, db, jobs, mailer

logger = logging.getLogger(__name__)
HERE = Path(__file__).parent
PRODUCTION = (os.environ.get("APP_ENV") or "").lower() == "production"
templates = Jinja2Templates(directory=str(HERE / "templates"))
_md = MarkdownIt("commonmark", {"html": False}).enable("table")
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[^@\s]{2,}$")
ADVISOR_BANDS = {"favorites": (-600, -200, "Favourites (-600 to -200)"),
                 "balanced": (-250, 250, "Near even (-250 to +250)"),
                 "wide": (-600, 400, "Everything (-600 to +400)")}


# Runs before the stylesheet so the first paint already has the right theme: the stored choice, else the OS
# preference. Inline and synchronous on purpose; the CSP allows exactly this text by its hash.
THEME_SCRIPT = ('(function(){var t=null;try{t=localStorage.getItem("tp-theme")}catch(e){}'
                'if(t!=="light"&&t!=="dark")t=window.matchMedia&&matchMedia("(prefers-color-scheme: light)").matches?"light":"dark";'
                'document.documentElement.setAttribute("data-theme",t)})();')
THEME_SCRIPT_HASH = "sha256-" + base64.b64encode(hashlib.sha256(THEME_SCRIPT.encode()).digest()).decode()


def _secret_key() -> str:
    key = os.environ.get("SECRET_KEY")
    if key:
        return key
    if PRODUCTION:
        raise RuntimeError("SECRET_KEY must be set in production")
    path = db.ROOT / "data" / ".secret_key"
    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(secrets.token_urlsafe(48), encoding="utf-8")
    return path.read_text(encoding="utf-8").strip()


def base_url(request: Request) -> str:
    return (os.environ.get("BASE_URL") or str(request.base_url)).rstrip("/")


# ---------------------------------------------------------------------------
# Request helpers: user, CSRF, rate limits, rendering
# ---------------------------------------------------------------------------

def current_user(request: Request):
    uid = request.session.get("uid")
    user = db.get_user(uid) if uid else None
    if user is None or user["session_version"] != request.session.get("sv"):
        return None
    db.refresh_free_credits(user)
    return user


def csrf_token(request: Request) -> str:
    if "csrf" not in request.session:
        request.session["csrf"] = secrets.token_urlsafe(24)
    return request.session["csrf"]


async def checked_form(request: Request):
    """Read a POST form and require the session's CSRF token."""
    form = await request.form()
    sent, want = str(form.get("csrf") or ""), request.session.get("csrf") or ""
    if not want or not secrets.compare_digest(sent, want):
        raise HTTPException(403, "This form expired. Go back, reload the page and try again.")
    return form


_hits: dict[str, deque] = defaultdict(deque)
LONGEST_WINDOW = 3600  # every `per` passed to _over must be at most this
_swept = [0.0]


def _over(key: str, n: int, per: int) -> bool:
    # shortcut: per-process memory; use a shared store when there is more than one web instance.
    now = time.time()
    if now - _swept[0] > 300:  # keys are attacker-chosen (emails, IPs), so drop idle ones or memory grows forever
        _swept[0] = now
        for k in [k for k, q in _hits.items() if not q or q[-1] < now - LONGEST_WINDOW]:
            del _hits[k]
    q = _hits[key]
    while q and q[0] < now - per:
        q.popleft()
    q.append(now)
    return len(q) > n


def limited(request: Request, bucket: str, n: int, per: int) -> bool:
    """True when this client has made more than ``n`` requests to ``bucket`` in ``per`` seconds."""
    ip = (request.headers.get("x-forwarded-for") or (request.client.host if request.client else "?")).split(",")[0].strip()
    return _over(f"{bucket}:{ip}", n, per)


_dummy_hash: list[str] = []


def _password_matches(user, password: str) -> bool:
    """Checks a password with the same scrypt work whether or not the account exists."""
    if user is None:
        if not _dummy_hash:
            _dummy_hash.append(db.hash_password(secrets.token_urlsafe(16)))
        db.check_password(password, _dummy_hash[0])
        return False
    return db.check_password(password, user["pw_hash"])


def flash(request: Request, message: str, kind: str = "ok") -> None:
    request.session["flash"] = [kind, message]


def receipt(request: Request, user, result: str, kind: str, what: str) -> None:
    """Say what a spend cost and what is left, so no credit disappears without a line on screen."""
    if result == "ok":
        n = db.COSTS[kind]
        flash(request, f"Charged {n} credit{'s' if n != 1 else ''} for {what}. "
                       f"You have {db.balance(user['id'])} left.")
    elif result == "already":
        flash(request, f"You already opened {what}. Nothing was charged.")


PRIVATE_PREFIXES = ("/app", "/admin", "/login", "/register", "/forgot", "/reset", "/verify", "/billing")


def render(request: Request, name: str, status_code: int = 200, **ctx) -> Response:
    user = ctx.pop("user", None) or current_user(request)
    ctx.setdefault("noindex", status_code >= 400 or request.url.path.startswith(PRIVATE_PREFIXES))
    ctx.update(
        user=user, plan=db.effective_plan(user) if user else None, balance=db.balance(user["id"]) if user else None,
        reset=db.next_reset(user) if user else None,
        csrf=csrf_token(request), flash=request.session.pop("flash", None), plans=db.PLANS, costs=db.COSTS, theme_script=THEME_SCRIPT,
        billing_on=billing.enabled(), legal_reviewed=db.flag("legal_reviewed", False), path=request.url.path,
        support_email=os.environ.get("SUPPORT_EMAIL") or "support@example.com", canonical=base_url(request) + request.url.path,
        feature={k: db.flag(k) for k in ("arbitrage", "advisor", "scan")},
        announcements=db.query("SELECT body FROM announcements WHERE active=1 ORDER BY id DESC LIMIT 3") if user else [],
    )
    return templates.TemplateResponse(request, name, ctx, status_code=status_code)


def need_user(request: Request, verified: bool = False):
    """The signed-in user, or a redirect response to send instead."""
    user = current_user(request)
    if user is None:
        return None, RedirectResponse(f"/login?{urlencode({'next': request.url.path})}", 303)
    if verified and not user["verified_at"]:
        flash(request, "Confirm your email address first. The link is in your inbox.", "warn")
        return None, RedirectResponse("/app/account", 303)
    return user, None


def pct(value, digits: int = 1) -> str:
    return "–" if value is None else f"{float(value) * 100:.{digits}f}%"


def american(odds) -> str:
    return f"+{int(odds)}" if int(odds) > 0 else str(int(odds))


def when(ts) -> str:
    return "–" if not ts else time.strftime("%b %d, %Y %H:%M UTC", time.gmtime(float(ts)))


def day(ts) -> str:
    return "–" if not ts else time.strftime("%b %d, %Y", time.gmtime(float(ts))).replace(" 0", " ")


def ago(ts) -> str:
    if not ts:
        return "never"
    mins = int((time.time() - float(ts)) // 60)
    return "just now" if mins < 1 else f"{mins} min ago" if mins < 90 else f"{mins // 60} h ago"


VENUE_SITES = (("https://kalshi.com/", "Kalshi"), ("https://polymarket.com/", "Polymarket"))


def venue_links(urls) -> list[tuple[str, str]]:
    """(url, venue) for market pages on the venues we scan; anything else is dropped."""
    return [(u, name) for u in urls or [] if isinstance(u, str) for host, name in VENUE_SITES if u.startswith(host)]


def run_status(status: str) -> str:
    """An Advisor run's state in words, including whether its credits came back."""
    back = f"{db.COSTS['advisor']} credits returned"
    return {"running": "Being written", "done": "Ready", "failed": f"Did not complete, {back}",
            "empty": f"Nothing to review in that range, {back}"}.get(status, status)


templates.env.filters.update(pct=pct, american=american, when=when, day=day, ago=ago, venue_links=venue_links,
                             run_status=run_status)


# ---------------------------------------------------------------------------
# Public pages
# ---------------------------------------------------------------------------

SUMMARY_FIELDS = ("decided", "hit_rate", "brier_model", "brier_market", "flat_roi_per_bet")


def graded_totals(rows: list[dict]) -> dict:
    """Every graded line pooled, scored the way engine/clv.py scores one date. Both Briers use the same lines."""
    decided = [r for r in rows if r.get("result") in ("win", "loss")]
    both = [(r["model_probability"], r["market_probability"], r["result"] == "win") for r in decided
            if r.get("market_probability") is not None and r.get("model_probability") is not None]
    flagged = [r for r in rows if (r.get("edge") or 0) > 0]
    hits = [r["result"] == "win" for r in flagged if r.get("result") in ("win", "loss")]
    rois = [r["roi"] for r in flagged if r.get("roi") is not None]
    mean = lambda xs: sum(xs) / len(xs) if xs else None
    return {"decided": len(decided), "with_market": len(both),
            "brier_model": mean([(m - y) ** 2 for m, _, y in both]), "brier_market": mean([(k - y) ** 2 for _, k, y in both]),
            "flagged": len(hits), "flagged_hit_rate": mean(hits), "flagged_roi": mean(rois)}


def _performance() -> dict:
    """Published results, read from files the grading jobs write. Nothing is filtered out."""
    out: dict = {"slope": None, "dates": [], "scorecard": None, "total": None}
    try:
        out["slope"] = json.loads((db.ROOT / "data" / "models" / "market_slope.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        pass
    rows: list[dict] = []
    for path in sorted((db.ROOT / "data" / "predictions").glob("????-??-??.graded.json")):
        try:
            graded = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        s = graded.get("summary") or {}
        if (s.get("all") or {}).get("decided"):
            # A group with no decided lines has no hit_rate or Brier keys; the templates need them present (as None).
            out["dates"].append({"date": path.name[:10], "games": s.get("final_games"), **{
                k: {f: (s.get(k) or {}).get(f) for f in SUMMARY_FIELDS} for k in ("all", "positive_edge")}})
            rows += graded.get("rows") or []
    if out["dates"]:
        out["total"] = graded_totals(rows) | {"games": sum(d["games"] or 0 for d in out["dates"])}
    try:
        from engine.candidate.track import scorecard

        out["scorecard"] = scorecard()
    except Exception:
        logger.exception("Scorecard unavailable")
    return out


def structured_data(base: str) -> list[dict]:
    """Home-page JSON-LD: site name, organisation and the real plan prices (from PLANS, so never stale)."""
    home = base + "/"
    return [
        {"@context": "https://schema.org", "@type": "WebSite", "name": "True Prob AI", "url": home},
        {"@context": "https://schema.org", "@type": "Organization", "name": "True Prob AI", "url": home},
        {"@context": "https://schema.org", "@type": "SoftwareApplication", "name": "True Prob AI", "url": home,
         "applicationCategory": "BusinessApplication", "operatingSystem": "Web",
         "offers": [{"@type": "Offer", "name": p["name"], "price": str(p["price"]), "priceCurrency": "USD",
                     "url": base + "/pricing"} for p in db.PLANS.values()]},
    ]


def performance_dataset(base: str, dates: list[str]) -> dict | None:
    """Dataset JSON-LD for /performance: one CSV per graded date, the same files the page links to."""
    if not dates:
        return None
    return {"@context": "https://schema.org", "@type": "Dataset", "name": "True Prob AI graded NBA player prop lines",
            "description": "Every NBA player prop line True Prob AI published before tip-off, graded against box scores the "
                           "next day: offered price, no-vig market probability, model probability, gap, result and "
                           "flat-stake return. Nothing is left out, including losing dates.",
            "url": base + "/performance", "creator": {"@type": "Organization", "name": "True Prob AI", "url": base + "/"},
            "isAccessibleForFree": True, "temporalCoverage": f"{dates[0]}/{dates[-1]}",
            "distribution": [{"@type": "DataDownload", "encodingFormat": "text/csv", "name": f"Graded lines {d}",
                              "contentUrl": f"{base}/performance/{d}.csv"} for d in dates]}


async def landing(request: Request):
    db.track("visit_landing")
    return render(request, "landing.html", faq=content.FAQ, board=jobs.load_board(),
                  structured_data=structured_data(base_url(request)))


async def favicon(request: Request):
    return RedirectResponse("/static/favicon.svg", 301)


async def pricing(request: Request):
    db.track("visit_pricing")
    return render(request, "pricing.html", faq=content.FAQ)


async def performance(request: Request):
    perf = await run_in_threadpool(_performance)
    return render(request, "performance.html", perf=perf,
                  dataset=performance_dataset(base_url(request), [d["date"] for d in perf["dates"]]))


GRADED_COLUMNS = ("player", "prop", "sportsbook", "odds", "implied_probability", "market_probability", "model_probability",
                  "probability_method", "edge", "actual", "result", "roi", "closing_probability", "clv")


async def graded_csv(request: Request):
    """Every graded line for one date, as saved before tip-off and graded after. Nothing dropped or edited."""
    date = request.path_params["date"]
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date):
        raise HTTPException(404)
    try:
        rows = json.loads((db.ROOT / "data" / "predictions" / f"{date}.graded.json").read_text(encoding="utf-8")).get("rows") or []
    except (OSError, ValueError):
        raise HTTPException(404)
    out = io.StringIO()
    writer = csv.writer(out)
    writer.writerow(GRADED_COLUMNS)
    for r in rows:
        writer.writerow(["" if r.get(c) is None else csv_cell(r[c]) if isinstance(r[c], str) else r[c] for c in GRADED_COLUMNS])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="true-prob-ai-graded-{date}.csv"'})


async def legal(request: Request):
    page = content.LEGAL.get(request.path_params["slug"])
    if page is None:
        raise HTTPException(404)
    support = os.environ.get("SUPPORT_EMAIL") or "support@example.com"
    return render(request, "legal.html", page=page, pages=content.LEGAL,
                  body=_md.render(page["body"].replace("{support_email}", support)))


async def healthz(request: Request):
    db.query("SELECT 1")
    return JSONResponse({"ok": True, "jobs": jobs.health()})


async def robots(request: Request):
    return PlainTextResponse(f"User-agent: *\nDisallow: /app\nDisallow: /admin\nDisallow: /billing\n"
                             f"Sitemap: {base_url(request)}/sitemap.xml\n")


def performance_lastmod() -> str | None:
    """The day /performance last got new numbers: the newest graded board or slope fit. None if unknown."""
    days = [p.name[:10] for p in (db.ROOT / "data" / "predictions").glob("????-??-??.graded.json")]
    try:
        days.append(str(json.loads((db.ROOT / "data" / "models" / "market_slope.json").read_text(encoding="utf-8"))["fitted_at"])[:10])
    except (OSError, ValueError, KeyError):
        pass
    days = [d for d in days if re.fullmatch(r"\d{4}-\d{2}-\d{2}", d)]
    return max(days) if days else None


async def sitemap(request: Request):
    # lastmod only where the date is real; Google ignores a sitemap whose dates are not.
    urls = [("/", None), ("/pricing", None), ("/performance", performance_lastmod())] + [(f"/legal/{s}", None) for s in content.LEGAL]
    body = "".join(f"<url><loc>{base_url(request)}{u}</loc>{f'<lastmod>{d}</lastmod>' if d else ''}</url>" for u, d in urls)
    return Response(f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{body}</urlset>',
                    media_type="application/xml")


# ---------------------------------------------------------------------------
# Accounts
# ---------------------------------------------------------------------------

def _sign_in(request: Request, user) -> None:
    csrf = request.session.get("csrf")
    request.session.clear()  # new session on login (no fixation)
    request.session.update(uid=user["id"], sv=user["session_version"], csrf=csrf or secrets.token_urlsafe(24))


def _send_verification(request: Request, user) -> str:
    link = f"{base_url(request)}/verify?token={db.make_token(user['id'], 'verify', 3 * 86400)}"
    mailer.send(user["email"], "Confirm your email for True Prob AI",
                f"Welcome to True Prob AI.\n\nConfirm your email address to start using your {db.PLANS['free']['credits']} free credits:\n{link}\n\n"
                "The link works once and expires in 3 days. If you did not create this account, ignore this email.")
    return link


async def register(request: Request):
    if request.method == "GET":
        return render(request, "auth.html", mode="register", regions=content.REGIONS)
    form = await checked_form(request)
    email, password = str(form.get("email") or "").strip().lower(), str(form.get("password") or "")
    region = str(form.get("region") or "")
    error = None
    if not EMAIL_RE.match(email):
        error = "Enter a valid email address."
    elif len(password) < 10 or len(password) > 200:
        error = "Use a password of at least 10 characters."
    elif form.get("age") != "yes" or form.get("terms") != "yes":
        error = "You must be 21 or older and accept the terms to create an account."
    elif region not in content.REGIONS:
        error = "Choose where you live."
    elif region in content.blocked_regions():
        error = "True Prob AI is not available where you live."
    if error:
        return render(request, "auth.html", 400, mode="register", regions=content.REGIONS, error=error, email=email, region=region)
    # Counts only sign-ups that pass validation, so typos never lock anyone out.
    if limited(request, "register", 5, 3600):
        raise HTTPException(429, "Too many sign-ups from this network. Try again in an hour.")
    uid = db.create_user(email, password, region)
    if uid is None:
        return render(request, "auth.html", 400, mode="register", regions=content.REGIONS, email=email, region=region,
                      error="An account with that email already exists. Sign in instead.")
    user = db.get_user(uid)
    link = _send_verification(request, user)
    _sign_in(request, user)
    db.track("registered", uid)
    db.audit(uid, "user.register")
    if not mailer.configured() and not PRODUCTION:
        request.session["dev_verify"] = link  # local development has no mail server
    return RedirectResponse("/app", 303)


async def login(request: Request):
    nxt = request.query_params.get("next") or "/app"
    if not nxt.startswith("/") or nxt.startswith("//"):
        nxt = "/app"
    if request.method == "GET":
        return render(request, "auth.html", mode="login", next=nxt)
    form = await checked_form(request)
    if limited(request, "login", 10, 600):
        raise HTTPException(429, "Too many sign-in attempts. Wait ten minutes and try again.")
    email = str(form.get("email") or "").strip().lower()
    # Per account as well as per network, so spreading guesses over many addresses does not help.
    if _over(f"login-account:{email}", 10, 900):
        raise HTTPException(429, "Too many sign-in attempts. Wait fifteen minutes and try again, or reset your password.")
    user = db.get_user(email=email)
    if not _password_matches(user, str(form.get("password") or "")):
        db.audit(user["id"] if user else None, "login.failed")
        return render(request, "auth.html", 400, mode="login", next=nxt, email=email, error="That email and password do not match.")
    if db.needs_rehash(user["pw_hash"]):
        db.update_user(user["id"], pw_hash=db.hash_password(str(form.get("password") or "")))
    if email in db.admin_emails() and not user["is_admin"]:
        db.update_user(user["id"], is_admin=1)
        user = db.get_user(user["id"])
    _sign_in(request, user)
    db.audit(user["id"], "login.ok")
    return RedirectResponse(nxt, 303)


async def logout(request: Request):
    await checked_form(request)
    request.session.clear()
    return RedirectResponse("/", 303)


async def verify(request: Request):
    uid = db.use_token(request.query_params.get("token", ""), "verify")
    if uid is None:
        flash(request, "That confirmation link is invalid or was already used. Send a new one from Account.", "warn")
        return RedirectResponse("/app/account" if current_user(request) else "/login", 303)
    db.update_user(uid, verified_at=time.time())
    request.session.pop("dev_verify", None)
    db.track("verified", uid)
    flash(request, "Email confirmed. Your credits are ready to use.")
    return RedirectResponse("/app" if current_user(request) else "/login", 303)


async def resend(request: Request):
    await checked_form(request)
    user, redirect = need_user(request)
    if redirect:
        return redirect
    if limited(request, "resend", 3, 3600):
        raise HTTPException(429, "A confirmation email was sent recently. Check your inbox and spam folder.")
    if not user["verified_at"]:
        link = _send_verification(request, user)
        if not mailer.configured() and not PRODUCTION:
            request.session["dev_verify"] = link
    flash(request, "Confirmation email sent.")
    return RedirectResponse("/app/account", 303)


async def forgot(request: Request):
    if request.method == "GET":
        return render(request, "auth.html", mode="forgot")
    form = await checked_form(request)
    if limited(request, "forgot", 5, 3600):
        raise HTTPException(429, "Too many reset requests. Try again later.")
    user = db.get_user(email=str(form.get("email") or ""))
    dev_link = None
    if user:
        link = f"{base_url(request)}/reset?token={db.make_token(user['id'], 'reset', 3600)}"
        mailer.send(user["email"], "Reset your True Prob AI password",
                    f"Use this link to choose a new password. It works once and expires in one hour:\n{link}\n\n"
                    "If you did not ask for this, ignore this email; your password stays the same.")
        if not mailer.configured() and not PRODUCTION:
            dev_link = link
    # Same answer whether or not the account exists.
    return render(request, "auth.html", mode="forgot", sent=True, dev_link=dev_link)


async def reset(request: Request):
    if request.method == "GET":
        return render(request, "auth.html", mode="reset", token=request.query_params.get("token", ""))
    form = await checked_form(request)
    password, token = str(form.get("password") or ""), str(form.get("token") or "")
    if len(password) < 10 or len(password) > 200:
        return render(request, "auth.html", 400, mode="reset", token=token, error="Use a password of at least 10 characters.")
    uid = db.use_token(token, "reset")
    if uid is None:
        return render(request, "auth.html", 400, mode="forgot", error="That reset link is invalid or expired. Request a new one.")
    user = db.get_user(uid)
    # A new session version signs out every existing session.
    db.update_user(uid, pw_hash=db.hash_password(password), session_version=user["session_version"] + 1)
    db.audit(uid, "password.reset")
    flash(request, "Password changed. Sign in with the new one.")
    return RedirectResponse("/login", 303)


# ---------------------------------------------------------------------------
# App: dashboard, explorer, arbitrage, advisor
# ---------------------------------------------------------------------------

async def dashboard(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    board, arb = jobs.load_board(), jobs.load_arb()
    watch = db.query("SELECT * FROM watchlist WHERE user_id=? ORDER BY created_at DESC LIMIT 20", (user["id"],))
    watched = {w["key"] for w in watch}
    hits = [r for r in (board or {}).get("rows", []) if r["player"].lower() in watched][:12]
    perf = await run_in_threadpool(_performance)
    steps = [("Confirm your email", bool(user["verified_at"]), "/app/account"),
             ("Open your first line or today's board", bool(db.query("SELECT 1 FROM unlocks WHERE user_id=? LIMIT 1", (user["id"],))),
              "/app/explorer"),
             ("Watch a player from any line's page", bool(watch), "/app/explorer")]
    return render(request, "dashboard.html", user=user, board=board, arb=arb, watch=watch, watch_hits=hits,
                  steps=None if all(done for _, done, _ in steps) else steps,
                  history=db.history(user["id"], 8), perf_dates=perf["dates"][-3:],
                  runs=db.query("SELECT id, slate, status, created_at FROM advisor_runs WHERE user_id=? ORDER BY id DESC LIMIT 5", (user["id"],)),
                  dev_verify=request.session.get("dev_verify"))


def _filter_rows(rows: list[dict], q) -> list[dict]:
    text, prop, sort = (q.get("q") or "").strip().lower(), q.get("prop") or "", q.get("sort") or "edge"
    try:
        lo, hi = int(q.get("min_odds") or -100000), int(q.get("max_odds") or 100000)
    except ValueError:
        lo, hi = -100000, 100000
    out = [r for r in rows if (not text or text in r["player"].lower() or text in r["team"].lower())
           and (not prop or r["prop"] == prop) and lo <= r["odds"] <= hi
           and (q.get("backed") != "1" or (r["model_agrees"] and r["weight"] > 0))]
    key = {"edge": lambda r: -r["edge"], "player": lambda r: (r["player"], r["prop"], r["line"]),
           "price": lambda r: r["odds"]}.get(sort, lambda r: -r["edge"])
    return sorted(out, key=key)


async def explorer(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    job = jobs.ensure_board() if user["verified_at"] else jobs.status("board")
    board = jobs.load_board()
    slate = jobs.today().isoformat()
    unlocked = db.is_unlocked(user["id"], "board", slate)
    params = dict(request.query_params)
    if not unlocked:  # gap size and model backing are what customers pay for: no sorting or filtering by them
        params.pop("backed", None)
        if params.get("sort", "edge") == "edge":
            params["sort"] = "player"
    rows = _filter_rows((board or {}).get("rows", []), params)
    mine = {r["ref"].split(":", 1)[1] for r in db.query(
        "SELECT ref FROM unlocks WHERE user_id=? AND kind='prop' AND ref LIKE ?", (user["id"], f"{slate}:%"))}
    return render(request, "explorer.html", user=user, board=board, job=job, slate=slate, unlocked=unlocked, rows=rows[:400],
                  total=len(rows), mine=mine, q=request.query_params,
                  props=sorted({r["prop"] for r in (board or {}).get("rows", [])}))


async def unlock_board(request: Request):
    await checked_form(request)
    user, redirect = need_user(request, verified=True)
    if redirect:
        return redirect
    slate = jobs.today().isoformat()
    if not (jobs.load_board(slate) or {}).get("rows"):
        flash(request, "There is no board to open yet. Nothing was charged.", "warn")
        return RedirectResponse("/app/explorer", 303)
    result = db.spend(user["id"], "board", slate, f"Full board for {slate}")
    if result == "insufficient":
        db.track("limit_hit", user["id"])
        return RedirectResponse("/pricing?need=board", 303)
    if result == "ok":
        db.track("board_unlocked", user["id"])
    receipt(request, user, result, "board", f"today's full board ({slate})")
    return RedirectResponse("/app/explorer", 303)


def _find_prop(pid: str):
    board = jobs.load_board()
    row = next((r for r in (board or {}).get("rows", []) if r["id"] == pid), None)
    return board, row


async def prop_detail(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    board, row = _find_prop(request.path_params["pid"])
    if row is None:
        raise HTTPException(404, "That line is no longer on today's board.")
    slate = board["slate"]
    open_ = db.is_unlocked(user["id"], "board", slate) or db.is_unlocked(user["id"], "prop", f"{slate}:{row['id']}")
    other = next((r for r in board["rows"] if r["player"] == row["player"] and r["prop"] == row["prop"]
                  and r["line"] == row["line"] and r["side"] != row["side"]), None)
    watching = bool(db.query("SELECT 1 FROM watchlist WHERE user_id=? AND key=?", (user["id"], row["player"].lower())))
    return render(request, "prop.html", user=user, board=board, r=row, open=open_, other=other if open_ else None, watching=watching)


async def unlock_prop(request: Request):
    await checked_form(request)
    user, redirect = need_user(request, verified=True)
    if redirect:
        return redirect
    board, row = _find_prop(request.path_params["pid"])
    if row is None:
        raise HTTPException(404, "That line is no longer on today's board. Nothing was charged.")
    result = db.spend(user["id"], "prop", f"{board['slate']}:{row['id']}",
                      f"Analysis: {row['player']} {row['prop']} {row['side']} {row['line']:g}")
    if result == "insufficient":
        db.track("limit_hit", user["id"])
        return RedirectResponse("/pricing?need=prop", 303)
    if result == "ok":
        db.track("prop_unlocked", user["id"])
    receipt(request, user, result, "prop", f"{row['player']} {row['prop']} {row['side']} {row['line']:g}")
    return RedirectResponse(f"/app/prop/{row['id']}", 303)


async def watch(request: Request):
    form = await checked_form(request)
    user, redirect = need_user(request)
    if redirect:
        return redirect
    label = str(form.get("player") or "").strip()[:80]
    if label:
        if form.get("remove"):
            db.execute("DELETE FROM watchlist WHERE user_id=? AND key=?", (user["id"], label.lower()))
        elif len(db.query("SELECT 1 FROM watchlist WHERE user_id=?", (user["id"],))) < 50:
            db.execute("INSERT OR IGNORE INTO watchlist VALUES (?,?,?,?)", (user["id"], label.lower(), label, time.time()))
    back = str(form.get("back") or "/app")
    return RedirectResponse(back if back.startswith("/app") else "/app", 303)


async def arbitrage(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    if not db.flag("arbitrage"):
        return render(request, "arbitrage.html", user=user, off=True)
    job = jobs.ensure_arb() if user["verified_at"] else jobs.status("arbitrage")
    arb = jobs.load_arb()
    return render(request, "arbitrage.html", user=user, arb=arb, job=job,
                  unlocked=db.is_unlocked(user["id"], "arbitrage", jobs.today().isoformat()))


async def unlock_arb(request: Request):
    await checked_form(request)
    user, redirect = need_user(request, verified=True)
    if redirect:
        return redirect
    if jobs.load_arb() is None or not db.flag("arbitrage"):
        flash(request, "The scan has not finished yet. Nothing was charged.", "warn")
        return RedirectResponse("/app/arbitrage", 303)
    result = db.spend(user["id"], "arbitrage", jobs.today().isoformat(), "Arbitrage terminal for today")
    if result == "insufficient":
        db.track("limit_hit", user["id"])
        return RedirectResponse("/pricing?need=arbitrage", 303)
    if result == "ok":
        db.track("arbitrage_unlocked", user["id"])
    receipt(request, user, result, "arbitrage", "today's arbitrage terminal")
    return RedirectResponse("/app/arbitrage", 303)


async def advisor(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    board = jobs.load_board()
    jobs.reap_advisor_runs(user["id"])
    runs = db.query("SELECT * FROM advisor_runs WHERE user_id=? ORDER BY id DESC LIMIT 10", (user["id"],))
    return render(request, "advisor.html", user=user, board=board, runs=runs, bands=ADVISOR_BANDS, run=None,
                  ai_ready=bool(os.environ.get("OPENAI_API_KEY")))


async def advisor_run(request: Request):
    form = await checked_form(request)
    user, redirect = need_user(request, verified=True)
    if redirect:
        return redirect
    if not db.flag("advisor") or not os.environ.get("OPENAI_API_KEY"):
        flash(request, "The AI Advisor is switched off right now. Nothing was charged.", "warn")
        return RedirectResponse("/app/advisor", 303)
    if limited(request, "advisor", 6, 600):
        raise HTTPException(429, "That is a lot of analyses in a short time. Wait a few minutes.")
    board = jobs.load_board()
    band = ADVISOR_BANDS.get(str(form.get("band")), ADVISOR_BANDS["balanced"])
    if not board or not board.get("rows"):
        flash(request, "Open the Explorer first so today's board is scanned. Nothing was charged.", "warn")
        return RedirectResponse("/app/advisor", 303)
    busy = await run_in_threadpool(jobs.running_advisor, user["id"])
    if busy:
        flash(request, "Your previous analysis is still being written. Nothing was charged.")
        return RedirectResponse(f"/app/advisor/{busy}", 303)
    if db.balance(user["id"]) < db.COSTS["advisor"]:
        db.track("limit_hit", user["id"])
        return RedirectResponse("/pricing?need=advisor", 303)
    run_id = await run_in_threadpool(jobs.start_advisor, user["id"], board["slate"], band[0], band[1])
    if run_id is None:
        flash(request, "The analysis could not start. Nothing was charged.", "warn")
        return RedirectResponse("/app/advisor", 303)
    db.track("advisor_run", user["id"])
    receipt(request, user, "ok", "advisor", "an AI Advisor analysis (refunded automatically if it fails)")
    return RedirectResponse(f"/app/advisor/{run_id}", 303)


async def advisor_view(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    jobs.reap_advisor_runs(user["id"])
    rows = db.query("SELECT * FROM advisor_runs WHERE id=? AND user_id=?", (request.path_params["run_id"], user["id"]))
    if not rows:
        raise HTTPException(404)
    run = rows[0]
    body = _md.render(run["body"]) if run["status"] == "done" else ""
    return render(request, "advisor.html", user=user, board=jobs.load_board(), runs=[], bands=ADVISOR_BANDS, run=run, body=body,
                  ai_ready=True)


# ---------------------------------------------------------------------------
# Account and billing
# ---------------------------------------------------------------------------

async def account(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    if request.query_params.get("checkout") == "done":
        flash(request, "Thanks. Your plan and credits appear here as soon as the payment is confirmed (usually within a minute).")
        return RedirectResponse("/app/account", 303)
    return render(request, "account.html", user=user, history=db.history(user["id"], 100),
                  dev_verify=request.session.get("dev_verify"))


def csv_cell(text: str) -> str:
    """Text a spreadsheet will not run as a formula (OWASP CSV injection)."""
    return "'" + text if text[:1] in ("=", "+", "-", "@", "\t", "\r") else text


async def history_csv(request: Request):
    user, redirect = need_user(request)
    if redirect:
        return redirect
    out, running = io.StringIO(), 0
    writer = csv.writer(out)
    writer.writerow(["When (UTC)", "What", "Credits", "Balance after"])
    for h in db.query("SELECT * FROM ledger WHERE user_id=? ORDER BY id", (user["id"],)):
        running += h["delta"]
        writer.writerow([time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(h["created_at"])), csv_cell(h["reason"]), h["delta"], running])
    return Response(out.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": 'attachment; filename="true-prob-ai-credits.csv"'})


async def change_password(request: Request):
    form = await checked_form(request)
    user, redirect = need_user(request)
    if redirect:
        return redirect
    new = str(form.get("new") or "")
    if not db.check_password(str(form.get("current") or ""), user["pw_hash"]):
        flash(request, "Your current password is not correct.", "warn")
    elif len(new) < 10 or len(new) > 200:
        flash(request, "Use a new password of at least 10 characters.", "warn")
    else:
        db.update_user(user["id"], pw_hash=db.hash_password(new), session_version=user["session_version"] + 1)
        request.session["sv"] = user["session_version"] + 1  # this session stays signed in; all others end
        db.audit(user["id"], "password.change")
        flash(request, "Password changed. Other devices were signed out.")
    return RedirectResponse("/app/account", 303)


async def sign_out_others(request: Request):
    form = await checked_form(request)
    user, redirect = need_user(request)
    if redirect:
        return redirect
    if not db.check_password(str(form.get("password") or ""), user["pw_hash"]):
        flash(request, "Your password is not correct. No device was signed out.", "warn")
    else:
        db.update_user(user["id"], session_version=user["session_version"] + 1)
        request.session["sv"] = user["session_version"] + 1
        db.audit(user["id"], "sessions.revoke_others")
        flash(request, "Every other device and browser was signed out. This one stays signed in.")
    return RedirectResponse("/app/account", 303)


async def delete_account(request: Request):
    form = await checked_form(request)
    user, redirect = need_user(request)
    if redirect:
        return redirect
    if not db.check_password(str(form.get("password") or ""), user["pw_hash"]):
        flash(request, "Enter your password to delete the account.", "warn")
        return RedirectResponse("/app/account", 303)
    try:
        await run_in_threadpool(billing.cancel_now, user)
    except Exception:
        flash(request, "We could not cancel your subscription with the payment provider, so the account was not deleted. "
                       "Try again, or contact support.", "warn")
        return RedirectResponse("/app/account", 303)
    db.audit(user["id"], "user.delete")
    db.delete_user(user["id"])
    request.session.clear()
    return render(request, "message.html", title="Account deleted",
                  message="Your account, watchlist and analysis history were erased and any subscription was cancelled.")


async def checkout(request: Request):
    form = await checked_form(request)
    user, redirect = need_user(request, verified=True)
    if redirect:
        return redirect
    plan = str(form.get("plan") or "")
    if not billing.enabled() or plan not in billing.price_ids():
        flash(request, "Payments are not switched on yet. Nothing was charged.", "warn")
        return RedirectResponse("/pricing", 303)
    if db.effective_plan(user) != "free":
        return RedirectResponse("/app/account", 303)  # plan changes go through the billing portal
    db.track("checkout_started", user["id"])
    return RedirectResponse(await run_in_threadpool(billing.create_checkout, user, plan, base_url(request)), 303)


async def portal(request: Request):
    await checked_form(request)
    user, redirect = need_user(request)
    if redirect:
        return redirect
    if not billing.enabled() or not user["stripe_customer_id"]:
        flash(request, "There is no billing profile for this account yet.", "warn")
        return RedirectResponse("/app/account", 303)
    return RedirectResponse(await run_in_threadpool(billing.create_portal, user, base_url(request)), 303)


async def stripe_webhook(request: Request):
    payload = await request.body()
    if not os.environ.get("STRIPE_WEBHOOK_SECRET"):
        return PlainTextResponse("billing is not configured", 503)
    try:
        event = billing.verify(payload, request.headers.get("stripe-signature", ""))
    except ValueError:
        return PlainTextResponse("invalid signature", 400)
    try:
        result = await run_in_threadpool(billing.handle_event, event)
    except Exception:
        logger.exception("Stripe event %s failed", event.get("id"))
        return PlainTextResponse("temporary failure", 500)  # Stripe retries
    return JSONResponse({"result": result})


# ---------------------------------------------------------------------------
# Admin
# ---------------------------------------------------------------------------

def need_admin(request: Request):
    user = current_user(request)
    if user is None or not user["is_admin"]:
        raise HTTPException(404)  # do not reveal that the page exists
    return user


async def admin(request: Request):
    user = need_admin(request)
    search = (request.query_params.get("q") or "").strip().lower()
    users = db.query(
        "SELECT u.*, (SELECT COALESCE(SUM(delta),0) FROM ledger l WHERE l.user_id=u.id) AS balance FROM users u "
        "WHERE deleted_at IS NULL AND email LIKE ? ORDER BY id DESC LIMIT 100", (f"%{search}%",))
    paying = db.query("SELECT plan, COUNT(*) AS n FROM users WHERE deleted_at IS NULL AND plan!='free' AND sub_status IN ('active','trialing') GROUP BY plan")
    since = time.time() - 30 * 86400
    funnel = {r["name"]: r["n"] for r in db.query("SELECT name, COUNT(*) AS n FROM events WHERE at>? GROUP BY name", (since,))}
    return render(
        request, "admin.html", user=user, users=users, search=search, funnel=funnel,
        mrr=sum(db.PLANS[r["plan"]]["price"] * r["n"] for r in paying), paying={r["plan"]: r["n"] for r in paying},
        total_users=db.query("SELECT COUNT(*) AS n FROM users WHERE deleted_at IS NULL")[0]["n"],
        past_due=db.query("SELECT email, sub_status FROM users WHERE sub_status IN ('past_due','unpaid','incomplete') AND deleted_at IS NULL"),
        audit=db.query("SELECT * FROM audit ORDER BY id DESC LIMIT 40"),
        flags={k: db.flag(k, k != "legal_reviewed") for k in ("scan", "arbitrage", "advisor", "legal_reviewed")},
        notes=db.query("SELECT * FROM announcements ORDER BY id DESC LIMIT 10"),
        health=jobs.health(), board=jobs.load_board(), arb=jobs.load_arb(),
        spend_30d=db.query("SELECT ref, COUNT(*) AS n, -SUM(delta) AS credits FROM (SELECT substr(ref,1,instr(ref,':')-1) AS ref, delta "
                           "FROM ledger WHERE delta<0 AND created_at>?) GROUP BY ref", (since,)),
    )


async def admin_action(request: Request):
    admin_user = need_admin(request)
    form = await checked_form(request)
    action = request.path_params["action"]
    if action == "credits":
        try:
            uid, delta = int(form.get("user_id")), int(form.get("delta"))
        except (TypeError, ValueError):
            raise HTTPException(400, "User and amount must be whole numbers.")
        note = str(form.get("note") or "").strip()
        if not note or abs(delta) > 100000 or db.get_user(uid) is None:
            raise HTTPException(400, "A note, a real user and a sensible amount are required.")
        db.adjust_credits(admin_user["id"], uid, delta, note)
    elif action == "flag":
        key = str(form.get("key"))
        if key not in ("scan", "arbitrage", "advisor", "legal_reviewed"):
            raise HTTPException(400)
        db.set_flag(admin_user["id"], key, form.get("value") == "1")
    elif action == "announce":
        body = str(form.get("body") or "").strip()[:300]
        if form.get("hide"):
            db.execute("UPDATE announcements SET active=0 WHERE id=?", (int(form.get("hide")),))
        elif body:
            db.execute("INSERT INTO announcements (body, created_at) VALUES (?,?)", (body, time.time()))
        db.audit(admin_user["id"], "announcement", detail=body or f"hide {form.get('hide')}")
    else:
        raise HTTPException(404)
    return RedirectResponse("/admin", 303)


# ---------------------------------------------------------------------------
# App assembly
# ---------------------------------------------------------------------------

class SecurityHeaders(BaseHTTPMiddleware):
    async def dispatch(self, request, call_next):
        response = await call_next(request)
        response.headers.update({
            "Content-Security-Policy": "default-src 'self'; style-src 'self' https://fonts.googleapis.com; "
                                       f"font-src https://fonts.gstatic.com; img-src 'self' data:; script-src 'self' '{THEME_SCRIPT_HASH}'; "
                                       "frame-ancestors 'none'; base-uri 'self'; form-action 'self' https://checkout.stripe.com https://billing.stripe.com",
            "X-Content-Type-Options": "nosniff",
            "X-Frame-Options": "DENY",
            "Referrer-Policy": "strict-origin-when-cross-origin",
            "Permissions-Policy": "geolocation=(), camera=(), microphone=()",
        })
        if PRODUCTION:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        if request.url.path.startswith(("/app", "/admin")):
            response.headers["Cache-Control"] = "no-store"
        return response


async def on_error(request: Request, exc: HTTPException):
    if request.url.path == "/stripe/webhook":
        return PlainTextResponse(str(exc.detail), exc.status_code)
    titles = {403: "That did not go through", 404: "Page not found", 429: "Slow down"}
    detail = exc.detail if isinstance(exc.detail, str) and exc.status_code != 404 else "There is nothing at this address."
    return render(request, "message.html", exc.status_code, title=titles.get(exc.status_code, "Something went wrong"), message=detail)


async def on_crash(request: Request, exc: Exception):
    logger.exception("Unhandled error on %s", request.url.path)
    return PlainTextResponse("Something went wrong on our side. Nothing was charged. Try again in a moment.", 500)


def P(path, endpoint, methods=("GET",)):
    return Route(path, endpoint, methods=list(methods))


routes = [
    P("/", landing), P("/pricing", pricing), P("/performance", performance), P("/performance/{date}.csv", graded_csv),
    P("/legal/{slug}", legal),
    P("/healthz", healthz), P("/robots.txt", robots), P("/sitemap.xml", sitemap), P("/favicon.ico", favicon),
    P("/register", register, ("GET", "POST")), P("/login", login, ("GET", "POST")), P("/logout", logout, ("POST",)),
    P("/verify", verify), P("/verify/resend", resend, ("POST",)),
    P("/forgot", forgot, ("GET", "POST")), P("/reset", reset, ("GET", "POST")),
    P("/app", dashboard), P("/app/explorer", explorer), P("/app/explorer/unlock", unlock_board, ("POST",)),
    P("/app/prop/{pid}", prop_detail), P("/app/prop/{pid}/unlock", unlock_prop, ("POST",)), P("/app/watch", watch, ("POST",)),
    P("/app/arbitrage", arbitrage), P("/app/arbitrage/unlock", unlock_arb, ("POST",)),
    P("/app/advisor", advisor), P("/app/advisor/run", advisor_run, ("POST",)), P("/app/advisor/{run_id:int}", advisor_view),
    P("/app/account", account), P("/app/account/password", change_password, ("POST",)),
    P("/app/account/sessions", sign_out_others, ("POST",)), P("/app/account/history.csv", history_csv),
    P("/app/account/delete", delete_account, ("POST",)),
    P("/billing/checkout", checkout, ("POST",)), P("/billing/portal", portal, ("POST",)),
    P("/stripe/webhook", stripe_webhook, ("POST",)),
    P("/admin", admin), P("/admin/{action}", admin_action, ("POST",)),
    Mount("/static", StaticFiles(directory=str(HERE / "static")), name="static"),
]

db.init()
app = Starlette(
    routes=routes,
    middleware=[
        Middleware(SecurityHeaders),
        Middleware(SessionMiddleware, secret_key=_secret_key(), session_cookie="tp_session", max_age=14 * 86400,
                   same_site="lax", https_only=PRODUCTION),
    ],
    exception_handlers={HTTPException: on_error, Exception: on_crash},
)
