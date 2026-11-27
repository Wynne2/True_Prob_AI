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
from pathlib import Path

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
    if "new" in data and "new2" not in data:
        data["new2"] = data["new"]  # confirm matches unless a test sets new2 on purpose
    return client.post(path, data=data, follow_redirects=False)


def reset_post(client, token, password, password2=None):
    return client.post("/reset", data={"csrf": _csrf(client, "/forgot"), "token": token, "password": password,
                                       "password2": password if password2 is None else password2},
                       follow_redirects=False)


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
    assert 'href="/register">Create a free account</a>' in home and 'href="/app">Open the app</a>' not in home
    assert "Content-Security-Policy" in client.get("/").headers
    assert client.get("/legal/nope").status_code == 404


def test_missing_pages_offer_ways_back_into_the_product(client):
    page = client.get("/nope").text
    assert "Page not found" in page
    assert 'href="/performance"' in page and 'href="/pricing"' in page and 'href="/login">Sign in</a>' in page
    register(client, verify=False)
    signed = client.get("/nope").text
    assert 'href="/app">Open the app</a>' in signed and 'href="/login">Sign in</a>' not in signed


def test_signed_in_home_page_opens_the_app_instead_of_sign_up(client):
    register(client)
    home = client.get("/").text
    assert 'href="/app">Open the app</a>' in home
    assert 'href="/register">Create a free account</a>' not in home
    assert "free credits every 30 days" not in home


def test_theme_is_set_before_first_paint_by_a_csp_allowed_script(client):
    import base64

    r = client.get("/pricing")
    script = re.search(r"<script>(.*?)</script>", r.text, re.S).group(1)
    assert "prefers-color-scheme: light" in script and "tp-theme" in script
    digest = "sha256-" + base64.b64encode(hashlib.sha256(script.encode()).digest()).decode()
    csp = r.headers["Content-Security-Policy"]
    assert f"script-src 'self' '{digest}';" in csp and "unsafe-inline" not in csp
    assert "object-src 'none'" in csp  # OWASP: block plugins / unexpected embeds
    # It runs before the stylesheet loads, so there is no flash of the other theme.
    assert r.text.index("<script>") < r.text.index('rel="stylesheet" href="/static/app.css"')


def test_structured_data_and_indexing_hints(client):
    from web import content, db

    home = client.get("/").text
    ld = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', home, re.S).group(1))
    assert {d["@type"] for d in ld} == {"WebSite", "Organization", "SoftwareApplication", "FAQPage"}
    org = next(d for d in ld if d["@type"] == "Organization")
    assert org["description"] and "21+" in org["description"] and "not betting or investment advice" in org["description"]
    app = next(d for d in ld if d["@type"] == "SoftwareApplication")
    assert app["description"] == org["description"]
    offers = app["offers"]
    assert [(o["name"], o["price"]) for o in offers] == [(p["name"], str(p["price"])) for p in db.PLANS.values()]
    faq = next(d for d in ld if d["@type"] == "FAQPage")["mainEntity"]
    assert [(q["name"], q["acceptedAnswer"]["text"]) for q in faq] == list(content.FAQ)
    assert 'property="og:site_name"' in home and 'name="twitter:card"' in home and "/static/favicon.svg" in home
    for path in ("/", "/pricing", "/performance", "/legal/terms"):
        assert 'name="robots" content="noindex"' not in client.get(path).text, path
    for path in ("/login", "/register", "/forgot", "/legal/nope"):
        assert 'name="robots" content="noindex"' in client.get(path).text, path
    register(client)
    assert 'name="robots" content="noindex"' in client.get("/app").text
    assert client.get("/favicon.ico", follow_redirects=False).headers["location"] == "/static/favicon.svg"
    assert client.get("/static/favicon.svg").status_code == 200


def test_every_legal_page_has_its_own_meta_description(client):
    from web import app as webapp, content

    assert set(webapp.LEGAL_DESCRIPTIONS) == set(content.LEGAL)
    seen = set()
    for slug in content.LEGAL:
        page = client.get(f"/legal/{slug}").text
        desc = re.search(r'<meta name="description" content="([^"]+)">', page).group(1)
        og = re.search(r'<meta property="og:description" content="([^"]+)">', page).group(1)
        assert desc == og and desc.endswith("Draft awaiting review by a lawyer."), slug
        seen.add(desc)
    assert len(seen) == len(content.LEGAL)                  # unique per page
    home = re.search(r'<meta name="description" content="([^"]+)">', client.get("/").text).group(1)
    assert home not in seen and "Draft" not in home


def test_pricing_translates_credits_into_usage(client, env):
    from web import content, db

    page = client.get("/pricing").text
    for key, plan in db.PLANS.items():
        c = plan["credits"]
        assert (f"Up to {c // db.COSTS['board']:,} full-day boards, or {c // db.COSTS['advisor']:,} Advisor reviews, "
                f"or {c // db.COSTS['prop']:,} single lines") in page, key
    assert "no overage fees" in page and "What happens when I run out of credits?" in page
    credits_answer = dict(content.FAQ)["What are credits?"]
    assert all(f"costs {db.COSTS[k]}" in credits_answer for k in ("prop", "board", "advisor", "arbitrage"))


def test_credit_counts_of_a_thousand_or_more_have_a_thousands_separator(client, env):
    from web import db

    page = client.get("/pricing").text
    assert "1,000 credits per billing period" in page and "or 1,000 single lines" in page and "1000" not in page
    assert "1,000 credits each month" in client.get("/").text
    user = register(client)
    db.adjust_credits(user["id"], user["id"], 1214, "test top-up")
    for path in ("/app", "/app/account", "/app/advisor"):
        text = client.get(path).text
        assert "1,234" in text and ">1234<" not in text and ">+1214<" not in text, path
    assert '<td class="num pos">+1,214</td><td class="num">1,234</td>' in client.get("/app/account").text
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    assert "You have 1,233 left." in client.get("/app/prop/p0").text


def test_teams_card_needs_a_real_support_address(client, monkeypatch):
    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    page = client.get("/pricing").text
    assert "<h2>Teams</h2>" not in page and "example.com" not in page
    monkeypatch.setenv("SUPPORT_EMAIL", "help@trueprob.example")
    page = client.get("/pricing").text
    assert "<h2>Teams</h2>" in page and 'href="mailto:help@trueprob.example?subject=' in page


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
    assert "Disallow: /login" in robots and "Disallow: /register" in robots and "Disallow: /forgot" in robots
    assert "Disallow: /reset" in robots and "Disallow: /verify" in robots  # capability-URL paths, not tokens


def _graded(path, rows, decided, games=2):
    path.write_text(json.dumps({"summary": {"final_games": games, "all": {"decided": decided}, "positive_edge": {}},
                                "rows": rows}), encoding="utf-8")


def test_performance_totals_pool_every_graded_line_on_the_same_rows(client, monkeypatch, tmp_path):
    from web import app as webapp, db

    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    row = lambda model, market, result, edge=0.0, roi=None: {"model_probability": model, "market_probability": market,
                                                             "result": result, "edge": edge, "roi": roi}
    _graded(preds / "2026-11-01.graded.json", [row(0.6, None, "win", 0.1, 1.0)], 1)       # no market price
    _graded(preds / "2026-11-02.graded.json", [row(0.5, 0.4, "loss", 0.05, -1.0), row(0.7, 0.8, "win"),
                                               row(0.5, 0.5, "void", 0.2)], 2, games=3)
    _graded(preds / "2026-11-03.graded.json", [row(0.9, 0.9, "win", 0.3, 0.5)], 0)        # nothing decided: left out
    monkeypatch.setattr(db, "ROOT", tmp_path)
    t = webapp._performance()["total"]
    assert (t["decided"], t["with_market"], t["games"], t["flagged"]) == (3, 2, 5, 2)
    assert t["brier_model"] == pytest.approx((0.25 + 0.09) / 2) and t["brier_market"] == pytest.approx((0.16 + 0.04) / 2)
    assert t["flagged_hit_rate"] == 0.5 and t["flagged_roi"] == 0.0
    page = client.get("/performance").text
    assert "All 2 dates" in page and "0.1700" in page and "0.1000" in page and '<td class="num ">0.0%</td>' in page
    assert "only the 2 of 3 lines that had a market price" in page
    monkeypatch.setattr(db, "ROOT", tmp_path / "empty")
    assert webapp._performance()["total"] is None and "All 0 dates" not in client.get("/performance").text


def test_a_missing_or_zero_return_is_not_coloured_as_a_gain_or_loss(client, env, monkeypatch, tmp_path):
    from web import app as webapp, db

    assert webapp.ret(None) == webapp.NO_DATA and "no data" in str(webapp.ret(None))
    assert [webapp.ret(v) for v in (0.0, -0.0004, 0.123, -0.192)] == ["0.0%", "0.0%", "+12.3%", "-19.2%"]
    assert [webapp.ret_tone(v) for v in (None, 0.0, -0.0004, 0.123, -0.192)] == ["", "", "", "pos", "neg"]
    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    _graded(preds / "2026-11-01.graded.json", [{"model_probability": 0.6, "market_probability": 0.5, "result": "win"}], 1)
    monkeypatch.setattr(db, "ROOT", tmp_path)
    register(client)
    for page in (client.get("/performance").text, client.get("/app").text):   # no flagged line, so no return
        assert 'class="num ">–<span class="visually-hidden"> no data</span></td>' in page
        assert 'class="num pos">–' not in page


