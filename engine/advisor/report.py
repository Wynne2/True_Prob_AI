"""
Rendering and persistence for AI Advisor results.

- :func:`render_console` prints rich tables (picks, parlays, AI reasoning).
- :func:`to_markdown` builds a human-readable report.
- :func:`save_result` writes ``data/advisor/<date>_<HHMMSS>.json`` and ``.md``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import asdict
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Optional

from engine.advisor.prompts import SYSTEM_PROMPT
from utils.api_debug import redact_sensitive_text
from utils.formatting import format_american, format_currency, format_edge, format_prob

if TYPE_CHECKING:
    from rich.console import Console

    from engine.advisor.parlays import ParlayRow, PickRow
    from engine.advisor.run import AdvisorResult

logger = logging.getLogger(__name__)


def _pick_label(row: "PickRow") -> str:
    p = row.prop
    return f"{p.player_name} {p.prop_type.value.upper()} {p.side.value} {p.line:g}"


def _status_line(result: "AdvisorResult") -> str:
    from engine.advisor.run import AI_DISABLED, AI_FAILED, AI_OK, AI_SKIPPED

    meta = result.llm_meta or {}
    if result.ai_status == AI_OK:
        usage = meta.get("usage") or {}
        return (
            f"AI layer: OK — {meta.get('model')} (effort {meta.get('reasoning_effort')}, "
            f"{meta.get('transport')} transport), {meta.get('attempts')} attempt(s), "
            f"{meta.get('latency_s', 0):.1f}s, tokens in/out/reasoning "
            f"{usage.get('input_tokens', 0)}/{usage.get('output_tokens', 0)}/{usage.get('reasoning_tokens', 0)}"
        )
    if result.ai_status == AI_DISABLED:
        return "AI layer: disabled (--no-llm). Showing the deterministic recommendation."
    if result.ai_status == AI_SKIPPED:
        return "AI layer: skipped — no props passed the filters."
    if result.ai_status == AI_FAILED:
        reason = (result.ai_error or "unknown error").rstrip(". ")
        return f"AI layer FAILED — {reason}. Showing the deterministic recommendation instead."
    return f"AI layer: {result.ai_status}"


def _filter_counts_line(result: "AdvisorResult") -> str:
    s = result.stats
    return (
        f"{s.total_props} props → {s.positive_edge} positive edge → {s.in_odds_band} in odds band → "
        f"{s.confidence_ok} after confidence → {s.deduped} unique → {s.selected} selected"
    )


def _parlay_legs_text(row: "ParlayRow", picks: dict[str, "PickRow"]) -> str:
    return " + ".join(f"{pid} {picks[pid].prop.player_name}" for pid in row.leg_ids)


# ---------------------------------------------------------------------------
# Console
# ---------------------------------------------------------------------------

def _kelly_text(row, bankroll: Optional[float]) -> str:
    """Quarter-Kelly bankroll share, plus the dollar stake when a bankroll is given."""
    frac = getattr(row, "kelly_fraction", 0.0) or 0.0
    if bankroll:
        return f"{frac:.1%} ({format_currency(row.kelly_stake(bankroll))})"
    return f"{frac:.1%}"


def render_console(result: "AdvisorResult", console: "Console", max_parlays: int = 10) -> None:
    """Print picks, parlays, AI reasoning and saved file paths."""
    from rich import box
    from rich.panel import Panel
    from rich.table import Table

    from utils.formatting import book_display_name

    opts = result.options
    console.print(Panel.fit(
        f"[bold cyan]AI Advisor[/bold cyan]  |  {result.game_date}  |  odds "
        f"{format_american(min(opts.min_odds, opts.max_odds))} to {format_american(max(opts.min_odds, opts.max_odds))}  |  "
        f"edge > {format_edge(opts.min_edge)}  |  stake {format_currency(opts.stake)}",
        border_style="cyan",
    ))
    if result.odds_source_counts:
        src = ", ".join(f"{k}: {v}" for k, v in sorted(result.odds_source_counts.items()))
        console.print(f"[dim]Odds lines by source — {src}[/dim]")
    console.print(f"[dim]Filter funnel: {_filter_counts_line(result)}[/dim]\n")

    if not result.picks:
        console.print("[yellow]No props qualified for the advisor with these settings.[/yellow]")
        console.print(f"[yellow]{_status_line(result)}[/yellow]")
        _print_paths(result, console)
        return

    ai_rank: dict[str, int] = {}
    ai_notes: dict[str, str] = {}
    if result.ai_response:
        for tp in result.ai_response.top_picks:
            ai_rank[tp.id] = tp.rank
            ai_notes[tp.id] = tp.rationale

    t = Table(title=f"Top {len(result.picks)} picks", box=box.SIMPLE_HEAVY, show_lines=False)
    for col in ("ID", "AI", "Player", "Prop", "Odds", "Venue", "Proj", "Market", "True", "Implied", "Edge",
                "Conf", f"Return on {format_currency(opts.stake)}", "EV", "Kelly"):
        t.add_column(col, justify="right" if col not in ("Player", "Prop", "Venue", "Conf") else "left")
    for row in result.picks:
        p = row.prop
        t.add_row(
            row.id,
            str(ai_rank.get(row.id, "")),
            p.player_name,
            f"{p.prop_type.value.upper()} {p.side.value} {p.line:g}",
            format_american(p.sportsbook_odds),
            book_display_name(p.best_book, getattr(p, "best_book_key", "") or ""),
            f"{p.projected_value:.1f}",
            format_prob(p.market_probability) if getattr(p, "market_probability", None) is not None else "–",
            format_prob(p.true_probability),
            format_prob(p.implied_probability),
            format_edge(p.edge),
            p.confidence.value,
            format_currency(row.total_return),
            f"{row.expected_value:+.2f}",
            _kelly_text(row, opts.bankroll),
        )
    console.print(t)

    picks = result.pick_by_id
    recommended = {r.id for r in result.ai_response.parlay_recommendations} if result.ai_response else set()
    det = {x for x in (result.deterministic.best_two_leg, result.deterministic.best_three_leg) if x}
    if result.parlays:
        pt = Table(title="Parlays (correlation-checked, sorted by model EV)", box=box.SIMPLE_HEAVY)
        for col in ("ID", "Legs", "Odds", "Implied", "Model true", "Edge",
                    f"Return on {format_currency(opts.stake)}", "Profit", "EV", "Kelly", "Corr", "Venue", "Pick"):
            pt.add_column(col, justify="left" if col in ("Legs", "Venue", "Pick") else "right")
        for row in result.parlays[:max_parlays]:
            flag = "AI" if row.id in recommended else ("best EV" if row.id in det else "")
            pt.add_row(
                row.id,
                _parlay_legs_text(row, picks),
                format_american(row.combined_american_odds),
                format_prob(row.implied_probability),
                format_prob(row.true_probability),
                format_edge(row.edge),
                format_currency(row.total_return),
                format_currency(row.profit),
                f"{row.expected_value:+.2f}",
                _kelly_text(row, opts.bankroll),
                f"{row.correlation_risk_score:.2f}",
                "single" if row.single_venue else "mixed",
                flag,
            )
        console.print(pt)
        if len(result.parlays) > max_parlays:
            console.print(f"[dim]{len(result.parlays) - max_parlays} more parlays in the saved JSON.[/dim]")
    else:
        console.print("[yellow]No valid 2- or 3-leg parlays (need ≥2 non-correlated legs).[/yellow]")

    console.print()
    color = "green" if result.ai_response else "yellow"
    console.print(f"[{color}]{_status_line(result)}[/{color}]")

    if result.ai_response:
        resp = result.ai_response
        lines = [f"[bold]Summary:[/bold] {resp.summary}", ""]
        for tp in resp.top_picks:
            lines.append(f"[bold]#{tp.rank} {tp.id} {_pick_label(picks[tp.id])}[/bold] — {tp.rationale}")
            if tp.risk_notes:
                lines.append(f"   [dim]Risk: {tp.risk_notes}[/dim]")
        lines.append("")
        if resp.straight_bets_only or not resp.parlay_recommendations:
            lines.append("[bold]Parlay call:[/bold] straight bets only.")
        for rec in resp.parlay_recommendations:
            row = result.parlay_by_id[rec.id]
            lines.append(
                f"[bold]{rec.id} ({row.num_legs}-leg, {format_american(row.combined_american_odds)}):[/bold] "
                f"{format_currency(opts.stake)} returns {format_currency(row.total_return)} "
                f"(profit {format_currency(row.profit)}, model EV {row.expected_value:+.2f})"
            )
            lines.append(f"   {rec.rationale}")
            lines.append(f"   [dim]Key risks: {rec.key_risks}[/dim]")
        lines.append("")
        lines.append(f"[italic]{resp.responsible_gambling_note}[/italic]")
        console.print(Panel("\n".join(lines), title="AI reasoning", border_style="magenta"))
    else:
        console.print(Panel(_deterministic_text(result), title="Deterministic recommendation", border_style="yellow"))

    for w in result.warnings:
        console.print(f"[yellow]⚠ {w}[/yellow]")
    _print_paths(result, console)


def _deterministic_text(result: "AdvisorResult") -> str:
    det = result.deterministic
    opts = result.options
    lines = [det.note, "", "Top picks: " + ", ".join(
        f"{pid} {_pick_label(result.pick_by_id[pid])}" for pid in det.top_pick_ids
    )]
    for label, pid in (("Best 2-leg", det.best_two_leg), ("Best 3-leg", det.best_three_leg)):
        if pid:
            row = result.parlay_by_id[pid]
            lines.append(
                f"{label}: {pid} = {_parlay_legs_text(row, result.pick_by_id)} at "
                f"{format_american(row.combined_american_odds)} — {format_currency(opts.stake)} returns "
                f"{format_currency(row.total_return)} (profit {format_currency(row.profit)}, "
                f"model EV {row.expected_value:+.2f})"
            )
    lines.append("")
    lines.append("Bet only what you can afford to lose; model edges are estimates, not guarantees.")
    return "\n".join(lines)


def _print_paths(result: "AdvisorResult", console: "Console") -> None:
    if result.json_path:
        console.print(f"[dim]Saved: {result.json_path}[/dim]")
    if result.md_path:
        console.print(f"[dim]Saved: {result.md_path}[/dim]")


# ---------------------------------------------------------------------------
# Markdown
# ---------------------------------------------------------------------------

def to_markdown(result: "AdvisorResult", max_parlays: int = 10) -> str:
    """Human-readable markdown report (also used by Streamlit)."""
    from utils.formatting import book_display_name

    opts = result.options
    out = [
        f"# AI Advisor — {result.game_date}",
        "",
        f"- Odds band: {format_american(min(opts.min_odds, opts.max_odds))} to "
        f"{format_american(max(opts.min_odds, opts.max_odds))}; edge > {format_edge(opts.min_edge)}; "
        f"stake {format_currency(opts.stake)}",
        f"- Filter funnel: {_filter_counts_line(result)}",
    ]
    if result.odds_source_counts:
        out.append("- Odds lines by source: " + ", ".join(
            f"{k} {v}" for k, v in sorted(result.odds_source_counts.items())
        ))
    out += [f"- {_status_line(result)}", ""]

    if result.picks:
        out += [
            "## Top picks",
            "",
            "| ID | Player | Prop | Odds | Venue | Market | True | Implied | Edge | Conf | Return | EV | Kelly |",
            "|---|---|---|---:|---|---:|---:|---:|---:|---|---:|---:|---:|",
        ]
        for row in result.picks:
            p = row.prop
            out.append(
                f"| {row.id} | {p.player_name} | {p.prop_type.value.upper()} {p.side.value} {p.line:g} | "
                f"{format_american(p.sportsbook_odds)} | "
                f"{book_display_name(p.best_book, getattr(p, 'best_book_key', '') or '')} | "
                f"{format_prob(p.market_probability) if getattr(p, 'market_probability', None) is not None else '–'} | "
                f"{format_prob(p.true_probability)} | {format_prob(p.implied_probability)} | "
                f"{format_edge(p.edge)} | {p.confidence.value} | {format_currency(row.total_return)} | "
                f"{row.expected_value:+.2f} | {_kelly_text(row, opts.bankroll)} |"
            )
        out.append("")

    if result.parlays:
        out += [
            "## Parlays",
            "",
            "| ID | Legs | Odds | Implied | Model true | Edge | Return | Profit | EV | Kelly | Corr | Venue |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|",
        ]
        for row in result.parlays[:max_parlays]:
            out.append(
                f"| {row.id} | {_parlay_legs_text(row, result.pick_by_id)} | "
                f"{format_american(row.combined_american_odds)} | {format_prob(row.implied_probability)} | "
                f"{format_prob(row.true_probability)} | {format_edge(row.edge)} | "
                f"{format_currency(row.total_return)} | {format_currency(row.profit)} | "
                f"{row.expected_value:+.2f} | {_kelly_text(row, opts.bankroll)} | {row.correlation_risk_score:.2f} | "
                f"{'single' if row.single_venue else 'mixed'} |"
            )
        out.append("")

    if result.ai_response:
        resp = result.ai_response
        out += ["## AI reasoning", "", resp.summary, ""]
        for tp in resp.top_picks:
            out.append(f"{tp.rank}. **{tp.id} {_pick_label(result.pick_by_id[tp.id])}** — {tp.rationale}")
            if tp.risk_notes:
                out.append(f"   - Risk: {tp.risk_notes}")
        out.append("")
        if resp.straight_bets_only or not resp.parlay_recommendations:
            out.append("**Parlay call:** straight bets only.")
        for rec in resp.parlay_recommendations:
            row = result.parlay_by_id[rec.id]
            out.append(
                f"**{rec.id} ({row.num_legs}-leg, {format_american(row.combined_american_odds)})** — "
                f"{format_currency(opts.stake)} returns {format_currency(row.total_return)} "
                f"(profit {format_currency(row.profit)}, model EV {row.expected_value:+.2f})"
            )
            out.append(f"- {rec.rationale}")
            out.append(f"- Key risks: {rec.key_risks}")
        out += ["", f"_{resp.responsible_gambling_note}_", ""]
    else:
        det_lines = [line for line in _deterministic_text(result).splitlines() if line]
        out += ["## Deterministic recommendation", "", "\n\n".join(det_lines), ""]

    if result.warnings:
        out += ["## Warnings", ""] + [f"- {w}" for w in result.warnings] + [""]
    return "\n".join(out)


# ---------------------------------------------------------------------------
# JSON
# ---------------------------------------------------------------------------

def to_record(result: "AdvisorResult") -> dict:
    """Serialisable record: inputs, request metadata, raw responses, validated output."""
    opts = asdict(result.options)
    opts["output_dir"] = str(opts["output_dir"])
    return {
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "game_date": result.game_date.isoformat(),
        "inputs": {
            "options": opts,
            "user_prompt": result.user_prompt,
            "filter_counts": result.stats.as_dict(),
            "odds_source_counts": result.odds_source_counts,
            "pregame_games": result.pregame_games,
            "payload": result.payload,
        },
        "request": {
            **(result.llm_meta or {}),
            "system_prompt": SYSTEM_PROMPT,
        },
        "ai_status": result.ai_status,
        "ai_error": result.ai_error,
        "raw_responses": result.raw_responses,
        "validated_output": result.ai_response.model_dump() if result.ai_response else None,
        "deterministic_recommendation": asdict(result.deterministic),
        "warnings": result.warnings,
    }


def save_result(result: "AdvisorResult", output_dir: Optional[Path] = None) -> tuple[Path, Path]:
    """Write the JSON record and markdown report; returns their paths."""
    from engine.advisor.run import DEFAULT_OUTPUT_DIR

    out_dir = Path(output_dir or DEFAULT_OUTPUT_DIR)
    out_dir.mkdir(parents=True, exist_ok=True)
    stem = f"{result.game_date.isoformat()}_{datetime.now().strftime('%H%M%S')}"
    json_path = out_dir / f"{stem}.json"
    md_path = out_dir / f"{stem}.md"
    n = 1
    while json_path.exists() or md_path.exists():
        json_path = out_dir / f"{stem}_{n}.json"
        md_path = out_dir / f"{stem}_{n}.md"
        n += 1

    from config import get_openai_config

    secret = [get_openai_config().api_key or ""]
    json_text = redact_sensitive_text(json.dumps(to_record(result), indent=2, default=str), secret)
    json_path.write_text(json_text, encoding="utf-8")
    md_path.write_text(redact_sensitive_text(to_markdown(result), secret), encoding="utf-8")
    result.json_path, result.md_path = json_path, md_path
    logger.info("Advisor output saved to %s", json_path)
    return json_path, md_path
