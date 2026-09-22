"""Browser-level Streamlit journey using its official testing harness."""

from __future__ import annotations

from pathlib import Path

from streamlit.testing.v1 import AppTest


def test_demo_user_can_scan_and_inspect_a_fixture_backed_prediction():
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
    ).run(timeout=30)

    assert not app.exception
    assert app.segmented_control[0].options == [
        "Home",
        "Research",
        "Performance",
        "System",
    ]
    assert app.segmented_control[0].value == "Home"
    assert app.button[0].label == "Scan slate"

    app.button[0].click().run(timeout=60)

    assert not app.exception
    assert any("Evaluated" in notice.value for notice in app.success)
    assert any("Highest current edge" in notice.value for notice in app.success)
    assert {metric.label for metric in app.metric} >= {
        "Pregame games",
        "Offers evaluated",
        "Recommendations",
        "Quality flags",
    }

    app.segmented_control[0].set_value("Research").run(timeout=30)

    assert not app.exception
    assert [tab.label for tab in app.tabs][:4] == [
        "Recommendation board",
        "Market grid",
        "Line shop",
        "Parlay lab",
    ]
    assert app.text_input[0].label == "Search recommendations"
    assert len(app.dataframe) >= 1

    app.text_input[0].set_value("definitely-not-a-player").run(timeout=30)

    assert not app.exception
    assert any(
        "No recommendations match" in block.value for block in app.markdown
    )


def test_system_workspace_keeps_diagnostics_out_of_primary_navigation():
    app = AppTest.from_file(
        Path(__file__).resolve().parents[1] / "app" / "streamlit_app.py"
    ).run(timeout=30)

    app.segmented_control[0].set_value("System").run(timeout=30)

    assert not app.exception
    labels = [tab.label for tab in app.tabs]
    assert {
        "Activity and exclusions",
        "Provider diagnostics",
        "Model audit",
    }.issubset(labels)
    assert any(
        "BALLDONTLIE_API_KEY" in warning.value for warning in app.warning
    )