def test_empty_table_cells_name_the_missing_value_for_screen_readers(client, env, monkeypatch, tmp_path):
    from web import app as webapp, db

    assert webapp.pct(None) == webapp.when(None) == webapp.day(None) == webapp.score(None) == webapp.NO_DATA
    assert webapp.score(0.2144) == "0.2144"
    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    _graded(preds / "2026-11-01.graded.json", [{"model_probability": 0.6, "result": "win"}], 1)  # no market → empty Brier
    monkeypatch.setattr(db, "ROOT", tmp_path)
    page = client.get("/performance").text
    assert page.count('visually-hidden"> no data</span>') >= 2  # our Brier and market Brier on the date row


def test_performance_lists_saved_boards_that_are_not_graded_yet(client, monkeypatch, tmp_path):
    from web import app as webapp, db

    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    _graded(preds / "2026-11-01.graded.json", [{"model_probability": 0.5, "market_probability": 0.5, "result": "win"}], 1)
    (preds / "2026-11-01.json").write_text("[{}]", encoding="utf-8")                     # graded: not pending
    (preds / "2026-11-03.json").write_text(json.dumps([{}] * 1234), encoding="utf-8")
    (preds / "2026-11-03.closing.json").write_text("[]", encoding="utf-8")               # closing file is not a board
    monkeypatch.setattr(db, "ROOT", tmp_path)
    assert webapp._performance()["pending"] == [{"date": "2026-11-03", "lines": 1234}]
    page = client.get("/performance").text
    assert "Saved, not graded yet" in page and "2026-11-03: 1,234 lines saved before tip-off" in page
    _graded(preds / "2026-11-03.graded.json", [], 0, games=0)                            # graded with no final games
    assert webapp._performance()["pending"] == [{"date": "2026-11-03", "lines": None, "no_finals": True}]
    assert "2026-11-03: graded, but no game had a final box score" in client.get("/performance").text
    _graded(preds / "2026-11-03.graded.json", [{"model_probability": 0.5, "market_probability": 0.5, "result": "loss"}], 1)
    assert webapp._performance()["pending"] == [] and "Saved, not graded yet" not in client.get("/performance").text


def test_performance_says_how_fresh_the_record_is_at_the_top(client, monkeypatch, tmp_path):
    from web import db

    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    for d in ("2026-11-01", "2026-11-04"):
        _graded(preds / f"{d}.graded.json", [{"model_probability": 0.5, "market_probability": 0.5, "result": "win"}], 1)
    monkeypatch.setattr(db, "ROOT", tmp_path)
    page = client.get("/performance").text
    assert "Newest graded board: <strong>2026-11-04</strong>." in page and 'href="#pending"' not in page
    (preds / "2026-11-05.json").write_text("[{}]", encoding="utf-8")
    page = client.get("/performance").text
    assert '<a href="#pending">1 saved board</a> is not in the numbers yet.' in page and 'id="pending"' in page
    (preds / "2026-11-06.json").write_text("[{}]", encoding="utf-8")
    assert '<a href="#pending">2 saved boards</a> are not in the numbers yet.' in client.get("/performance").text
    monkeypatch.setattr(db, "ROOT", tmp_path / "empty")
    assert "Newest graded board" not in client.get("/performance").text


def test_each_graded_date_downloads_every_line_unedited(client):
    import csv as _csv

    page = client.get("/performance").text
    assert 'href="/performance/2026-11-06.csv"' in page
    graded = json.loads((Path(__file__).resolve().parents[1] / "data" / "predictions" / "2026-11-06.graded.json").read_text())
    r = client.get("/performance/2026-11-06.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    rows = list(_csv.DictReader(r.text.lstrip("\ufeff").splitlines()))
    assert len(rows) == len(graded["rows"])
    first = graded["rows"][0]
    assert rows[0]["player"] == first["player"] and rows[0]["result"] == first["result"]
    assert float(rows[0]["market_probability"]) == first["market_probability"]
    for bad in ("/performance/2026-11-15.csv", "/performance/..%2Fapp.csv", "/performance/latest.csv"):
        assert client.get(bad).status_code == 404, bad


def test_every_graded_line_downloads_in_one_file_with_its_date(client):
    import csv as _csv

    assert 'href="/performance/all.csv"' in client.get("/performance").text
    r = client.get("/performance/all.csv")
    assert r.status_code == 200 and "true-prob-ai-graded-all-dates.csv" in r.headers["content-disposition"]
    assert r.content.startswith(b"\xef\xbb\xbf")
    rows = list(_csv.DictReader(r.text.lstrip("\ufeff").splitlines()))
    assert list(rows[0])[0] == "date"
    folder = Path(__file__).resolve().parents[1] / "data" / "predictions"
    for path in sorted(folder.glob("????-??-??.graded.json")):
        date = path.name[:10]
        per_date = list(_csv.DictReader(client.get(f"/performance/{date}.csv").text.lstrip("\ufeff").splitlines()))
        assert [{k: v for k, v in row.items() if k != "date"} for row in rows if row["date"] == date] == per_date, date
    assert len(rows) == sum(len(json.loads(p.read_text())["rows"] or []) for p in folder.glob("????-??-??.graded.json"))


def test_unchanged_graded_csvs_answer_a_repeat_download_with_304(client, monkeypatch, tmp_path):
    from web import db

    for path in ("/performance/all.csv", "/performance/2026-11-06.csv"):
        first = client.get(path)
        tag = first.headers["etag"]
        assert first.status_code == 200 and first.headers["cache-control"] == "no-cache" and tag.startswith('"')
        again = client.get(path, headers={"If-None-Match": f'"other", W/{tag}'})
        assert again.status_code == 304 and again.content == b"" and again.headers["etag"] == tag
        assert client.get(path, headers={"If-None-Match": '"stale"'}).text == first.text
    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    _graded(preds / "2026-11-06.graded.json", [{"player": "A", "result": "win"}], 1)
    monkeypatch.setattr(db, "ROOT", tmp_path)
    regraded = client.get("/performance/2026-11-06.csv", headers={"If-None-Match": tag})
    assert regraded.status_code == 200 and regraded.headers["etag"] != tag and "A," in regraded.text


def test_performance_page_describes_its_csv_files_as_a_dataset(client, monkeypatch, tmp_path):
    from web import db

    page = client.get("/performance").text
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
    assert data["@type"] == "Dataset" and 50 <= len(data["description"]) <= 5000 and data["name"]
    assert data["temporalCoverage"] == "2026-10-31/2026-11-06" and data["isAccessibleForFree"] is True
    urls = [d["contentUrl"] for d in data["distribution"]]
    assert urls[-1] == "http://testserver/performance/2026-11-06.csv" and "license" not in data
    for u in urls:                                            # every advertised file really downloads
        assert client.get(u.replace("http://testserver", "")).status_code == 200
    monkeypatch.setattr(db, "ROOT", tmp_path)
    assert "application/ld+json" not in client.get("/performance").text


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
    # web.dev / Chromium: new-password on sign-up, current-password on sign-in (same ids as Account).
    assert re.search(r'<label for="new-password">Password</label>', page)
    assert re.search(r'<input id="new-password" type="password"[^>]*aria-describedby="pw-rule"', page)
    # Hidden until app.js runs, so without JavaScript there is no dead button.
    assert re.search(r'<button type="button"[^>]*data-reveal="new-password" aria-controls="new-password" aria-pressed="false" hidden>Show</button>', page)
    assert "Create free account</button>" in page and "20 free credits every 30 days. No card needed." in page
    assert 'autocapitalize="none" spellcheck="false"' in page

    r = client.post("/register", data={"csrf": _csrf(client), "email": "kim@example.com", "password": "short",
                                       "region": "New Jersey", "age": "yes", "terms": "yes"})
    assert r.status_code == 400 and "<option selected>New Jersey</option>" in r.text and 'value="kim@example.com"' in r.text
    login = client.get("/login").text
    assert 'id="current-password"' in login and 'autocomplete="current-password"' in login
    assert 'autocomplete="username"' in login  # password managers; type stays email
    assert 'autocomplete="email"' in client.get("/register").text and 'autocomplete="username"' not in client.get("/register").text
    # Forgot is account recovery: same username token so managers fill the stored address (web.dev sign-in forms).
    assert 'autocomplete="username"' in client.get("/forgot").text and 'autocomplete="email"' not in client.get("/forgot").text
    reset = client.get("/reset?token=abc").text
    assert 'id="new-password"' in reset and 'id="new-password-confirm"' in reset
    assert 'data-reveal="new-password-confirm"' in reset


def test_auth_errors_are_tied_to_the_fields_that_failed(client):
    bad = client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "nobody@example.com", "password": "wrong guess"})
    assert bad.status_code == 400
    assert 'id="form-error"' in bad.text and 'role="alert"' in bad.text
    assert re.search(r'name="email"[^>]*aria-invalid="true"[^>]*aria-describedby="form-error"', bad.text)
    assert re.search(r'id="current-password"[^>]*aria-describedby="form-error"[^>]*aria-invalid="true"', bad.text)
    reg = client.post("/register", data={"csrf": _csrf(client), "email": "kim@example.com", "password": "short",
                                         "region": "New Jersey", "age": "yes", "terms": "yes"})
    assert 'aria-describedby="pw-rule form-error"' in reg.text and 'aria-invalid="true"' in reg.text
    assert 'id="new-password"' in reg.text
    assert 'aria-invalid' not in client.get("/login").text  # clean form is not marked invalid


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


