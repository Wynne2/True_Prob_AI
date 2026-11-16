"""
Customer web app: accounts, credits, billing lifecycle, authorization and security.

Stripe's outbound API (customer, checkout, subscription reads) is replaced by fakes; webhook
signatures are produced and verified with Stripe's real scheme. No network calls.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import threading
import time

import pytest

WEBHOOK_SECRET = "whsec_test_secret"


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("TRUEPROB_DB", str(tmp_path / "app.db"))
    monkeypatch.setenv("STRIPE_SECRET_KEY", "sk_test_fake")
    monkeypatch.setenv("STRIPE_WEBHOOK_SECRET", WEBHOOK_SECRET)
    monkeypatch.setenv("STRIPE_PRICE_PRO", "price_pro")
    monkeypatch.setenv("STRIPE_PRICE_POWER", "price_power")
    monkeypatch.setenv("ADMIN_EMAILS", "boss@example.com")
    monkeypatch.delenv("SMTP_HOST", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    from web import app as webapp, db, jobs, mailer

    monkeypatch.setattr(mailer, "OUTBOX", tmp_path / "outbox.log")
    db.init()
    monkeypatch.setattr(jobs, "BOARD_DIR", tmp_path / "boards")
    monkeypatch.setattr(jobs, "ARB_PATH", tmp_path / "boards" / "arbitrage.json")
    # Pages must never start a real scan in tests.
    monkeypatch.setattr(jobs, "ensure_board", lambda: jobs.status("board"))
    monkeypatch.setattr(jobs, "ensure_arb", lambda: jobs.status("arbitrage"))
    monkeypatch.setattr(jobs, "today", lambda: __import__("datetime").date(2026, 11, 15))
    webapp._hits.clear()
    return webapp


@pytest.fixture
def client(env):
    from starlette.testclient import TestClient

    return TestClient(env.app)


def _csrf(client, path="/register") -> str:
    return re.search(r'name="csrf" value="([^"]+)"', client.get(path).text).group(1)


def register(client, email="ann@example.com", password="correct horse 1", verify=True):
    r = client.post("/register", data={"csrf": _csrf(client), "email": email, "password": password,
                                       "region": "New Jersey", "age": "yes", "terms": "yes"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    if verify:
        link = re.search(r'href="[^"]*(/verify\?token=[^"]+)"', client.get("/app").text).group(1)
        assert client.get(link, follow_redirects=False).status_code == 303
    from web import db

    return db.get_user(email=email)


def write_board(env, rows=None):
    from web import jobs

    rows = rows or [{
        "id": f"p{i}", "player": f"Player {i}", "team": "AAA", "opponent": "BBB", "game_id": "g1", "prop": "points",
        "side": "over", "line": 20.5, "odds": -110, "venue": "Kalshi", "implied": 0.524, "market": 0.5, "market_lo": 0.48,
        "market_hi": 0.52, "venues": 3, "model_own": 0.55, "blended": 0.5, "weight": 0.0, "method": "market_blend",
        "edge": -0.024, "ev_per_100": -4.5, "confidence": "low", "projection": 21.0, "minutes": 32.0, "model_agrees": True,
        "warnings": [], "notes": "note",
    } for i in range(3)]
    jobs.BOARD_DIR.mkdir(parents=True, exist_ok=True)
    board = {"slate": "2026-11-15", "scanned_at": time.time(), "games": [], "pregame_games": 1, "sources": {"kalshi": 3},
             "source_errors": {}, "candidate": {}, "rows": rows}
    (jobs.BOARD_DIR / "2026-11-15.json").write_text(json.dumps(board), encoding="utf-8")
    return board


def post(client, path, **data):
    data.setdefault("csrf", _csrf(client, "/app/account"))
    return client.post(path, data=data, follow_redirects=False)


def signed(payload: dict, secret: str = WEBHOOK_SECRET) -> tuple[bytes, str]:
    body = json.dumps(payload).encode()
    ts = int(time.time())
    sig = hmac.new(secret.encode(), f"{ts}.".encode() + body, hashlib.sha256).hexdigest()
    return body, f"t={ts},v1={sig}"


def send_event(client, event: dict, secret: str = WEBHOOK_SECRET):
    body, sig = signed(event, secret)
    return client.post("/stripe/webhook", content=body, headers={"stripe-signature": sig, "content-type": "application/json"})


def subscription(sub_id="sub_1", customer="cus_1", price="price_pro", status="active", cancel=False, end=None):
    return {"id": sub_id, "object": "subscription", "customer": customer, "status": status, "cancel_at_period_end": cancel,
            "items": {"data": [{"price": {"id": price}, "current_period_end": end or int(time.time()) + 30 * 86400}]}}


# ---------------------------------------------------------------------------
# Public pages and accounts
# ---------------------------------------------------------------------------

def test_public_pages_render_with_disclosures(client):
    for path in ("/", "/pricing", "/performance", "/legal/terms", "/legal/privacy", "/legal/risk", "/legal/subscription",
                 "/legal/refunds", "/legal/acceptable-use", "/legal/contact", "/login", "/register", "/forgot"):
        r = client.get(path)
        assert r.status_code == 200, path
        assert "1-800-GAMBLER" in r.text and "21+" in r.text
    home = client.get("/").text
    assert "guarantee" not in home.lower().replace("nothing here is a guarantee", "")
    assert "Content-Security-Policy" in client.get("/").headers
    assert client.get("/legal/nope").status_code == 404


def test_theme_is_set_before_first_paint_by_a_csp_allowed_script(client):
    import base64

    r = client.get("/pricing")
    script = re.search(r"<script>(.*?)</script>", r.text, re.S).group(1)
    assert "prefers-color-scheme: light" in script and "tp-theme" in script
    digest = "sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = r.headers["Content-Security-Policy"]
    assert f"script-src 'self' '{digest}';" in csp and "unsafe-inline" not in csp
    # It runs before the stylesheet loads, so there is no flash of the other theme.
    assert r.text.index("<script>") < r.text.index('rel="stylesheet" href="/static/app.css"')


def test_structured_data_and_indexing_hints(client):
    from web import db

    home = client.get("/").text
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', home, re.S).group(1))
    assert {d["@type"] for d in ld} == {"WebSite", "Organization", "SoftwareApplication"}
    offers = next(d for d in ld if d["@type"] == "SoftwareApplication")["offers"]
    assert [(o["name"], o["price"]) for o in offers] == [(p["name"], str(p["price"])) for p in db.PLANS.values()]
    assert 'property="og:site_name"' in home and 'name="twitter:card"' in home and "/static/favicon.svg" in home
    for path in ("/", "/pricing", "/performance", "/legal/terms"):
        assert 'name="robots" content="noindex"' not in client.get(path).text, path
    for path in ("/login", "/register", "/forgot", "/legal/nope"):
        assert 'name="robots" content="noindex"' in client.get(path).text, path
    register(client)
    assert 'name="robots" content="noindex"' in client.get("/app").text
    assert client.get("/favicon.ico", follow_redirects=False).headers["location"] == "/static/favicon.svg"
    assert client.get("/static/favicon.svg").status_code == 200


def test_pricing_translates_credits_into_usage(client, env):
    from web import content, db

    page = client.get("/pricing").text
    for key, plan in db.PLANS.items():
        c = plan["credits"]
        assert (f"Up to {c // db.COSTS['board']} full-day boards, or {c // db.COSTS['advisor']} Advisor reviews, "
                f"or {c // db.COSTS['prop']} single lines") in page, key
    assert "no overage fees" in page and "What happens when I run out of credits?" in page
    credits_answer = dict(content.FAQ)["What are credits?"]
    assert all(f"costs {db.COSTS[k]}" in credits_answer for k in ("prop", "board", "advisor", "arbitrage"))


def test_sitemap_dates_only_the_page_whose_change_date_is_known(client, env, monkeypatch, tmp_path):
    import xml.etree.ElementTree as ET

    from web import db

    (tmp_path / "data" / "predictions").mkdir(parents=True)
    (tmp_path / "data" / "models").mkdir(parents=True)
    for d in ("2026-11-04", "2026-11-06"):
        (tmp_path / "data" / "predictions" / f"{d}.graded.json").write_text("{}", encoding="utf-8")
    (tmp_path / "data" / "predictions" / "2026-11-09.json").write_text("[]", encoding="utf-8")   # pregame, not graded
    (tmp_path / "data" / "models" / "market_slope.json").write_text(json.dumps({"fitted_at": "2026-11-05T10:00:00+00:00"}),
                                                                    encoding="utf-8")
    monkeypatch.setattr(db, "ROOT", tmp_path)
    ns = {"s": "http://www.sitemaps.org/schemas/sitemap/0.9"}
    urls = ET.fromstring(client.get("/sitemap.xml").content).findall("s:url", ns)
    dated = {u.find("s:loc", ns).text.split("testserver")[1]: u.find("s:lastmod", ns) for u in urls}
    assert dated["/performance"].text == "2026-11-06"
    assert all(v is None for k, v in dated.items() if k != "/performance") and "/legal/terms" in dated
    (tmp_path / "data" / "models" / "market_slope.json").write_text(json.dumps({"fitted_at": "2026-11-16T01:00:00+00:00"}),
                                                                    encoding="utf-8")
    assert "<lastmod>2026-11-16</lastmod>" in client.get("/sitemap.xml").text
    robots = client.get("/robots.txt").text
    assert "Disallow: /billing" in robots and "Disallow: /app" in robots and "Sitemap: " in robots


def test_paid_plans_show_what_a_board_works_out_to(client):
    from web import db

    page = client.get("/pricing").text
    pro, power = db.PLANS["pro"], db.PLANS["power"]
    per_board = lambda p: "%.2f" % (p["price"] * db.COSTS["board"] / p["credits"])
    assert f"Works out to about ${per_board(pro)} per full-day board</li>" in page
    saving = "%.0f" % (100 - 100 * (power["price"] / power["credits"]) / (pro["price"] / pro["credits"]))
    assert f"Works out to about ${per_board(power)} per full-day board, {saving}% less than on Pro" in page
    assert page.count("Works out to") == 2          # not on Free or Teams


def test_registration_rules(client):
    def attempt(**over):
        data = {"csrf": _csrf(client), "email": "kid@example.com", "password": "long enough 1", "region": "Ohio",
                "age": "yes", "terms": "yes", **over}
        return client.post("/register", data=data, follow_redirects=False)

    assert attempt(age="").status_code == 400            # must confirm 21+
    assert attempt(password="short").status_code == 400
    assert attempt(email="not-an-email").status_code == 400
    assert attempt(region="Atlantis").status_code == 400
    assert attempt().status_code == 303
    assert attempt().status_code == 400                  # email already taken


def test_sign_up_form_has_a_password_toggle_and_keeps_answers_after_an_error(client):
    page = client.get("/register").text
    assert re.search(r'<label for="pw">Password</label>', page)
    assert re.search(r'<input id="pw" type="password"[^>]*aria-describedby="pw-rule"', page)
    # Hidden until app.js runs, so without JavaScript there is no dead button.
    assert re.search(r'<button type="button"[^>]*data-reveal="pw" aria-controls="pw" aria-pressed="false" hidden>Show</button>', page)
    assert "Create free account</button>" in page and "20 free credits every 30 days. No card needed." in page
    assert 'autocapitalize="none" spellcheck="false"' in page

    r = client.post("/register", data={"csrf": _csrf(client), "email": "kim@example.com", "password": "short",
                                       "region": "New Jersey", "age": "yes", "terms": "yes"})
    assert r.status_code == 400 and "<option selected>New Jersey</option>" in r.text and 'value="kim@example.com"' in r.text
    assert "autocomplete=\"current-password\"" in client.get("/login").text


def test_blocked_region_cannot_register(client, monkeypatch):
    monkeypatch.setenv("BLOCKED_REGIONS", "Washington")
    r = client.post("/register", data={"csrf": _csrf(client), "email": "w@example.com", "password": "long enough 1",
                                       "region": "Washington", "age": "yes", "terms": "yes"})
    assert r.status_code == 400 and "not available" in r.text


def test_free_credits_and_verification_gate(client, env):
    from web import db

    user = register(client, verify=False)
    assert db.balance(user["id"]) == 20 and not user["verified_at"]
    write_board(env)
    r = post(client, "/app/prop/p0/unlock")
    assert r.headers["location"] == "/app/account"      # unconfirmed email cannot spend
    assert db.balance(user["id"]) == 20


def test_first_run_checklist_tracks_progress_then_disappears(client, env):
    register(client, verify=False)
    page = client.get("/app").text
    assert "Getting started" in page and "0 of 3 done" in page
    link = re.search(r'href="[^"]*(/verify\?token=[^"]+)"', page).group(1)
    client.get(link)
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    assert "2 of 3 done" in client.get("/app").text
    post(client, "/app/watch", player="Player 0")
    assert "Getting started" not in client.get("/app").text


def test_mail_without_smtp_goes_to_the_outbox_with_the_disclosures(env):
    from web import mailer

    assert mailer.send("a@example.com", "Hello", "Body text") is False
    logged = mailer.OUTBOX.read_text(encoding="utf-8")
    assert "to=a@example.com subject=Hello" in logged and "Body text" in logged
    assert "21+" in logged and "1-800-GAMBLER" in logged


def test_mail_over_smtp_sends_the_footer_and_never_raises(env, monkeypatch):
    import smtplib

    from web import mailer

    sent = []

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            sent.append(("connect", host, port))

        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def starttls(self):
            pass

        def login(self, user, password):
            sent.append(("login", user))

        def send_message(self, msg):
            sent.append(("msg", msg["To"], msg.get_content()))

    monkeypatch.setenv("SMTP_HOST", "smtp.example.com")
    monkeypatch.setenv("SMTP_USER", "mailer")
    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    assert mailer.send("b@example.com", "Hi", "Body") is True
    assert sent[0] == ("connect", "smtp.example.com", 587) and sent[1] == ("login", "mailer")
    assert sent[2][1] == "b@example.com" and "1-800-GAMBLER" in sent[2][2] and "21+" in sent[2][2]

    def down(*a, **k):
        raise OSError("connection refused")

    monkeypatch.setattr(smtplib, "SMTP", down)
    assert mailer.send("b@example.com", "Hi", "Body") is False


def test_login_logout_and_open_redirect(client):
    register(client, verify=False)
    client.post("/logout", data={"csrf": _csrf(client, "/app")})
    assert client.get("/app", follow_redirects=False).status_code == 303
    bad = client.post("/login?next=//evil.example", data={"csrf": _csrf(client, "/login"), "email": "ann@example.com",
                                                          "password": "wrong password"})
    assert bad.status_code == 400
    ok = client.post("/login?next=//evil.example", data={"csrf": _csrf(client, "/login"), "email": "ann@example.com",
                                                         "password": "correct horse 1"}, follow_redirects=False)
    assert ok.headers["location"] == "/app"


def test_old_password_hashes_still_work_and_are_upgraded_at_sign_in(client, env):
    import hashlib
    import secrets

    from web import db

    salt = secrets.token_bytes(16)
    legacy = f"scrypt${salt.hex()}${hashlib.scrypt(b'old password 1', salt=salt, n=2**14, r=8, p=1).hex()}"
    uid = db.create_user("legacy@example.com", "placeholder pw")
    db.update_user(uid, pw_hash=legacy)
    assert db.check_password("old password 1", legacy) and db.needs_rehash(legacy)
    fresh = db.hash_password("new password 1")
    assert fresh.startswith("scrypt$16384$8$5$") and not db.needs_rehash(fresh)

    r = client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "legacy@example.com",
                                    "password": "old password 1"}, follow_redirects=False)
    assert r.status_code == 303
    upgraded = db.get_user(uid)["pw_hash"]
    assert upgraded.startswith("scrypt$16384$8$5$") and db.check_password("old password 1", upgraded)
    # A row edited to demand huge memory is refused instead of computed.
    assert not db.check_password("x", f"scrypt${2**20}$8$1${salt.hex()}$00")


def test_csrf_required_on_every_post(client, env):
    register(client)
    write_board(env)
    for path in ("/app/prop/p0/unlock", "/app/explorer/unlock", "/billing/checkout", "/app/account/delete", "/logout"):
        assert client.post(path, data={"csrf": "forged"}).status_code == 403, path


def test_password_reset_is_single_use_and_ends_sessions(client):
    from web import db

    user = register(client)
    other = __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(client.app)
    other.post("/login", data={"csrf": _csrf(other, "/login"), "email": "ann@example.com", "password": "correct horse 1"})
    assert other.get("/app", follow_redirects=False).status_code == 200
    token = db.make_token(user["id"], "reset", 3600)
    assert client.post("/reset", data={"csrf": _csrf(client, "/forgot"), "token": token, "password": "brand new pass 2"},
                       follow_redirects=False).status_code == 303
    assert client.post("/reset", data={"csrf": _csrf(client, "/forgot"), "token": token, "password": "again again 3"}).status_code == 400
    assert other.get("/app", follow_redirects=False).status_code == 303   # old session signed out
    assert db.check_password("brand new pass 2", db.get_user(user["id"])["pw_hash"])


def test_forgot_password_does_not_reveal_accounts(client):
    register(client, verify=False)
    known = client.post("/forgot", data={"csrf": _csrf(client, "/forgot"), "email": "ann@example.com"}).text
    unknown = client.post("/forgot", data={"csrf": _csrf(client, "/forgot"), "email": "nobody@example.com"}).text
    strip = lambda t: re.sub(r'<p class="flash note">.*?</p>|name="csrf" value="[^"]+"', "", t, flags=re.S)
    assert strip(known) == strip(unknown)


def test_login_rate_limit(client):
    register(client, verify=False)
    codes = [client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "ann@example.com", "password": "nope"}).status_code
             for _ in range(12)]
    assert codes[-1] == 429


def test_login_limit_per_account_survives_changing_networks(client, monkeypatch):
    from web import app as webapp, db

    register(client, verify=False)
    codes = [client.post("/login", headers={"x-forwarded-for": f"10.0.0.{i}"},
                         data={"csrf": _csrf(client, "/login"), "email": "ann@example.com", "password": "nope"}).status_code
             for i in range(12)]
    assert codes[:10] == [400] * 10 and codes[-1] == 429
    other = client.post("/login", headers={"x-forwarded-for": "10.0.1.1"},
                        data={"csrf": _csrf(client, "/login"), "email": "bob@example.com", "password": "nope"})
    assert other.status_code == 400      # other accounts are unaffected

    checked = []
    monkeypatch.setattr(db, "check_password", lambda pw, stored: checked.append(stored) or False)
    client.post("/login", headers={"x-forwarded-for": "10.0.2.1"},
                data={"csrf": _csrf(client, "/login"), "email": "nobody@example.com", "password": "nope"})
    assert checked and checked[0].startswith("scrypt$")    # unknown email still pays for one hash
    assert webapp._dummy_hash


# ---------------------------------------------------------------------------
# Credits
# ---------------------------------------------------------------------------

def test_credits_charge_once_and_never_for_reopening(client, env):
    from web import db

    user = register(client)
    write_board(env)
    page = client.get("/app/prop/p0").text
    assert "Open for 1 credit" in page and "Market consensus" not in page
    assert post(client, "/app/prop/p0/unlock").status_code == 303
    assert db.balance(user["id"]) == 19
    assert "Market consensus" in client.get("/app/prop/p0").text
    post(client, "/app/prop/p0/unlock")                     # double click
    assert db.balance(user["id"]) == 19
    post(client, "/app/explorer/unlock")
    post(client, "/app/explorer/unlock")
    assert db.balance(user["id"]) == 14
    assert "Locked" not in client.get("/app/explorer").text


def test_every_charge_shows_a_receipt(client, env):
    register(client)
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    assert "Charged 1 credit for Player 0 points over 20.5. You have 19 left." in client.get("/app/prop/p0").text
    post(client, "/app/prop/p0/unlock")
    assert "already opened Player 0 points over 20.5. Nothing was charged." in client.get("/app/prop/p0").text
    post(client, "/app/explorer/unlock")
    assert "Charged 5 credits for today&#39;s full board (2026-11-15). You have 14 left." in client.get("/app/explorer").text


def test_customers_see_when_their_credits_reset_and_to_what(client, env):
    from web import app as webapp, db

    user = register(client)
    when = webapp.day(float(user["credits_reset_at"]) + db.PERIOD_SECONDS)
    assert f"Back to 20 on {when}." in client.get("/app").text
    assert f"Your balance becomes 20 on {when}" in client.get("/app/account").text
    assert f"or wait until {when}, when your balance becomes 20" in client.get("/pricing?need=board").text

    end = time.time() + 10 * 86400
    db.update_user(user["id"], plan="pro", sub_status="active", period_end=end)
    assert db.next_reset(db.get_user(user["id"])) == (end, 200)
    db.update_user(user["id"], cancel_at_period_end=1)       # cancelled: drops to the free allowance
    assert db.next_reset(db.get_user(user["id"])) == (end, 20)
    db.update_user(user["id"], period_end=None)
    assert db.next_reset(db.get_user(user["id"])) is None
    assert "Back to" not in client.get("/app").text


def test_insufficient_credits_charge_nothing(client, env):
    from web import db

    user = register(client)
    write_board(env)
    for i in range(4):
        assert db.spend(user["id"], "board", f"other-{i}") == "ok"
    assert db.balance(user["id"]) == 0
    r = post(client, "/app/prop/p1/unlock")
    assert r.headers["location"] == "/pricing?need=prop" and db.balance(user["id"]) == 0
    assert "Nothing was charged" in client.get("/pricing?need=prop").text


def test_concurrent_spending_cannot_overdraw(env):
    from web import db

    uid = db.create_user("race@example.com", "long password 1")
    results = []
    threads = [threading.Thread(target=lambda i=i: results.append(db.spend(uid, "board", f"r{i}"))) for i in range(12)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert results.count("ok") == 4 and db.balance(uid) == 0
    same = []
    db.grant_period(uid, "free", "test-refill")
    threads = [threading.Thread(target=lambda: same.append(db.spend(uid, "prop", "same"))) for _ in range(10)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert same.count("ok") == 1 and db.balance(uid) == 19


def test_refund_only_once_and_grants_idempotent(env):
    from web import db

    uid = db.create_user("r@example.com", "long password 1")
    assert db.spend(uid, "advisor", "run1") == "ok" and db.balance(uid) == 17
    assert db.refund(uid, "advisor", "run1", "failed") and db.balance(uid) == 20
    assert not db.refund(uid, "advisor", "run1", "failed") and db.balance(uid) == 20
    assert db.grant_period(uid, "pro", "inv_1") and db.balance(uid) == 200
    assert not db.grant_period(uid, "pro", "inv_1") and db.balance(uid) == 200
    db.spend(uid, "board", "x")
    assert db.grant_period(uid, "pro", "inv_2") and db.balance(uid) == 200     # no rollover


def test_free_credits_reset_every_30_days(env):
    from web import db

    uid = db.create_user("f@example.com", "long password 1")
    for i in range(4):
        db.spend(uid, "board", f"b{i}")
    db.refresh_free_credits(db.get_user(uid))
    assert db.balance(uid) == 0
    db.execute("UPDATE users SET credits_reset_at=?, created_at=? WHERE id=?", (time.time() - 31 * 86400, time.time() - 31 * 86400, uid))
    db.refresh_free_credits(db.get_user(uid))
    db.refresh_free_credits(db.get_user(uid))
    assert db.balance(uid) == 20


def test_failed_advisor_run_is_refunded(env, monkeypatch):
    from web import db, jobs

    uid = db.create_user("adv@example.com", "long password 1")
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    monkeypatch.setattr(jobs, "board_props", lambda slate: ["prop"])
    import engine.advisor.run as run_mod

    monkeypatch.setattr(run_mod, "run_advisor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("model down")))
    run_id = jobs.start_advisor(uid, "2026-11-15", -250, 250)
    assert db.query("SELECT status FROM advisor_runs WHERE id=?", (run_id,))[0]["status"] == "failed"
    assert db.balance(uid) == 20
    assert any(r["reason"].startswith("Refund") for r in db.history(uid))


def test_advisor_run_with_nothing_to_review_is_refunded(client, env, monkeypatch):
    from types import SimpleNamespace

    import engine.advisor.run as run_mod
    from web import db, jobs

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(jobs, "RUN_INLINE", True)
    monkeypatch.setattr(jobs, "board_props", lambda slate: ["prop"])
    monkeypatch.setattr(run_mod, "run_advisor", lambda *a, **k: SimpleNamespace(ai_status=run_mod.AI_SKIPPED, picks=[]))
    user = register(client)
    write_board(env)
    page = client.get(post(client, "/app/advisor/run", band="favorites").headers["location"]).text
    assert "Nothing to review in that price range" in page and "credits were returned" in page
    assert "Generated by an AI model" not in page
    assert db.balance(user["id"]) == 20
    assert db.query("SELECT status FROM advisor_runs WHERE user_id=?", (user["id"],))[0]["status"] == "empty"
    assert any(r["reason"].startswith("Refund: no lines") for r in db.history(user["id"]))


def test_one_advisor_run_at_a_time_and_double_submit_is_free(env, monkeypatch):
    from web import db, jobs

    uid = db.create_user("busy@example.com", "long password 1")
    monkeypatch.setattr(jobs, "board_props", lambda slate: ["prop"])
    monkeypatch.setattr(jobs, "_start", lambda name, work: True)     # the run stays "running"
    first = jobs.start_advisor(uid, "2026-11-15", -250, 250)
    second = jobs.start_advisor(uid, "2026-11-15", -250, 250)
    assert first == second and db.balance(uid) == 17
    assert len(db.query("SELECT 1 FROM advisor_runs WHERE user_id=?", (uid,))) == 1
    assert jobs.running_advisor(uid) == first


def test_advisor_double_click_says_nothing_was_charged(client, env, monkeypatch):
    from web import db, jobs

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    monkeypatch.setattr(jobs, "board_props", lambda slate: ["prop"])
    monkeypatch.setattr(jobs, "_start", lambda name, work: True)
    user = register(client)
    write_board(env)
    first = post(client, "/app/advisor/run", band="balanced")
    assert "Charged 3 credits" in client.get(first.headers["location"]).text
    again = post(client, "/app/advisor/run", band="balanced")
    assert again.headers["location"] == first.headers["location"]
    assert "still being written. Nothing was charged." in client.get(again.headers["location"]).text
    assert db.balance(user["id"]) == 17


def test_advisor_run_orphaned_by_a_restart_is_refunded(env):
    from web import db, jobs

    uid = db.create_user("orphan@example.com", "long password 1")
    run_id = db.execute("INSERT INTO advisor_runs (user_id, slate, status, created_at) VALUES (?,?,?,?)",
                        (uid, "2026-11-15", "running", time.time() - 3600))
    db.spend(uid, "advisor", str(run_id))
    fresh = db.execute("INSERT INTO advisor_runs (user_id, slate, status, created_at) VALUES (?,?,?,?)",
                       (uid, "2026-11-15", "running", time.time()))
    jobs.reap_advisor_runs(uid)
    jobs.reap_advisor_runs(uid)
    status = {r["id"]: r["status"] for r in db.query("SELECT id, status FROM advisor_runs WHERE user_id=?", (uid,))}
    assert status == {run_id: "failed", fresh: "running"}     # a just-started run is left alone
    assert db.balance(uid) == 20                              # refunded once


# ---------------------------------------------------------------------------
# Billing lifecycle
# ---------------------------------------------------------------------------

def test_subscription_lifecycle(client, env, monkeypatch):
    from web import billing, db

    user = register(client)
    subs = {"sub_1": subscription()}
    monkeypatch.setattr(billing, "fetch_subscription", lambda sid: subs[sid])
    created = {}

    def fake_checkout(u, plan, base):
        created.update(user=u["id"], plan=plan)
        db.update_user(u["id"], stripe_customer_id="cus_1")
        return "https://checkout.stripe.com/c/pay/test"

    monkeypatch.setattr(billing, "create_checkout", fake_checkout)
    r = post(client, "/billing/checkout", plan="pro")
    assert r.headers["location"].startswith("https://checkout.stripe.com/") and created == {"user": user["id"], "plan": "pro"}

    # The success redirect alone grants nothing.
    client.get("/app/account?checkout=done")
    assert db.effective_plan(db.get_user(user["id"])) == "free"

    assert send_event(client, {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "client_reference_id": str(user["id"]), "customer": "cus_1", "subscription": "sub_1"}}}).status_code == 200
    paid = {"id": "evt_2", "type": "invoice.paid", "data": {"object": {"id": "in_1", "subscription": "sub_1", "customer": "cus_1"}}}
    assert send_event(client, paid).json()["result"] == "ok"
    u = db.get_user(user["id"])
    assert db.effective_plan(u) == "pro" and db.balance(u["id"]) == 200
    assert send_event(client, paid).json()["result"] == "duplicate"     # Stripe retries
    assert db.balance(u["id"]) == 200
    assert "Pro" in client.get("/app/account").text

    # Renewal: a new invoice starts a new period without rollover.
    db.spend(u["id"], "board", "day1")
    send_event(client, {"id": "evt_3", "type": "invoice.paid", "data": {"object": {"id": "in_2", "subscription": "sub_1"}}})
    assert db.balance(u["id"]) == 200

    # Failed renewal pauses paid access.
    subs["sub_1"] = subscription(status="past_due")
    send_event(client, {"id": "evt_4", "type": "invoice.payment_failed", "data": {"object": {"id": "in_3", "subscription": "sub_1"}}})
    assert db.effective_plan(db.get_user(u["id"])) == "free"
    subs["sub_1"] = subscription()
    send_event(client, {"id": "evt_5", "type": "customer.subscription.updated", "data": {"object": {"id": "sub_1", "customer": "cus_1"}}})
    assert db.effective_plan(db.get_user(u["id"])) == "pro"

    # Cancel at period end: keeps the plan until it ends.
    subs["sub_1"] = subscription(cancel=True)
    send_event(client, {"id": "evt_6", "type": "customer.subscription.updated", "data": {"object": {"id": "sub_1", "customer": "cus_1"}}})
    u = db.get_user(u["id"])
    assert u["cancel_at_period_end"] and db.effective_plan(u) == "pro"
    assert "will not renew" in client.get("/app/account").text

    ended = subscription(status="canceled", cancel=True)
    send_event(client, {"id": "evt_7", "type": "customer.subscription.deleted", "data": {"object": ended}})
    u = db.get_user(u["id"])
    assert db.effective_plan(u) == "free" and db.balance(u["id"]) == 20


def test_webhook_rejects_spoofed_and_unknown_prices(client, env, monkeypatch):
    from web import billing, db

    user = register(client)
    db.update_user(user["id"], stripe_customer_id="cus_9")
    event = {"id": "evt_x", "type": "invoice.paid", "data": {"object": {"id": "in_x", "subscription": "sub_9"}}}
    assert send_event(client, event, secret="whsec_attacker").status_code == 400
    assert client.post("/stripe/webhook", content=b"{}", headers={"stripe-signature": "t=1,v1=00"}).status_code == 400
    assert db.balance(user["id"]) == 20
    monkeypatch.setattr(billing, "fetch_subscription", lambda sid: subscription("sub_9", "cus_9", price="price_unknown"))
    send_event(client, event)
    assert db.effective_plan(db.get_user(user["id"])) == "free" and db.balance(user["id"]) == 20


def test_checkout_disabled_without_stripe_keys(client, env, monkeypatch):
    register(client)
    monkeypatch.delenv("STRIPE_SECRET_KEY")
    assert "Payments open soon" in client.get("/pricing").text
    assert post(client, "/billing/checkout", plan="pro").headers["location"] == "/pricing"


# ---------------------------------------------------------------------------
# Authorization
# ---------------------------------------------------------------------------

def test_admin_pages_hidden_from_customers(client, env):
    from web import db

    user = register(client)
    assert client.get("/admin").status_code == 404
    assert post(client, "/admin/credits", user_id=str(user["id"]), delta="1000", note="free money").status_code == 404
    assert db.balance(user["id"]) == 20


def test_admin_can_adjust_credits_with_audit(client, env):
    from web import db

    target = db.create_user("cust@example.com", "long password 1")
    register(client, email="boss@example.com")
    assert client.get("/admin").status_code == 200
    assert post(client, "/admin/credits", user_id=str(target), delta="15", note="goodwill").status_code == 303
    assert db.balance(target) == 35
    assert db.query("SELECT action FROM audit WHERE action='credits.adjust'")


def test_customers_cannot_see_each_others_analyses(client, env):
    from web import db

    other = db.create_user("other@example.com", "long password 1")
    run_id = db.execute("INSERT INTO advisor_runs (user_id, slate, status, body, created_at) VALUES (?,?,?,?,?)",
                        (other, "2026-11-15", "done", "secret analysis", time.time()))
    register(client)
    assert client.get(f"/app/advisor/{run_id}").status_code == 404


def test_account_deletion_erases_personal_data(client, env, monkeypatch):
    from web import billing, db

    cancelled = []
    monkeypatch.setattr(billing, "cancel_now", lambda u: cancelled.append(u["id"]))
    user = register(client)
    post(client, "/app/watch", player="Player 0")
    assert post(client, "/app/account/delete", password="wrong").headers["location"] == "/app/account"
    r = post(client, "/app/account/delete", password="correct horse 1")
    assert r.status_code == 200 and "Account deleted" in r.text and cancelled == [user["id"]]
    assert db.get_user(email="ann@example.com") is None
    assert not db.query("SELECT 1 FROM watchlist WHERE user_id=?", (user["id"],))
    assert client.get("/app", follow_redirects=False).status_code == 303


def test_no_secrets_in_pages(client, env, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-live-should-never-show")
    register(client)
    for path in ("/", "/pricing", "/app", "/app/advisor", "/app/account"):
        text = client.get(path).text
        assert "sk-live-should-never-show" not in text and "sk_test_fake" not in text and WEBHOOK_SECRET not in text


def test_explorer_and_arbitrage_render_real_rows(client, env):
    from web import db, jobs

    user = register(client)
    write_board(env)
    db.spend(user["id"], "board", "2026-11-15")
    page = client.get("/app/explorer?prop=points&sort=player").text
    assert "Player 0" in page and "model weight" in page.lower()
    jobs.ARB_PATH.write_text(json.dumps({"arbs": [], "near": [], "maker": [], "scanned_at": time.time(), "seconds": 100, "stats": {
        "kalshi_events": 10, "polymarket_events": 20, "matched_games": 2, "confirmed_pairs": 1, "candidates": 3,
        "depth_checked": 3, "maker_pairs": 0, "book_errors": 0}}), encoding="utf-8")
    page = client.get("/app/arbitrage").text
    assert "Open for 5 credits" in page
    post(client, "/app/arbitrage/unlock")
    assert "None right now" in client.get("/app/arbitrage").text and db.balance(user["id"]) == 10


def test_arbitrage_rows_link_to_both_venues_rules(client, env):
    from web import db, jobs

    user = register(client)
    row = {"title": "Player X points over 20.5 [Kalshi+Polymarket prop]", "legs": ["kalshi: YES", "polymarket: NO"],
           "contracts": 100, "cost": 90.0, "profit": 8.0, "profit_if_slipped": 6.0, "interest": 0.1,
           "closes": "2026-11-16T03:00:00+00:00",
           "urls": ["https://kalshi.com/markets/kxnbapts", "https://polymarket.com/event/nba-x", "javascript:alert(1)"]}
    jobs.ARB_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs.ARB_PATH.write_text(json.dumps({"arbs": [row], "near": [], "maker": [], "scanned_at": time.time(), "seconds": 100,
                                         "stats": {"kalshi_events": 1, "polymarket_events": 1, "matched_games": 0,
                                                   "confirmed_pairs": 0, "candidates": 1}}), encoding="utf-8")
    db.spend(user["id"], "arbitrage", "2026-11-15")
    page = client.get("/app/arbitrage").text
    assert 'href="https://kalshi.com/markets/kxnbapts"' in page and 'href="https://polymarket.com/event/nba-x"' in page
    assert "javascript:" not in page and "Read the settlement rules on" in page


def test_wide_tables_scroll_in_their_own_keyboard_reachable_box(client, env):
    from web import db

    user = register(client)
    write_board(env)
    db.spend(user["id"], "board", "2026-11-15")
    for page in (client.get("/app/explorer").text, client.get("/performance").text):
        assert re.search(r'<div class="table-scroll" role="region" aria-label="[^"]+" tabindex="0">\s*<table class="data', page)


def test_locked_board_does_not_leak_gap_order(client, env):
    from web import db

    user = register(client)
    board = write_board(env)
    for i, r in enumerate(board["rows"]):
        r["player"], r["edge"] = ["Zed", "Amy", "Max"][i], [0.20, -0.05, 0.01][i]
    write_board(env, board["rows"])
    order = lambda t: [n for n in re.findall(r'>(Zed|Amy|Max)</a>', t)]
    assert order(client.get("/app/explorer?sort=edge&backed=1").text) == ["Amy", "Max", "Zed"]   # alphabetical while locked
    db.spend(user["id"], "board", "2026-11-15")
    assert order(client.get("/app/explorer?sort=edge").text) == ["Zed", "Max", "Amy"]           # by gap once paid


def test_tests_never_write_to_the_real_prediction_ledger(tmp_path):
    from datetime import date
    from pathlib import Path

    import engine.prediction_ledger as ledger
    from tests.test_advisor import make_prop

    real = Path(ledger.__file__).resolve().parents[1] / "data" / "predictions"
    path = ledger.save_pregame_predictions(date(2030, 1, 1), [make_prop("Fixture Player")])
    assert path.parent == tmp_path / "predictions" and not (real / "2030-01-01.json").exists()


def test_committed_pregame_ledgers_from_nov_15_hold_no_test_fixtures():
    from pathlib import Path

    folder = Path(__file__).resolve().parents[1] / "data" / "predictions"
    for path in folder.glob("????-??-??.json"):
        if path.name >= "2026-11-15":
            rows = json.loads(path.read_text(encoding="utf-8"))
            assert not [r for r in rows if str(r.get("game_id")) in {"g1", "g2", "g3"}], path.name


def test_prop_row_keeps_every_probability_separate():
    from tests.test_advisor import make_prop
    from web.jobs import prop_row

    p = make_prop("Zero Weight", odds=150, edge=0.02, true_prob=0.42)
    p.market_probability, p.raw_model_probability, p.model_weight = 0.42, 0.30, 0.0
    row = prop_row(p)
    assert row["implied"] == pytest.approx(0.40) and row["market"] == 0.42 and row["blended"] == 0.42
    assert row["model_own"] == 0.30 and row["weight"] == 0.0 and row["model_agrees"] is False
    assert row["ev_per_100"] == pytest.approx(100 * (0.42 * 2.5 - 1))


def test_arbitrage_shows_both_close_dates_and_flags_reviewed_pairs_with_different_deadlines(client, env):
    from web import db, jobs

    user = register(client)
    base = {"legs": ["kalshi: YES", "polymarket: NO"], "contracts": 100, "cost": 90.0, "profit": 8.0,
            "profit_if_slipped": 6.0, "interest": 0.1, "urls": []}
    game = {**base, "title": "A vs B [Kalshi+Polymarket]: A", "closes": "2026-11-29T00:00:00+00:00", "close_gap_days": 13.0,
            "close_by_venue": {"Kalshi": "2026-11-29T00:00:00+00:00", "Polymarket": "2026-11-16T00:00:00+00:00"}}
    reviewed = {**base, "title": "Leaves office? [confirmed pair]", "closes": "2027-12-31T00:00:00+00:00", "close_gap_days": 365.0,
                "close_by_venue": {"Kalshi": "2027-12-31T00:00:00+00:00", "Polymarket": "2026-12-31T00:00:00+00:00"}}
    same_day = {**base, "title": "C vs D [Kalshi+Polymarket]: C", "closes": "2026-11-16T05:00:00+00:00", "close_gap_days": 0.1,
                "close_by_venue": {"Kalshi": "2026-11-16T05:00:00+00:00", "Polymarket": "2026-11-16T03:00:00+00:00"}}
    jobs.ARB_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs.ARB_PATH.write_text(json.dumps({"arbs": [game, reviewed], "near": [same_day], "maker": [], "scanned_at": time.time(),
                                         "seconds": 100, "stats": {"kalshi_events": 1, "polymarket_events": 1, "matched_games": 1,
                                                                   "confirmed_pairs": 1, "candidates": 3}}), encoding="utf-8")
    db.spend(user["id"], "arbitrage", "2026-11-15")
    page = client.get("/app/arbitrage").text
    assert "Kalshi 2026-11-29, Polymarket 2026-11-16" in page
    assert "Kalshi 2027-12-31, Polymarket 2026-12-31" in page
    assert page.count("Deadlines 365 days apart") == 1 and "Deadlines 13 days" not in page
    assert "Kalshi 2026-11-16" not in page        # same calendar day: one date only
