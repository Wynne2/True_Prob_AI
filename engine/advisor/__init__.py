"""
AI Advisor: deterministic pick / parlay table + OpenAI reasoning layer.

Entry point: :func:`engine.advisor.run.run_advisor`.
"""

from __future__ import annotations

from engine.advisor.run import AdvisorOptions, AdvisorResult, run_advisor

__all__ = ["AdvisorOptions", "AdvisorResult", "run_advisor"]