def test_reopening_a_used_confirmation_link_says_the_email_is_already_confirmed(client, env):
    from starlette.testclient import TestClient

    register(client, verify=False)
    link = re.search(r'href="[^"]*(/verify\?token=[^"]+)"', client.get("/app").text).group(1)
    scanner = TestClient(env.app)                     # e.g. a mail filter that opens links first
    assert scanner.get(link, follow_redirects=False).headers["location"] == "/login"
    r = client.get(link, follow_redirects=False)
    assert r.headers["location"] == "/app"
    page = client.get("/app").text
    assert "Your email address is already confirmed." in page and "invalid or was already used" not in page
    stranger = TestClient(env.app).get(link)
    assert "already confirmed. Sign in to continue." in stranger.text
    bad = client.get("/verify?token=made-up", follow_redirects=False)
    assert bad.headers["location"] == "/app" and "already confirmed" in client.get("/app").text  # signed in and confirmed
    assert "invalid or was already used" in TestClient(env.app).get("/verify?token=made-up").text


def test_first_run_checklist_tracks_progress_then_disappears(client, env):
    register(client, verify=False)
    page = client.get("/app").text
    assert "Getting started" in page and "0 of 3 done" in page
    assert "Nothing opened yet." in page and 'href="/app/explorer"' in page
    link = re.search(r'href="[^"]*(/verify\?token=[^"]+)"', page).group(1)
    client.get(link)
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    page = client.get("/app").text
    assert "2 of 3 done" in page
    assert "Nothing opened yet." not in page  # first unlock removes the nudge
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


def test_login_form_keeps_next_in_a_hidden_field(client):
    register(client, verify=False)
    client.post("/logout", data={"csrf": _csrf(client, "/app")})
    form = client.get("/login?next=/app/account").text
    assert 'action="/login"' in form
    assert 'name="next" value="/app/account"' in form
    # POST to /login with no query still honours the hidden field; open redirects stay blocked.
    ok = client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "ann@example.com",
                                     "password": "correct horse 1", "next": "/app/explorer"}, follow_redirects=False)
    assert ok.headers["location"] == "/app/explorer"
    client.post("/logout", data={"csrf": _csrf(client, "/app")})
    blocked = client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "ann@example.com",
                                          "password": "correct horse 1", "next": "//evil.example"}, follow_redirects=False)
    assert blocked.headers["location"] == "/app"


def test_signing_out_tells_the_browser_to_drop_cached_pages(client):
    register(client, verify=False)
    r = client.post("/logout", data={"csrf": _csrf(client, "/app")}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["clear-site-data"] == '"cache"'
    assert client.get("/app", follow_redirects=False).status_code == 303


def test_idle_session_ends_after_a_week_without_activity(client, monkeypatch):
    """Cookie max-age is 14 days; a week of silence ends the session on the server."""
    from web import app as webapp

    register(client, verify=False)
    clock = {"now": time.time()}
    monkeypatch.setattr(webapp.time, "time", lambda: clock["now"])
    assert client.get("/app").status_code == 200
    clock["now"] += webapp.IDLE_SECONDS - 60
    assert client.get("/app").status_code == 200  # still inside the idle window; seen refreshes
    clock["now"] += webapp.IDLE_SECONDS + 60
    gone = client.get("/app", follow_redirects=False)
    assert gone.status_code == 303 and gone.headers["location"].startswith("/login")
    login = client.get("/login")
    assert login.status_code == 200 and "week without activity" in login.text
    assert client.get("/app", follow_redirects=False).status_code == 303  # still signed out


def test_signed_in_visitors_skip_the_sign_in_and_sign_up_forms(client):
    register(client, verify=False)
    assert client.get("/login", follow_redirects=False).headers["location"] == "/app"
    assert client.get("/login?next=/app/account", follow_redirects=False).headers["location"] == "/app/account"
    assert client.get("/login?next=//evil.example", follow_redirects=False).headers["location"] == "/app"
    assert client.get("/register", follow_redirects=False).headers["location"] == "/app"
    assert client.get("/forgot", follow_redirects=False).headers["location"] == "/app"
    client.post("/logout", data={"csrf": _csrf(client, "/app")})
    assert client.get("/login", follow_redirects=False).status_code == 200
    assert client.get("/register", follow_redirects=False).status_code == 200
    assert client.get("/forgot", follow_redirects=False).status_code == 200


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
    assert reset_post(client, token, "brand new pass 2").status_code == 303
    assert reset_post(client, token, "again again 3").status_code == 400
    assert other.get("/app", follow_redirects=False).status_code == 303   # old session signed out
    assert db.check_password("brand new pass 2", db.get_user(user["id"])["pw_hash"])


def test_sign_out_other_devices_keeps_this_one(client):
    from web import db

    user = register(client)
    other = __import__("starlette.testclient", fromlist=["TestClient"]).TestClient(client.app)
    other.post("/login", data={"csrf": _csrf(other, "/login"), "email": "ann@example.com", "password": "correct horse 1"})
    assert "Sign out other devices" in client.get("/app/account").text
    post(client, "/app/account/sessions", password="wrong password")
    assert "No device was signed out" in client.get("/app/account").text
    assert other.get("/app", follow_redirects=False).status_code == 200
    assert post(client, "/app/account/sessions", password="correct horse 1").status_code == 303
    assert "Every other device and browser was signed out" in client.get("/app/account").text
    assert other.get("/app", follow_redirects=False).status_code == 303
    assert client.get("/app", follow_redirects=False).status_code == 200
    assert db.query("SELECT 1 FROM audit WHERE actor_id=? AND action='sessions.revoke_others'", (user["id"],))
    assert client.post("/app/account/sessions", data={"csrf": "forged", "password": "correct horse 1"}).status_code == 403


def test_password_re_entry_on_account_is_capped_per_account(client):
    from web import db

    user = register(client)
    for path, field in (("/app/account/password", "current"), ("/app/account/sessions", "password"),
                        ("/app/account/delete", "password")):
        assert post(client, path, **{field: "wrong guess", "new": "brand new pass 2"}).status_code == 303
    for _ in range(2):
        assert post(client, "/app/account/sessions", password="wrong guess").status_code == 303
    blocked = post(client, "/app/account/password", current="correct horse 1", new="brand new pass 2")
    assert blocked.status_code == 429 and blocked.headers["retry-after"] == "900" and "Nothing was changed" in blocked.text
    assert db.check_password("correct horse 1", db.get_user(user["id"])["pw_hash"])
    assert post(client, "/app/account/delete", password="correct horse 1").status_code == 429
    assert db.get_user(user["id"]) is not None
    assert len(db.query("SELECT 1 FROM audit WHERE actor_id=? AND action='reauth.failed'", (user["id"],))) == 5
    assert "Wrong password entered on this page" in client.get("/app/account").text


def test_correct_password_re_entries_do_not_count_toward_the_cap(client):
    register(client)
    for i in range(8):
        assert post(client, "/app/account/sessions", password="correct horse 1").status_code == 303


def test_forgot_password_does_not_reveal_accounts(client):
    register(client, verify=False)
    known = client.post("/forgot", data={"csrf": _csrf(client, "/forgot"), "email": "ann@example.com"}).text
    unknown = client.post("/forgot", data={"csrf": _csrf(client, "/forgot"), "email": "nobody@example.com"}).text
    strip = lambda t: re.sub(r'<p class="flash note">.*?</p>|name="csrf" value="[^"]+"', "", t, flags=re.S)
    assert strip(known) == strip(unknown)


def test_login_rate_limit(client):
    register(client, verify=False)
    replies = [client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "ann@example.com", "password": "nope"})
               for _ in range(12)]
    assert replies[-1].status_code == 429
    assert replies[-1].headers["retry-after"] == "600" and replies[-1].headers["cache-control"] == "no-store"
    assert "1-800-GAMBLER" in replies[-1].text and "retry-after" not in replies[0].headers


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


