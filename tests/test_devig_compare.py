import json

import pytest

from engine import devig_compare as dc
from engine.market_blend import no_vig_side_probability
from odds.normalizer import american_to_raw_implied_prob as q


@pytest.mark.parametrize("over, under", [(-110, -110), (-250, 190), (300, -450), (-120, 100)])
def test_every_method_gives_both_sides_summing_to_one(over, under):
    qo, qu = q(over), q(under)
    for name, f in dc.METHODS.items():
        assert f(qo, qu) + f(qu, qo) == pytest.approx(1.0, abs=1e-9), name


def test_multiplicative_is_what_the_board_uses():
    assert dc.multiplicative(q(-250), q(190)) == pytest.approx(no_vig_side_probability(-250, 190, "over"))


def test_even_prices_are_fifty_fifty_and_long_shots_come_down():
    for f in dc.METHODS.values():
        assert f(q(-110), q(-110)) == pytest.approx(0.5)
    long_shot = q(300), q(-450)
    for name in ("additive", "power", "shin"):
        assert dc.METHODS[name](*long_shot) < dc.multiplicative(*long_shot), name


def test_report_scores_by_date_and_never_changes_the_board(tmp_path):
    preds = tmp_path / "data" / "predictions"
    preds.mkdir(parents=True)
    rows = []
    for i, (over, under, result) in enumerate([(-110, -110, "win"), (300, -450, "loss"), (-200, 160, "win")]):
        for side, odds, res in (("over", over, result), ("under", under, "loss" if result == "win" else "win")):
            rows.append({"key": f"g{i}|p{i}|points|{side}|20.5|kalshi", "odds": odds, "result": res})
    rows.append({"key": "g9|p9|points|over|20.5|kalshi", "odds": -110, "result": "win"})   # one side only: skipped
    (preds / "2026-11-04.graded.json").write_text(json.dumps({"rows": rows}), encoding="utf-8")
    (preds / "2026-11-05.graded.json").write_text(json.dumps({"rows": rows[:2]}), encoding="utf-8")
    r = dc.run(tmp_path, save=False)
    assert r["pairs"] == 4 and r["dates"] == ["2026-11-04", "2026-11-05"] and r["venues"] == {"kalshi": 4}
    assert r["all_pairs"]["multiplicative"]["mean_date_brier_delta"] == 0
    assert set(r["all_pairs"]) == set(dc.METHODS) and "decision" in r


def test_experiment_is_offline_only():
    shipped = json.loads(dc.REPORT_PATH.read_text(encoding="utf-8"))
    assert set(shipped["all_pairs"]) == set(dc.METHODS) and "passing_bonferroni" in shipped
    root = dc.ROOT
    users = [p for d in ("engine", "web", "arbitrage", "services", "app") for p in (root / d).rglob("*.py")
             if p.name != "devig_compare.py" and "devig_compare" in p.read_text(encoding="utf-8")]
    assert users == [] and "devig_compare" not in (root / "main.py").read_text(encoding="utf-8")
