"""Browser-level Streamlit journey using its official testing harness."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_demo_user_can_scan_and_inspect_a_fixture_backed_prediction():
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
    ).run(timeout=30)

    assert not app.exception
    assert [tab.label for tab in app.tabs][:2] == ["Overview", "Straight Bets"]
    assert app.button[0].label == "Scan slate"

    app.button[0].click().run(timeout=60)

    assert not app.exception
    assert any("Evaluated" in notice.value for notice in app.success)
    assert any("Highest current edge" in notice.value for notice in app.success)
    assert len(app.dataframe) >= 1