def test_rate_limit_store_forgets_idle_keys(env, monkeypatch):
    clock = [1_000_000.0]
    monkeypatch.setattr(env.time, "time", lambda: clock[0])
    env._swept[0] = clock[0]
    for i in range(500):                      # e.g. guesses spread over many made-up emails
        env._over(f"login-account:{i}@example.com", 10, 900)
    assert env._over("login:1.2.3.4", 1, 3600) is False
    assert env._over("login:1.2.3.4", 1, 3600) is True
    clock[0] += env.LONGEST_WINDOW - 10
    env._over("other", 10, 60)                # sweep runs, but nothing is old enough yet
    assert len(env._hits) == 502
    clock[0] += 400
    env._over("other", 10, 60)
    assert set(env._hits) == {"other"}        # idle keys are gone; limits still work afterwards
    assert env._over("login:1.2.3.4", 1, 3600) is False


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


def test_single_line_pages_say_when_the_board_costs_less(client, env):
    register(client)
    write_board(env)
    page = client.get("/app/prop/p0").text
    assert "Opening 5 or more lines today?" in page and "the same as 5 single lines, and opens all 3" in page
    assert "You have opened" not in client.get("/app/explorer").text
    post(client, "/app/prop/p0/unlock")
    post(client, "/app/prop/p1/unlock")
    assert ("You have opened 2 single lines today (2 credits). Those are not counted toward the board, so it only saves "
            "credits if you will open more than 5 other lines today.") in client.get("/app/explorer").text
    post(client, "/app/explorer/unlock")
    assert "You have opened" not in client.get("/app/explorer").text
    assert "Opening 5 or more" not in client.get("/app/prop/p2").text


