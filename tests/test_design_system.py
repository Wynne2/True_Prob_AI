"""Regression checks for the app-wide accessibility and responsive design contract."""

from app.design_system import _THEME_CSS


def test_design_system_preserves_accessibility_and_responsive_foundations():
    assert ":focus-visible" in _THEME_CSS
    assert "prefers-reduced-motion: reduce" in _THEME_CSS
    assert "@media (max-width: 900px)" in _THEME_CSS
    assert "@media (max-width: 640px)" in _THEME_CSS
    assert "--tp-text-secondary: #a7b0c0" in _THEME_CSS
    assert "min-height: 2.75rem" in _THEME_CSS