def test_credit_history_downloads_as_csv_with_running_balance(client, env):
    import csv as _csv
    from web import db

    user = register(client)
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    db.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
               (user["id"], 2, '=HYPERLINK("http://evil")', "x", time.time()))
    register(client.__class__(client.app), email="bob@example.com")
    assert 'href="/app/account/history.csv"' in client.get("/app/account").text
    r = client.get("/app/account/history.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    assert r.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    assert r.headers["etag"].startswith('"') and len(r.headers["etag"]) == 34
    assert client.get("/app/account/history.csv", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    rows = list(_csv.reader(r.text.lstrip("\ufeff").splitlines()))
    assert rows[0] == ["When (UTC)", "What", "Credits", "Balance after"]
    assert [(w, c, b) for _, w, c, b in rows[1:]] == [
        ("Free plan credits", "20", "20"), ("Analysis: Player 0 points over 20.5", "-1", "19"),
        ("'=HYPERLINK(\"http://evil\")", "2", "21")]          # formula neutralised; other customers' rows absent
    signed_out = client.__class__(client.app).get("/app/account/history.csv", follow_redirects=False)
    assert signed_out.status_code == 303


def test_account_history_shows_the_balance_after_each_row(client, env):
    from web import db

    user = register(client)
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    post(client, "/app/explorer/unlock")
    db.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
               (user["id"], 3, "Adjustment by support: test", "admin:1", time.time()))
    page = client.get("/app/account").text
    assert '<th scope="col">Balance after</th>' in page
    cells = re.findall(r'<tr><td>[^<]+</td><td>([^<]+)</td><td class="num[^"]*">([^<]+)</td><td class="num">(-?\d+)</td></tr>', page)
    assert cells == [("Adjustment by support: test", "+3", "17"), ("Full board for 2026-11-15", "-5", "14"),
                     ("Analysis: Player 0 points over 20.5", "-1", "19"), ("Free plan credits", "+20", "20")]
    assert cells[0][2] == str(db.balance(user["id"]))


def test_customers_see_when_their_credits_reset_and_to_what(client, env):
    from web import app as webapp, db

    user = register(client)
    when = webapp.day(float(user["credits_reset_at"]) + db.PERIOD_SECONDS)
    assert f"Back to 20 on {when}." in client.get("/app").text
    assert f"Your balance becomes 20 on {when}" in client.get("/app/account").text
    assert f"or wait until {when}, when your balance becomes 20" in client.get("/pricing?need=board").text
    assert f"Your balance becomes 20 on {when} (on your first visit from that day)." in client.get("/app").text
    assert "30 days after the last reset" not in client.get("/app").text

    end = time.time() + 10 * 86400
    db.update_user(user["id"], plan="pro", sub_status="active", period_end=end)
    assert db.next_reset(db.get_user(user["id"])) == (end, 200)
    db.update_user(user["id"], cancel_at_period_end=1)       # cancelled: drops to the free allowance
    assert db.next_reset(db.get_user(user["id"])) == (end, 20)
    db.update_user(user["id"], period_end=None)
    assert db.next_reset(db.get_user(user["id"])) is None
    assert "Back to" not in client.get("/app").text


def test_free_allowance_copy_comes_from_the_plan(client, env, monkeypatch):
    from web import db

    monkeypatch.setitem(db.PLANS, "free", {**db.PLANS["free"], "credits": 25})
    home = client.get("/").text
    assert "25 free credits every 30 days. No card needed. 21+." in home and "a month. No card" not in home


def test_pricing_search_description_comes_from_the_plans(client, env, monkeypatch):
    from web import db

    want = ("Free (20 credits), Pro ($29/month, 200 credits) and Power ($79/month, 1,000 credits) plans for True Prob AI, "
            "an NBA player-prop research tool for adults 21+. Credits reset each period and have no cash value; cancel any time.")
    page = client.get("/pricing").text
    assert f'<meta name="description" content="{want}">' in page and f'<meta property="og:description" content="{want}">' in page
    monkeypatch.setitem(db.PLANS, "pro", {**db.PLANS["pro"], "price": 35})
    assert "Pro ($35/month, 200 credits)" in client.get("/pricing").text


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

    leak = "Error code: 401 - Incorrect API key provided: sk-proj-abc1****wxyz"
    monkeypatch.setattr(run_mod, "run_advisor", lambda *a, **k: (_ for _ in ()).throw(RuntimeError(leak)))
    run_id = jobs.start_advisor(uid, "2026-11-15", -250, 250)
    run = db.query("SELECT status, body FROM advisor_runs WHERE id=?", (run_id,))[0]
    assert run["status"] == "failed" and run["body"] == jobs.ADVISOR_FAILED and "sk-proj" not in run["body"]
    assert "sk-proj" in jobs.health(details=True)["errors"][f"advisor:{run_id}"]   # Admin still sees the cause
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
    for path in ("/app", "/app/advisor"):
        assert "Nothing to review in that range, 3 credits returned" in client.get(path).text


def test_advisor_says_how_old_the_board_is_before_charging(client, env, monkeypatch):
    from web import jobs

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    register(client)
    board = write_board(env)
    page = client.get("/app/advisor").text
    assert "It reads today's board as it is now: 3 lines, read just now" in page and "Prices may have moved since." in page
    assert "to start a fresh scan first" not in page
    board["scanned_at"] = time.time() - jobs.BOARD_TTL - 60
    (jobs.BOARD_DIR / "2026-11-15.json").write_text(json.dumps(board), encoding="utf-8")
    page = client.get("/app/advisor").text
    assert "read 31 min ago" in page and '<a href="/app/explorer">Open the explorer</a> to start a fresh scan first.' in page


def test_advisor_run_status_reads_as_words():
    from web.app import run_status

    assert [run_status(s) for s in ("running", "done", "failed", "empty")] == [
        "Being written", "Ready", "Did not complete, 3 credits returned", "Nothing to review in that range, 3 credits returned"]


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
    from web import mailer
    assert "back on the Free plan (20 credits every 30 days)" in mailer.OUTBOX.read_text(encoding="utf-8")


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


def test_admin_forms_answer_bad_input_with_400_not_a_crash(client, env):
    from web import db

    register(client, email="boss@example.com")
    assert post(client, "/admin/announce", body="Maintenance at 3 AM").status_code == 303
    note = db.query("SELECT id FROM announcements")[0]["id"]
    for bad in ("abc", "1.5", "-1", "1 OR 1=1"):
        r = post(client, "/admin/announce", hide=bad)
        assert r.status_code == 400 and "1-800-GAMBLER" in r.text, bad
    assert db.query("SELECT active FROM announcements WHERE id=?", (note,))[0]["active"] == 1
    assert post(client, "/admin/announce", hide=str(note)).status_code == 303
    assert db.query("SELECT active FROM announcements WHERE id=?", (note,))[0]["active"] == 0
    assert post(client, "/admin/credits", user_id="x", delta="5", note="n").status_code == 400
    assert post(client, "/admin/flag", key="nope", value="1").status_code == 400


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


def test_repeated_row_links_name_the_line_they_open(client, env):
    from web import db

    user = register(client)
    write_board(env)
    hidden = r'<span class="visually-hidden">{}</span>'
    page = client.get("/app/explorer").text
    assert 'Open, 1 credit' + hidden.format(": Player 2 points over 20.5") in page
    db.spend(user["id"], "board", "2026-11-15")
    assert 'Details' + hidden.format(": Player 0 points over 20.5") in client.get("/app/explorer").text
    post(client, "/app/watch", player="Player 1")
    page = client.get("/app").text
    assert 'View' + hidden.format(" Player 1 points over 20.5") in page
    assert 'Remove' + hidden.format(" Player 1 from your watchlist") in page and "aria-label=\"Stop watching" not in page


def test_a_gap_that_rounds_to_zero_reads_as_zero_in_no_colour(client, env):
    from web import app as webapp, db

    assert [webapp.gap(e) for e in (0.0123, -0.0004, 0.0004, 0.0, -0.024)] == ["+1.2", "0.0", "0.0", "0.0", "-2.4"]
    assert [webapp.gap_tone(e) for e in (0.0123, -0.0004, 0.0004, -0.024)] == ["pos", "", "", "neg"]
    user = register(client)
    board = write_board(env)
    board["rows"][0]["edge"] = -0.0004
    (env.jobs.BOARD_DIR / "2026-11-15.json").write_text(json.dumps(board), encoding="utf-8")
    db.spend(user["id"], "board", "2026-11-15")
    assert '<span class="">0.0</span>' in client.get("/app/explorer").text
    assert '<dd class="">0.0 points</dd>' in client.get("/app/prop/p0").text
    assert '<span class="neg">-2.4</span>' in client.get("/app/explorer").text


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


def test_explorer_filter_form_is_labelled_for_search_and_mobile_keyboards(client, env):
    register(client)
    write_board(env)
    page = client.get("/app/explorer").text
    assert 'role="search" aria-label="Filter today\'s lines"' in page or 'role="search" aria-label="Filter today&#39;s lines"' in page
    assert 'name="q"' in page and 'enterkeyhint="search"' in page and 'autocapitalize="none"' in page
    assert 'autocomplete="off"' in page and 'spellcheck="false"' in page


def test_explorer_filters_can_be_cleared_and_never_end_in_a_blank_table(client, env):
    register(client)
    board = write_board(env)
    for r, odds in zip(board["rows"], (-150, 120, 300)):
        r["odds"] = odds
    write_board(env, board["rows"])
    page = client.get("/app/explorer").text
    assert "3 of 3 lines match." in page and "Clear filters" not in page
    page = client.get("/app/explorer?min_odds=200&max_odds=-200").text                   # typed the wrong way round
    assert "2 of 3 lines match." in page and 'href="/app/explorer">Clear filters</a>' in page
    assert 'name="min_odds" value="-200"' in page and 'name="max_odds" value="200"' in page
    page = client.get("/app/explorer?q=nobody").text
    assert "No lines match these filters" in page and "Today's board has 3 lines" in page
    assert '<table class="data board">' not in page
    assert "Clear filters" not in client.get("/app/explorer?backed=1").text              # ignored while the board is locked


def test_unlocking_the_board_keeps_the_filters_the_customer_had_on(client, env):
    from web import app as webapp, db

    register(client)
    write_board(env)
    filtered = client.get("/app/explorer?q=Player+0&prop=points").text
    assert 'name="back" value="/app/explorer?q=Player+0&amp;prop=points"' in filtered
    assert webapp._explorer_back({"back": "/app/explorer?q=Player+0&prop=points"}) == "/app/explorer?q=Player+0&prop=points"
    assert webapp._explorer_back({"back": "https://evil.example/"}) == "/app/explorer"
    assert webapp._explorer_back({"back": "/login"}) == "/app/explorer"
    r = post(client, "/app/explorer/unlock", back="/app/explorer?q=Player+0&prop=points")
    assert r.headers["location"] == "/app/explorer?q=Player+0&prop=points"
    assert db.is_unlocked(db.get_user(email="ann@example.com")["id"], "board", "2026-11-15")
    # Off-path back values are ignored so a crafted form cannot bounce elsewhere after the spend.
    assert post(client, "/app/explorer/unlock", back="/pricing").headers["location"] == "/app/explorer"


def test_line_page_back_link_keeps_the_explorer_filters(client, env):
    from urllib.parse import quote

    from web import app as webapp

    register(client)
    write_board(env)
    here = "/app/explorer?q=Player+0&prop=points"
    page = client.get(here).text
    assert f'href="/app/prop/p0?from={quote(here, safe="")}"' in page
    assert webapp._safe_explorer_path(here) == here
    assert webapp._safe_explorer_path("https://evil.example/") == "/app/explorer"
    assert webapp._safe_explorer_path("/login") == "/app/explorer"
    prop = client.get(f"/app/prop/p0?from={quote(here, safe="")}").text
    assert 'href="/app/explorer?q=Player+0&amp;prop=points"' in prop and "Back to the explorer" in prop
    assert 'name="from" value="/app/explorer?q=Player+0&amp;prop=points"' in prop  # unlock keeps the shortlist
    unlocked = post(client, "/app/prop/p0/unlock", **{"from": here})
    assert unlocked.headers["location"] == f"/app/prop/p0?from={quote(here, safe="")}"
    assert post(client, "/app/prop/p1/unlock", **{"from": "/pricing"}).headers["location"] == "/app/prop/p1"


def test_tests_never_write_a_session_key_file(env, monkeypatch, tmp_path):
    from web import db

    monkeypatch.setattr(db, "ROOT", tmp_path)
    assert env._secret_key() and not (tmp_path / "data" / ".secret_key").exists()
    monkeypatch.delenv("SECRET_KEY")
    env._secret_key()                                   # local runs without SECRET_KEY still get a file key
    assert (tmp_path / "data" / ".secret_key").exists()


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


def test_arbitrage_dollars_put_the_sign_first_and_colour_only_real_gains_or_losses(client, env):
    from web import app as webapp, db, jobs

    assert [webapp.usd(v) for v in (1234.5, -0.42, -0.004, 0, None)] == ["$1,234.50", "-$0.42", "$0.00", "$0.00", "$0.00"]
    assert [webapp.usd_tone(v) for v in (1.0, -0.42, -0.004, 0)] == ["pos", "neg", "", ""]
    user = register(client)
    row = lambda slipped: {"title": "A vs B [Kalshi+Polymarket]: A", "legs": ["kalshi: YES", "polymarket: NO"], "contracts": 1500,
                           "cost": 1400.0, "profit": 3.25, "profit_if_slipped": slipped, "interest": None, "urls": []}
    jobs.ARB_PATH.parent.mkdir(parents=True, exist_ok=True)
    jobs.ARB_PATH.write_text(json.dumps({"arbs": [], "near": [row(-0.42), row(0.001)], "maker": [], "scanned_at": time.time(),
                                         "seconds": 100, "stats": {"kalshi_events": 1, "polymarket_events": 1, "matched_games": 1,
                                                                   "confirmed_pairs": 0, "candidates": 2}}), encoding="utf-8")
    db.spend(user["id"], "arbitrage", "2026-11-15")
    page = client.get("/app/arbitrage").text
    assert '<td class="num neg">-$0.42</td>' in page and '<td class="num ">$0.00</td>' in page and "$-" not in page
    assert '<td class="num">$1,400.00</td>' in page and '<td class="num pos">$3.25</td>' in page


def test_scan_error_text_stays_off_public_and_customer_pages(client, env, monkeypatch):
    from web import jobs

    secret = "HTTPError: 401 for url https://api.example.com/odds?apiKey=abc123"
    monkeypatch.setitem(jobs._errors, "board", secret)
    health = client.get("/healthz").json()
    assert health["jobs"]["errors"] == ["board"] and "abc123" not in json.dumps(health)
    register(client)
    page = client.get("/app/explorer").text
    assert "The last scan stopped on our side" in page and "abc123" not in page
    client.cookies.clear()
    register(client, email="boss@example.com")
    assert "abc123" in client.get("/admin").text


def test_production_refuses_to_start_without_base_url(env, monkeypatch):
    monkeypatch.setattr(env, "PRODUCTION", True)
    monkeypatch.delenv("BASE_URL", raising=False)
    with pytest.raises(RuntimeError, match="BASE_URL"):
        env._require_base_url()
    monkeypatch.setenv("BASE_URL", "https://trueprob.example")
    env._require_base_url()


def test_reset_link_uses_base_url_not_the_host_header(client, env, monkeypatch):
    from web import mailer

    register(client)
    monkeypatch.setenv("BASE_URL", "https://trueprob.example")
    client.post("/forgot", data={"csrf": _csrf(client, "/forgot"), "email": "ann@example.com"}, headers={"host": "evil.example"})
    mail = mailer.OUTBOX.read_text(encoding="utf-8")
    assert "https://trueprob.example/reset?token=" in mail and "evil.example" not in mail


def test_owner_is_emailed_when_the_password_is_changed_or_reset(client, env):
    from web import db, mailer

    user = register(client)
    subject = "subject=Your True Prob AI password was changed"
    post(client, "/app/account/password", current="wrong one here", new="brand new pass 2")
    assert subject not in mailer.OUTBOX.read_text(encoding="utf-8")
    post(client, "/app/account/password", current="correct horse 1", new="brand new pass 2")
    assert mailer.OUTBOX.read_text(encoding="utf-8").count(subject) == 1
    token = db.make_token(user["id"], "reset", 3600)
    reset_post(client, token, "third pass 333")
    mail = mailer.OUTBOX.read_text(encoding="utf-8")
    assert mail.count(subject) == 2 and "/forgot" in mail and "third pass" not in mail and "1-800-GAMBLER" in mail


def test_password_notice_names_a_support_address_only_when_one_is_set(client, env, monkeypatch):
    from web import mailer

    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    register(client)
    post(client, "/app/account/password", current="correct horse 1", new="brand new pass 2")
    mail = mailer.OUTBOX.read_text(encoding="utf-8")
    assert "/forgot." in mail and "write to" not in mail and "example.com" not in mail.replace("ann@example.com", "")
    monkeypatch.setenv("SUPPORT_EMAIL", "help@trueprob.example")
    post(client, "/app/account/password", current="brand new pass 2", new="third new pass 3")
    assert "/forgot and write to help@trueprob.example." in mailer.OUTBOX.read_text(encoding="utf-8")


def test_a_new_password_or_signing_out_others_voids_every_other_reset_link(client, env):
    from web import db

    user = register(client)
    reset = lambda token: reset_post(client, token, "fresh password 9").status_code
    first, second = db.make_token(user["id"], "reset", 3600), db.make_token(user["id"], "reset", 3600)
    verify_token = db.make_token(user["id"], "verify", 3600)
    assert reset(first) == 303 and reset(second) == 400
    assert db.use_token(verify_token, "verify") == user["id"]   # only reset links are voided
    register(client, email="bea@example.com")
    bea = db.get_user(email="bea@example.com")
    for path, data in (("/app/account/password", {"current": "correct horse 1", "new": "brand new pass 2"}),
                       ("/app/account/sessions", {"password": "brand new pass 2"})):
        token = db.make_token(bea["id"], "reset", 3600)
        assert post(client, path, **data).status_code == 303
        assert db.use_token(token, "reset") is None, path


def test_spent_and_expired_one_time_links_are_deleted_not_kept(env):
    from web import db

    uid = db.create_user("tok@example.com", "long password 1")
    used, expired, live = (db.make_token(uid, "reset", 3600), db.make_token(uid, "verify", 3600),
                           db.make_token(uid, "verify", 3600))
    db.execute("UPDATE tokens SET expires_at=? WHERE token_hash=?", (time.time() - 1, db._token_hash(expired)))
    assert db.use_token(used, "reset") == uid
    assert len(db.query("SELECT 1 FROM tokens")) == 3
    fresh = db.make_token(uid, "reset", 3600)
    hashes = {r["token_hash"] for r in db.query("SELECT token_hash FROM tokens")}
    assert hashes == {db._token_hash(live), db._token_hash(fresh)}
    assert db.use_token(used, "reset") is None and db.use_token(expired, "verify") is None   # still refused
    assert db.use_token(live, "verify") == uid and db.use_token(fresh, "reset") == uid


def test_reset_emails_are_capped_per_account_without_revealing_it(client, env):
    from web import mailer

    register(client)
    pages = []
    for i in range(5):
        env._hits.pop("forgot:testclient", None)   # a new network each time
        pages.append(client.post("/forgot", data={"csrf": _csrf(client, "/forgot"), "email": "Ann@Example.com "}))
    assert all(p.status_code == 200 for p in pages) and len({re.sub(r'<p class="flash note">Development mode.*?</p>', "", p.text) for p in pages}) == 1
    assert mailer.OUTBOX.read_text(encoding="utf-8").count("subject=Reset your True Prob AI password") == 3


def test_server_error_page_is_html_with_the_disclosures(env, monkeypatch):
    from starlette.testclient import TestClient
    from web import jobs

    monkeypatch.setattr(jobs, "load_board", lambda *a: 1 / 0)
    r = TestClient(env.app, raise_server_exceptions=False).get("/")
    assert r.status_code == 500 and r.headers["content-type"].startswith("text/html")
    assert "21+" in r.text and "1-800-GAMBLER" in r.text and "Nothing was charged" in r.text
    assert "ZeroDivisionError" not in r.text and 'name="robots" content="noindex"' in r.text


def test_performance_splits_every_graded_line_by_market(client, monkeypatch, tmp_path):
    from web import app as webapp, db

    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    row = lambda prop, model, market, result, edge=0.0, roi=None: {"prop": prop, "model_probability": model, "market_probability": market,
                                                                   "result": result, "edge": edge, "roi": roi}
    _graded(preds / "2026-11-01.graded.json", [row("points over 20.5", 0.6, 0.5, "win", 0.1, 0.9),
                                               row("points under 20.5", 0.4, 0.5, "loss"),
                                               row("rebounds over 5.5", 0.5, 0.5, "win"),
                                               row("pra over 30.5", 0.5, 0.5, "void"),       # never decided: no row
                                               row("blocks over 0.5", 0.7, 0.6, "loss", 0.1, -1.0)], 4)
    monkeypatch.setattr(db, "ROOT", tmp_path)
    by = dict(webapp._performance()["by_stat"])
    assert list(by) == ["Points", "Rebounds", "Blocks"] and by["Points"]["decided"] == 2
    assert by["Points"]["brier_model"] == pytest.approx((0.16 + 0.16) / 2) and by["Blocks"]["flagged_roi"] == -1.0
    assert sum(s["decided"] for s in by.values()) == webapp._performance()["total"]["decided"]
    page = client.get("/performance").text
    assert "By market" in page and "-100.0%" in page and "Points + rebounds + assists" not in page


def test_watchlist_says_what_happened_and_overview_shows_every_watched_player(client, env):
    from web import db

    user = register(client)
    write_board(env)
    post(client, "/app/watch", player="Player 2", back="/app/prop/p2")
    assert "Watching Player 2. Their lines on today&#39;s board are listed on your Overview." in client.get("/app/prop/p2").text
    for i in range(48):
        db.execute("INSERT INTO watchlist VALUES (?,?,?,?)", (user["id"], f"filler {i}", f"Filler {i}", time.time() + 1 + i))
    post(client, "/app/watch", player="Player 1")
    page = client.get("/app").text
    assert "Player 2" in page and "/app/prop/p2" in page and "Filler 47" in page   # the oldest of 50 still listed and matched
    post(client, "/app/watch", player="Player 0")
    assert "Your watchlist is full (50 players)" in client.get("/app").text
    assert len(db.query("SELECT 1 FROM watchlist WHERE user_id=?", (user["id"],))) == 50
    post(client, "/app/watch", player="Player 2", remove="1")
    assert "Stopped watching Player 2." in client.get("/app").text


def test_watchlist_downloads_as_csv_of_player_names(client, env):
    assert client.get("/app/watchlist.csv", follow_redirects=False).status_code == 303
    register(client)
    write_board(env)
    post(client, "/app/watch", player="Player 1")
    post(client, "/app/watch", player="=SUM(A1)")  # formula-looking name must be escaped
    assert 'href="/app/watchlist.csv"' in client.get("/app").text
    r = client.get("/app/watchlist.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert r.content.startswith(b"\xef\xbb\xbf")  # UTF-8 BOM for Excel
    assert r.headers["etag"].startswith('"')
    assert client.get("/app/watchlist.csv", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    body = r.content.decode("utf-8-sig")
    assert body.splitlines()[0] == "Player,Added (UTC)"
    assert "Player 1" in body and "'=SUM(A1)" in body
    assert "Kalshi" not in body and "odds" not in body.lower()  # names only; not board prices
    post(client, "/app/watch", player="Player 0")
    assert client.get("/app/watchlist.csv", headers={"If-None-Match": r.headers["etag"]}).status_code == 200  # changed


def test_overview_says_when_watched_lines_are_cut_short(client, env):
    register(client)
    rows = write_board(env)["rows"]
    many = [dict(rows[0], id=f"q{i}", player="Busy Player", line=10.5 + i) for i in range(15)]
    write_board(env, many)
    post(client, "/app/watch", player="Busy Player")
    page = client.get("/app").text
    assert page.count('href="/app/prop/q') == 12
    assert "Showing 12 of 15 lines for your players." in page and 'the <a href="/app/explorer">explorer</a>' in page
    write_board(env, many[:12])
    assert "Showing 12 of" not in client.get("/app").text


def test_account_shows_what_this_periods_credits_were_used_for(client, env):
    from web import db

    user = register(client)
    assert "Nothing used since" in client.get("/app/account").text
    write_board(env)
    for pid in ("p0", "p1", "p0"):   # reopening p0 is free and is not counted
        post(client, f"/app/prop/{pid}/unlock")
    post(client, "/app/explorer/unlock")
    db.spend(user["id"], "advisor", "7", "AI Advisor analysis")
    db.refund(user["id"], "advisor", "7", "the analysis did not complete")
    db.execute("INSERT INTO ledger (user_id, delta, reason, ref, created_at) VALUES (?,?,?,?,?)",
               (user["id"], -5, "Old board", "board:2026-10-01", float(user["credits_reset_at"]) - 60))   # last period
    usage = db.period_usage(db.get_user(user["id"]))
    assert usage == {"prop": {"times": 2, "spent": 2, "refunded": 0}, "board": {"times": 1, "spent": 5, "refunded": 0},
                     "advisor": {"times": 1, "spent": 3, "refunded": 3}}
    page = client.get("/app/account").text
    assert "Single lines opened: 2, 2 credits" in page and "Full boards opened: 1, 5 credits" in page
    assert "AI Advisor analyses: 1, 3 credits (3 returned)" in page and "Arbitrage terminal days" not in page


def test_common_and_context_passwords_are_refused_everywhere_a_password_is_set(client):
    from web import db, passwords

    for weak in ("1234567890", "Password2026!", "basketball23", "TrueProb2026", "aaaaaaaaaa", "ababababab"):
        assert passwords.problem(weak), weak
    assert passwords.problem("kimberly.smith99", "kimberly.smith@example.com")           # the email's own name
    for fine in ("correct horse 1", "long enough 1", "brand new pass 2", "password manager lamp"):
        assert passwords.problem(fine) is None, fine

    def sign_up(pw):
        return client.post("/register", data={"csrf": _csrf(client), "email": "kim@example.com", "password": pw,
                                              "region": "New Jersey", "age": "yes", "terms": "yes"}, follow_redirects=False)

    r = sign_up("qwertyuiop")
    assert r.status_code == 400 and "list of common or easy-to-guess passwords" in r.text
    assert "three or four unrelated words" in r.text.lower() and "not a common password, your email or our name" in r.text
    assert sign_up("kim@example.com").status_code == 400 and db.get_user(email="kim@example.com") is None
    user = register(client)

    post(client, "/app/account/password", current="correct horse 1", new="ann@example.com")
    assert "list of common or easy-to-guess passwords" in client.get("/app/account").text
    assert db.check_password("correct horse 1", db.get_user(user["id"])["pw_hash"])

    token = db.make_token(user["id"], "reset", 3600)
    r = reset_post(client, token, "football2026")
    assert r.status_code == 400 and "list of common" in r.text
    r = reset_post(client, token, "ann@example.com")
    fresh = re.search(r'name="token" value="([^"]+)"', r.text).group(1)        # token was spent; the form gets a new one
    assert r.status_code == 400 and fresh != token
    assert reset_post(client, fresh, "brand new pass 2").status_code == 303
    assert db.check_password("brand new pass 2", db.get_user(user["id"])["pw_hash"])


def test_new_password_must_be_typed_the_same_twice(client):
    from web import db

    user = register(client)
    page = client.get("/app/account").text
    assert 'name="new2"' in page and "Confirm new password" in page
    assert 'name="password2"' in client.get("/reset?token=abc").text
    post(client, "/app/account/password", current="correct horse 1", new="brand new pass 2", new2="brand new pass 3")
    assert "Those two passwords do not match." in client.get("/app/account").text
    assert db.check_password("correct horse 1", db.get_user(user["id"])["pw_hash"])
    token = db.make_token(user["id"], "reset", 3600)
    r = reset_post(client, token, "brand new pass 2", password2="brand new pass 3")
    assert r.status_code == 400 and "Those two passwords do not match." in r.text
    assert db.use_token(token, "reset") == user["id"]  # mismatch is checked before the token is spent


def test_account_password_form_helps_password_managers(client):
    # Chromium / web.dev: username + current-password / new-password ids, with a Show toggle per field.
    user = register(client)
    page = client.get("/app/account").text
    assert f'name="username" value="{user["email"]}" autocomplete="username"' in page
    assert 'id="current-password"' in page and 'autocomplete="current-password"' in page
    assert 'id="new-password"' in page and 'id="new-password-confirm"' in page
    assert 'for="current-password"' in page and 'for="new-password"' in page and 'for="new-password-confirm"' in page
    for field in ("current-password", "new-password", "new-password-confirm"):
        assert f'data-reveal="{field}"' in page and f'aria-controls="{field}"' in page
    assert 'aria-describedby="new-pw-rule"' in page
    # Sign-out-others and delete use the same Show pattern with distinct labelled ids.
    assert 'id="sessions-password"' in page and 'for="sessions-password"' in page
    assert 'data-reveal="sessions-password"' in page and 'aria-describedby="sessions-pw-hint"' in page
    assert 'id="delete-password"' in page and 'for="delete-password"' in page
    assert 'data-reveal="delete-password"' in page
    post(client, "/app/account/password", current="correct horse 1", new="brand new pass 2")
    assert "Password changed" in client.get("/app/account").text


def test_skip_link_target_can_receive_keyboard_focus(client):
    # tabindex=-1 lets "Skip to content" move focus into main without adding a Tab stop (Chrome a11y guidance).
    assert 'href="#main"' in client.get("/").text
    assert 'id="main" tabindex="-1"' in client.get("/").text
    register(client)
    assert 'id="main" class="work" tabindex="-1"' in client.get("/app").text


def test_warning_flashes_are_announced_as_alerts(client):
    register(client, verify=False)
    app = client.get("/app").text
    assert 'class="flash warn" role="alert"' in app and "Confirm your email" in app
    post(client, "/app/account/password", current="wrong guess", new="brand new pass 2")
    assert 'class="flash warn" role="alert"' in client.get("/app/account").text
    # A successful flash stays polite (status), not an interrupt.
    post(client, "/app/account/sessions", password="correct horse 1")
    page = client.get("/app/account").text
    assert 'class="flash ok" role="status"' in page and "signed out" in page.lower()


def test_pages_are_isolated_from_cross_origin_windows(client):
    for path in ("/", "/login", "/performance", "/nope"):
        h = client.get(path).headers
        assert h["Cross-Origin-Opener-Policy"] == "same-origin", path
        assert h["Cross-Origin-Resource-Policy"] == "same-origin", path


def test_permissions_policy_disables_sensors_and_payment_apis(client):
    # Research UI never needs these; leaving them on widens the XSS blast radius (OWASP headers).
    policy = client.get("/").headers["Permissions-Policy"]
    for feature in ("geolocation", "camera", "microphone", "accelerometer", "gyroscope", "magnetometer",
                    "usb", "payment", "midi", "display-capture", "bluetooth", "serial", "hid",
                    "xr-spatial-tracking", "interest-cohort", "browsing-topics"):
        assert f"{feature}=()" in policy, feature
    assert "clipboard-write" not in policy  # password managers paste into forms


def test_cross_domain_policy_files_are_refused(client):
    # OWASP Secure Headers: none means no crossdomain.xml opt-in for legacy clients.
    assert client.get("/").headers["X-Permitted-Cross-Domain-Policies"] == "none"
    assert client.get("/login").headers["X-Permitted-Cross-Domain-Policies"] == "none"


def test_pages_with_a_token_in_the_address_send_no_referrer(client):
    reset = client.get("/reset?token=abc")
    assert reset.headers["Referrer-Policy"] == "no-referrer"
    assert reset.headers["X-Robots-Tag"] == "noindex"
    verify = client.get("/verify?token=abc", follow_redirects=False)
    assert verify.headers["Referrer-Policy"] == "no-referrer"
    assert verify.headers["X-Robots-Tag"] == "noindex"
    assert client.get("/login").headers["Referrer-Policy"] == "strict-origin-when-cross-origin"
    assert "X-Robots-Tag" not in client.get("/").headers


def test_sign_in_pages_send_x_robots_noindex(client):
    # Meta noindex was already set; the header reaches crawlers that skip HTML (same pattern as /reset).
    for path in ("/login", "/register", "/forgot"):
        assert client.get(path).headers["X-Robots-Tag"] == "noindex", path
        assert 'name="robots" content="noindex"' in client.get(path).text, path
    assert "X-Robots-Tag" not in client.get("/pricing").headers
    assert "X-Robots-Tag" not in client.get("/performance").headers


def test_security_txt_follows_rfc_9116_and_needs_a_real_contact(client, monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.delenv("SUPPORT_EMAIL", raising=False)
    assert client.get("/.well-known/security.txt").status_code == 404     # never advertise support@example.com
    monkeypatch.setenv("SUPPORT_EMAIL", "help@trueprob.example")
    r = client.get("/.well-known/security.txt")
    assert r.status_code == 200 and r.headers["content-type"] == "text/plain; charset=utf-8"
    fields = dict(line.split(": ", 1) for line in r.text.strip().splitlines())
    assert fields["Contact"] == "mailto:help@trueprob.example" and fields["Preferred-Languages"] == "en"
    assert fields["Canonical"] == "http://testserver/.well-known/security.txt"
    expires = datetime.strptime(fields["Expires"], "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=timezone.utc)
    assert timedelta(days=30) < expires - datetime.now(timezone.utc) < timedelta(days=365)
    assert r.text.count("Expires:") == 1


def test_sign_in_and_recovery_pages_are_never_cached(client):
    for path in ("/login", "/register", "/forgot", "/reset?token=abc"):
        assert client.get(path).headers["Cache-Control"] == "no-store", path
    assert client.get("/verify?token=abc", follow_redirects=False).headers["Cache-Control"] == "no-store"
    r = client.post("/login", data={"csrf": _csrf(client, "/login"), "email": "x@example.com", "password": "nope"})
    assert r.status_code == 400 and r.headers["Cache-Control"] == "no-store"
    for path in ("/", "/pricing", "/performance", "/legal/terms"):          # public pages stay cacheable
        assert "Cache-Control" not in client.get(path).headers, path
    static = client.get("/static/app.css").headers["Cache-Control"]
    assert static == "public, max-age=86400"
    assert client.get("/static/app.js").headers["Cache-Control"] == "public, max-age=86400"


def test_private_pages_turn_off_dns_prefetch(client):
    # OWASP: private/auth pages should not DNS-prefetch third-party hosts from links.
    for path in ("/login", "/register", "/forgot", "/reset?token=abc"):
        assert client.get(path).headers["X-DNS-Prefetch-Control"] == "off", path
    register(client)
    assert client.get("/app").headers["X-DNS-Prefetch-Control"] == "off"
    assert client.get("/app/account").headers["X-DNS-Prefetch-Control"] == "off"
    for path in ("/", "/pricing", "/performance"):
        assert "X-DNS-Prefetch-Control" not in client.get(path).headers, path


def test_account_lists_recent_sign_ins_and_security_changes_for_this_user_only(client):
    from starlette.testclient import TestClient

    register(client)
    other = TestClient(client.app)
    other.post("/login", data={"csrf": _csrf(other, "/login"), "email": "ann@example.com", "password": "wrong password"})
    other.post("/login", data={"csrf": _csrf(other, "/login"), "email": "ann@example.com", "password": "correct horse 1"})
    post(client, "/app/account/password", current="correct horse 1", new="brand new pass 2")
    stranger = TestClient(client.app)
    register(stranger, email="bob@example.com")
    stranger.post("/login", data={"csrf": _csrf(stranger, "/login"), "email": "bob@example.com", "password": "nope nope nope"})

    page = client.get("/app/account").text
    section = page[page.index("Recent sign-ins and security changes"):]
    labels = re.findall(r"<li>([^<]+?) <span class=\"muted\">", section)
    assert labels == ["Password changed", "Signed in", "Sign-in with a wrong password", "Account created"]
    assert "We do not record IP addresses or devices" in section


def test_customers_can_download_their_own_data_as_json(client, env):
    from starlette.testclient import TestClient

    from web import db

    assert client.get("/app/account/data.json", follow_redirects=False).status_code == 303   # signed out
    user = register(client)
    write_board(env)
    post(client, "/app/prop/p0/unlock")
    post(client, "/app/watch", player="Player 0")
    db.execute("UPDATE users SET stripe_customer_id='cus_secret' WHERE id=?", (user["id"],))
    register(TestClient(client.app), email="bob@example.com")
    assert 'href="/app/account/data.json"' in client.get("/app/account").text

    r = client.get("/app/account/data.json")
    assert r.status_code == 200 and r.headers["content-type"].startswith("application/json")
    assert "attachment" in r.headers["content-disposition"] and r.headers["cache-control"] == "no-store"
    assert "etag" in r.headers
    data = r.json()
    assert data["account"]["email"] == "ann@example.com" and data["account"]["credit_balance"] == 19
    assert [h["credits"] for h in data["credit_history"]] == [20, -1]
    assert data["watchlist"] == [{"at": data["watchlist"][0]["at"], "player": "Player 0"}]
    assert data["unlocked"][0]["kind"] == "prop" and data["exported_at"].endswith("Z")
    assert "user.register" in {e["action"] for e in data["security_log"]}
    assert "bob@example.com" not in r.text and "cus_secret" not in r.text and "scrypt" not in r.text
    assert db.query("SELECT 1 FROM audit WHERE actor_id=? AND action='data.export'", (user["id"],))
    # Unchanged re-download is a bodiless 304 (same pattern as credit-history CSV).
    assert client.get("/app/account/data.json", headers={"If-None-Match": r.headers["etag"]}).status_code == 304
    post(client, "/app/watch", player="Player 1")
    changed = client.get("/app/account/data.json", headers={"If-None-Match": r.headers["etag"]})
    assert changed.status_code == 200 and "Player 1" in changed.text


def test_wallet_warns_before_the_balance_is_too_low_for_a_board(client):
    from web import db

    user = register(client)
    assert "Running low" not in client.get("/app").text and "Out of credits" not in client.get("/app").text
    db.adjust_credits(user["id"], user["id"], -17, "test")          # 3 left
    page = client.get("/app/account").text
    assert "Running low: enough for 3 single lines, not a full board (5)." in page
    db.adjust_credits(user["id"], user["id"], -2, "test")           # 1 left
    assert "enough for 1 single line, not" in client.get("/app").text
    db.adjust_credits(user["id"], user["id"], -1, "test")
    assert "Out of credits. Nothing is charged automatically." in client.get("/app/explorer").text


def test_paid_buttons_say_when_the_balance_is_short_before_anyone_clicks(client, env, monkeypatch):
    from web import db

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")
    user = register(client)
    write_board(env)
    db.adjust_credits(user["id"], user["id"], -18, "test")          # 2 left
    explorer = client.get("/app/explorer").text
    assert "This needs 5 credits and you have 2." in explorer and '<a href="/pricing?need=board">See plans</a>' in explorer
    assert re.search(r"<button class=\"btn\" disabled>Open board for 5 credits", explorer)
    assert "when your balance becomes 20" in explorer
    line = client.get("/app/prop/p0").text
    assert "You have 2." in line and "This needs" not in line
    assert not re.search(r"<button class=\"btn\" disabled>Open for 1 credit", line)
    advisor = client.get("/app/advisor").text
    assert "This needs 3 credits and you have 2." in advisor and "need=advisor" in advisor
    from web import jobs

    jobs.ARB_PATH.write_text(json.dumps({"arbs": [], "near": [], "maker": [], "scanned_at": time.time(), "seconds": 1, "stats": {
        "kalshi_events": 1, "polymarket_events": 1, "matched_games": 0, "confirmed_pairs": 0, "candidates": 0,
        "depth_checked": 0, "maker_pairs": 0, "book_errors": 0}}), encoding="utf-8")
    assert "This needs 5 credits and you have 2." in client.get("/app/arbitrage").text
    db.adjust_credits(user["id"], user["id"], 10, "test")
    assert "You have 12. Refunded automatically if it fails." in client.get("/app/advisor").text
    assert "This needs" not in client.get("/app/explorer").text


def test_performance_numbers_are_reused_until_a_graded_file_changes(client, monkeypatch, tmp_path):
    from web import app as webapp, db

    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    row = {"model_probability": 0.6, "market_probability": 0.5, "result": "win", "edge": 0.1, "roi": 1.0}
    _graded(preds / "2026-11-01.graded.json", [row], 1)
    monkeypatch.setattr(db, "ROOT", tmp_path)
    runs = []
    real = webapp._compute_performance
    monkeypatch.setattr(webapp, "_compute_performance", lambda: runs.append(1) or real())
    assert len(webapp._performance()["dates"]) == 1
    client.get("/performance")
    client.get("/performance")
    assert len(runs) == 1                                        # parsed once, not on every page view
    _graded(preds / "2026-11-02.graded.json", [row, row], 2)     # a newly graded date shows up straight away
    assert len(webapp._performance()["dates"]) == 2 and len(runs) == 2


def test_relative_times_switch_to_days_after_two_days(env):
    now = time.time()
    assert [env.ago(now - s) for s in (10, 5 * 60, 3 * 3600, 47 * 3600)] == ["just now", "5 min ago", "3 h ago", "47 h ago"]
    assert env.ago(now - 72 * 3600) == "3 days ago" and env.ago(now - 30 * 86400) == "30 days ago" and env.ago(None) == "never"


def test_health_check_says_503_in_json_when_the_database_is_down(client, env, monkeypatch):
    from web import db

    ok = client.get("/healthz")
    assert ok.status_code == 200 and ok.json()["ok"] is True and ok.json()["database"] == "ok"
    assert ok.headers["cache-control"] == "no-store"

    def broken(*a, **k):
        raise __import__("sqlite3").OperationalError("disk I/O error at /secret/path")

    monkeypatch.setattr(db, "query", broken)
    down = client.get("/healthz")
    assert down.status_code == 503 and down.json() == {"ok": False, "database": "unavailable"}
    assert "secret" not in down.text and down.headers["cache-control"] == "no-store"


def test_dataset_markup_and_page_explain_every_csv_column(client):
    from web import app as webapp

    page = client.get("/performance").text
    data = json.loads(re.search(r'<script type="application/ld\+json">(.*?)</script>', page, re.S).group(1))
    names = [v["name"] for v in data["variableMeasured"]]
    assert names == list(webapp.GRADED_COLUMNS) and all(v["@type"] == "PropertyValue" and v["description"]
                                                        for v in data["variableMeasured"])
    assert "Brier score" in data["keywords"]
    header = client.get("/performance/2026-11-06.csv").text.lstrip("\ufeff").splitlines()[0]
    assert header.split(",") == names                                   # the markup describes the real file
    assert "What each CSV column means" in page
    for k, v in webapp.GRADED_COLUMN_NOTES.items():                     # and the same text is on the page
        text = str(__import__("markupsafe").escape(v))
        assert f"<dt><code>{k}</code></dt><dd>{text}</dd>" in page
