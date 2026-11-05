"""
NBA Prop AI  –  CLI entry point.

Usage:
    python main.py
    python main.py --date 2025-04-14
    python main.py --min-edge 0.07 --max-legs 2 --stake 250
    python main.py --prop-types points assists --min-odds -150 --max-odds 250

All arguments have sensible defaults so you can run with no flags at all.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import date, datetime

# Force UTF-8 output on Windows so Rich markup and Unicode chars don't crash
# cp1252 terminals with UnicodeEncodeError.
if sys.platform == "win32":
    import io
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import get_credentials
from domain.enums import PropType, SortField
from engine.bankroll_engine import apply_stake_to_all
from engine.parlay_builder import ParlayConstraints, build_parlays, leg_odds_match_constraints
from engine.ranking_engine import rank_parlays
from utils.date_utils import parse_date, today_eastern
from utils.formatting import (
    book_display_name,
    format_american,
    format_currency,
    format_edge,
    format_prob,
)
from utils.api_debug import redact_url
from utils.logging_utils import setup_logging

console = Console(legacy_windows=False)


# ---------------------------------------------------------------------------
# API debug interceptor
# ---------------------------------------------------------------------------

def install_api_debug_logger() -> None:
    """
    Monkey-patch requests.get so every outbound HTTP call prints:
      - the redacted URL (API keys hidden)
      - HTTP status code
      - a readable JSON preview of the response body
    """
    import requests as _requests

    _original_get = _requests.get

    def _debug_get(url, **kwargs):
        response = _original_get(url, **kwargs)
        safe_url = redact_url(url)

        status_color = "green" if response.ok else "red"
        console.rule(f"[bold yellow]API Response[/bold yellow]  [{status_color}]{response.status_code}[/{status_color}]  [dim]{safe_url}[/dim]")

        try:
            data = response.json()
            if isinstance(data, list):
                console.print(f"[dim]  ↳ list of {len(data)} items[/dim]")
                if data:
                    preview = data[:3]  # first 3 items
                    console.print_json(json.dumps(preview, default=str))
                    if len(data) > 3:
                        console.print(f"[dim]  ... {len(data) - 3} more items not shown[/dim]")
            elif isinstance(data, dict):
                # For large dicts, show all top-level keys and truncate long values
                preview: dict = {}
                for k, v in data.items():
                    if isinstance(v, list) and len(v) > 3:
                        preview[k] = v[:3] + [f"... ({len(v) - 3} more)"]
                    elif isinstance(v, str) and len(v) > 200:
                        preview[k] = v[:200] + "..."
                    else:
                        preview[k] = v
                console.print_json(json.dumps(preview, default=str))
            else:
                console.print(f"[dim]  ↳ {repr(data)[:300]}[/dim]")
        except Exception:
            body_preview = response.text[:500]
            console.print(f"[red]  ↳ Non-JSON response:[/red] {body_preview}")

        console.print()
        return response

    _requests.get = _debug_get
    console.print("[bold yellow]⚡ API debug mode ON[/bold yellow] — raw responses will be printed for every HTTP call.\n")


# ---------------------------------------------------------------------------
# Argument parsing
# ---------------------------------------------------------------------------

def build_advisor_parser() -> argparse.ArgumentParser:
    """Dedicated parser for `python main.py advisor ...` (its own defaults and --help)."""
    parser = argparse.ArgumentParser(
        prog="main.py advisor",
        description=(
            "AI Advisor: run the model, keep positive-edge props priced inside the odds band "
            "(Kalshi / Polymarket lines), build correlation-checked 2- and 3-leg parlays in Python, "
            "then ask OpenAI to reason over the top picks. All math is done in Python."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py advisor
  python main.py advisor --stake 100 --top 5
  python main.py advisor --date 2026-11-02 --no-llm
  python main.py advisor --min-odds -400 --max-odds -150 --prompt "Only 2-leg parlays please"

Requires OPENAI_API_KEY in .env unless --no-llm is used.
        """,
    )
    parser.add_argument("--date", help="Game date YYYY-MM-DD, or 'today' (default: today, US/Eastern)")
    parser.add_argument("--min-odds", type=int, default=-600, help="Most negative leg odds allowed (default -600, inclusive)")
    parser.add_argument("--max-odds", type=int, default=-200, help="Least negative leg odds allowed (default -200, inclusive)")
    parser.add_argument("--min-edge", type=float, default=0.0,
                        help="Edge must be strictly greater than this (default 0.0 = positive edge)")
    parser.add_argument("--top", type=int, default=5, help="Number of picks to keep (default 5)")
    parser.add_argument("--stake", type=float, default=100.0, help="Stake in $ for payout math (default 100)")
    parser.add_argument("--bankroll", type=float, default=None,
                        help="Bankroll in $; adds quarter-Kelly suggested stakes (capped at 5%% of bankroll)")
    parser.add_argument("--min-confidence", choices=["high", "medium", "low", "very_low"],
                        help="Minimum confidence tier per pick")
    prompt = parser.add_mutually_exclusive_group()
    prompt.add_argument("--prompt", help="Custom question for the AI (default: the standard top-5 / parlay question)")
    prompt.add_argument("--prompt-file", help="Read the custom question from a text file")
    parser.add_argument("--no-llm", action="store_true",
                        help="Skip OpenAI; print the deterministic recommendation only")
    parser.add_argument("--playoff", action="store_true", help="Treat slate as NBA playoffs")
    parser.add_argument("--no-save", action="store_true", help="Do not write data/advisor/*.json|.md")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    return parser


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "advisor" in argv:
        idx = argv.index("advisor")
        if idx == 0 or argv[idx - 1] not in ("--prompt", "--prompt-file", "--date"):
            args = build_advisor_parser().parse_args(argv[:idx] + argv[idx + 1:])
            args.command = "advisor"
            args.debug_api = False
            return args

    parser = argparse.ArgumentParser(
        description="NBA Prop AI – True Probability Parlay Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --min-edge 0.07 --max-legs 2 --stake 200
  python main.py --date 2025-04-14 --prop-types points assists
  python main.py --min-odds -150 --max-parlay-odds +500 --top 20
  python main.py advisor --stake 100        (AI Advisor; see `python main.py advisor --help`)
        """,
    )
    parser.add_argument(
        "command",
        nargs="?",
        choices=["scan", "backtest", "advisor", "fit-blend", "clv", "arb"],
        help=(
            "Daily scan, `backtest` for a one-game historical sample, `advisor` for the AI Advisor, "
            "`fit-blend` to refit market-blend weights and parlay correlations from graded backtests, "
            "`clv` to record closing prices / grade a date's saved predictions, "
            "or `arb` to scan every Kalshi + Polymarket market for depth-verified arbitrage."
        ),
    )
    parser.add_argument("--date", help="Game date YYYY-MM-DD, or 'today'")
    parser.add_argument("--min-edge", type=float, default=0.01,
                        help="Min edge per leg (default 0.01 = 1%%; market-anchored edges are usually 1-4%%)")
    parser.add_argument("--max-legs", type=int, default=3, help="Max legs per parlay (default 3)")
    parser.add_argument("--min-legs", type=int, default=2, help="Min legs per parlay (default 2)")
    parser.add_argument("--stake", type=float, default=100.0, help="Stake amount in $ (default 100)")
    parser.add_argument(
        "--min-odds",
        type=int,
        default=-600,
        help="Min per-leg American odds (default -600; includes heavy favorites)",
    )
    parser.add_argument("--max-odds", type=int, default=400, help="Max per-leg American odds (default +400)")
    parser.add_argument("--min-parlay-odds", type=int, default=-10000, help="Min combined parlay odds")
    parser.add_argument("--max-parlay-odds", type=int, default=100000, help="Max combined parlay odds")
    parser.add_argument("--top", type=int, default=10, help="Number of top parlays to display (default 10)")
    parser.add_argument(
        "--prop-types", nargs="+",
        choices=[p.value for p in PropType],
        help="Prop types to include (default: all)",
    )
    parser.add_argument(
        "--sort", choices=[s.value for s in SortField], default="edge",
        help="Parlay ranking field (default: edge)",
    )
    parser.add_argument("--min-confidence", choices=["high", "medium", "low", "very_low"],
                        help="Minimum confidence tier per leg")
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    parser.add_argument(
        "--debug-api", action="store_true",
        help="Print raw JSON response from every API call (for verifying data sources)",
    )
    parser.add_argument(
        "--playoff",
        action="store_true",
        help="Treat slate as NBA playoffs (slightly higher minute projections for rotation)",
    )
    parser.add_argument(
        "--max-credits",
        type=int,
        default=50,
        help="Credit cap for a one-game `backtest`. The one-game sample estimates 41 credits.",
    )
    parser.add_argument(
        "--games",
        type=int,
        default=1,
        help="Backtest size. 1 is the cheap sample. 30 runs the January 2026 cohort if the estimate is within 1,500 credits.",
    )
    parser.add_argument(
        "--experiment",
        choices=["interaction", "accuracy"],
        help=(
            "interaction: January fit, February check, March closed. "
            "accuracy: fit projection weights before 2026, then score March once."
        ),
    )
    parser.add_argument("--min-profit", type=float, default=5.0, help="`arb`: min locked $ profit (default 5)")
    parser.add_argument("--min-annual", type=float, default=0.10,
                        help="`arb`: min annualized return on capital (default 0.10 = 10%%)")
    parser.add_argument("--propose", action="store_true",
                        help="`arb`: list fuzzy cross-venue matches to review into data/arb_pairs.json")
    parser.add_argument("--watch", type=float, default=0, metavar="MINUTES",
                        help="`arb`: rescan every MINUTES until Ctrl+C (default: scan once)")
    return parser.parse_args(argv)


# ---------------------------------------------------------------------------
# Display helpers
# ---------------------------------------------------------------------------

def print_header(game_date: date) -> None:
    console.print(Panel.fit(
        f"[bold cyan]NBA Prop AI[/bold cyan]  |  True Probability Parlay Builder\n"
        f"[dim]Date: {game_date}  |  Powered by multi-factor stat models[/dim]",
        border_style="cyan",
    ))


def print_provider_status() -> None:
    creds = get_credentials()
    available = creds.available_providers
    missing = creds.missing_providers

    table = Table(title="Provider Status", box=box.SIMPLE, show_header=True)
    table.add_column("Provider", style="cyan")
    table.add_column("Status")

    for p in available:
        table.add_row(p, "[green]OK active[/green]")
    for p in missing:
        table.add_row(p, "[dim]-- no key (no data)[/dim]")

    console.print(table)


def _book_label(book, api_key: str = "") -> str:
    return book_display_name(book, api_key)


def print_props_table(props: list, top_n: int = 20) -> None:
    """Print top qualifying props as individual cards, sorted by edge."""
    sorted_props = sorted(props, key=lambda p: p.edge, reverse=True)[:top_n]
    if not sorted_props:
        console.print("[yellow]No qualifying props found.[/yellow]")
        return

    console.print(f"\n[bold white]-- Top {len(sorted_props)} Qualifying Props by Edge --[/bold white]\n")

    for i, p in enumerate(sorted_props, 1):
        edge_pct = p.edge * 100
        edge_color = "bold green" if edge_pct >= 8 else "yellow" if edge_pct >= 5 else "red"
        side_color = "cyan" if p.side.value.upper() == "OVER" else "magenta"
        book = _book_label(p.best_book, getattr(p, "best_book_key", "") or "")

        # Header line: rank, player, prop, side, line, odds, book
        header = (
            f"[dim]{i:>2}.[/dim]  "
            f"[bold white]{p.player_name}[/bold white]  "
            f"[cyan]{p.prop_type.value.title()}[/cyan]  "
            f"[{side_color}]{p.side.value.upper()}[/{side_color}] "
            f"[bold]{p.line:.1f}[/bold]  "
            f"[bold yellow]{format_american(p.sportsbook_odds)}[/bold yellow]  "
            f"[bold cyan]{book}[/bold cyan]"
        )
        # Detail line: projection, true prob, implied, edge, confidence
        base = getattr(p, "baseline_projection", None) or p.projected_value
        expm = getattr(p, "expected_minutes", 0.0)
        detail = (
            f"      Proj [bold]{p.projected_value:.1f}[/bold]  "
            f"(baseline ~{base:.1f}"
            + (f", exp {expm:.0f} min" if expm else "")
            + ")  "
            f"True [green]{format_prob(p.true_probability)}[/green]  "
            f"Impl [yellow]{format_prob(p.implied_probability)}[/yellow]  "
            f"Edge [{edge_color}]{format_edge(p.edge)}[/{edge_color}]  "
            f"[dim]{p.confidence.value}[/dim]"
        )
        console.print(header)
        console.print(detail)
        warns = getattr(p, "calibration_warnings", None) or []
        if warns:
            console.print(f"      [dim]Calibration: {', '.join(warns)}[/dim]")
        if i < len(sorted_props):
            console.print()


def print_parlays(parlays: list, top_n: int = 10) -> None:
    """Print ranked parlays with leg details."""
    if not parlays:
        console.print("[yellow]No qualifying parlays found with current constraints.[/yellow]")
        return

    console.print(f"\n[bold cyan]Top {min(top_n, len(parlays))} Ranked Parlays[/bold cyan]\n")

    for parlay in parlays[:top_n]:
        tags = " | ".join(parlay.risk_profile_tags) if parlay.risk_profile_tags else ""
        tag_str = f"  [magenta][{tags}][/magenta]" if tags else ""

        title = (
            f"#{parlay.edge_rank}  {parlay.num_legs}-leg Parlay  "
            f"Combined: {format_american(parlay.combined_american_odds)}  "
            f"Edge: [bold green]{format_edge(parlay.combined_edge)}[/bold green]"
            f"{tag_str}"
        )
        console.print(Panel(title, expand=False, border_style="blue"))

        for leg_i, leg in enumerate(parlay.legs, 1):
            side_color = "cyan" if leg.side.value.upper() == "OVER" else "magenta"
            book = _book_label(leg.sportsbook, getattr(leg, "best_book_key", "") or "")
            console.print(
                f"  [dim]{leg_i}.[/dim] [bold white]{leg.player_name}[/bold white]  "
                f"[cyan]{leg.prop_type.value.title()}[/cyan]  "
                f"[{side_color}]{leg.side.value.upper()}[/{side_color}] "
                f"[bold]{leg.line:.1f}[/bold]  "
                f"[bold yellow]{format_american(leg.sportsbook_odds)}[/bold yellow]  "
                f"[bold cyan]{book}[/bold cyan]"
            )
            console.print(
                f"       Proj [bold]{leg.projected_value:.1f}[/bold]  "
                f"True [green]{format_prob(leg.true_probability)}[/green]  "
                f"Edge [bold green]{format_edge(leg.edge)}[/bold green]"
            )

        # Payout summary
        if parlay.stake > 0:
            console.print(
                f"  Stake: {format_currency(parlay.stake)}  =>  "
                f"Return: [bold green]{format_currency(parlay.total_return)}[/bold green]  "
                f"Profit: [bold green]{format_currency(parlay.net_profit)}[/bold green]  "
                f"True Prob: {format_prob(parlay.combined_true_probability)}  "
                f"Corr Risk: {parlay.correlation_risk_score:.2f}\n"
            )


# ---------------------------------------------------------------------------
# AI Advisor
# ---------------------------------------------------------------------------

def run_advisor_command(args: argparse.Namespace, game_date: date) -> None:
    """`python main.py advisor` — deterministic picks/parlays + OpenAI reasoning."""
    from pathlib import Path

    from config import get_openai_config
    from engine.advisor.report import render_console
    from engine.advisor.run import AdvisorOptions, run_advisor

    prompt = args.prompt
    if args.prompt_file:
        try:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8")
        except OSError as exc:
            console.print(f"[red]Could not read --prompt-file:[/red] {exc}")
            sys.exit(1)
    if args.top < 1 or args.stake <= 0 or (args.bankroll is not None and args.bankroll <= 0):
        console.print("[red]--top must be ≥ 1, and --stake / --bankroll must be > 0.[/red]")
        sys.exit(1)

    cfg = get_openai_config()
    if not args.no_llm and not cfg.is_configured:
        console.print(
            "[yellow]OPENAI_API_KEY is not set — the AI reasoning step will be skipped and the "
            "deterministic recommendation shown instead. Add it to .env or pass --no-llm.[/yellow]"
        )

    print_header(game_date)
    console.print(
        "[cyan]Scanning slate (odds from Kalshi / Polymarket; pregame games only)...[/cyan]"
    )
    options = AdvisorOptions(
        min_odds=args.min_odds,
        max_odds=args.max_odds,
        min_edge=args.min_edge,
        top_n=args.top,
        stake=args.stake,
        bankroll=args.bankroll,
        min_confidence=args.min_confidence,
        prompt=prompt,
        use_llm=not args.no_llm,
        save=not args.no_save,
        is_playoff=args.playoff,
    )
    if options.use_llm and cfg.is_configured:
        console.print(f"[dim]AI model: {cfg.model} (reasoning effort {cfg.reasoning_effort})[/dim]")
    result = run_advisor(game_date, options)
    if result.pregame_games == 0:
        console.print(
            f"[yellow]No pregame games on {game_date} (all started/final or none scheduled). "
            "Try --date with the next game day.[/yellow]"
        )
    render_console(result, console)


def run_fit_blend_command() -> None:
    """Refit market-blend weights and parlay correlations from graded backtests."""
    from engine.market_blend_fit import DEFAULT_SOURCES, fit_blend
    from engine.parlay_copula import DEFAULT_CORR_PATH, fit_correlations
    from engine.market_blend import DEFAULT_PARAMS_PATH

    try:
        blend = fit_blend(DEFAULT_SOURCES)
        corr = fit_correlations(DEFAULT_SOURCES)
    except ValueError as exc:
        console.print(f"[red]{exc}[/red]")
        sys.exit(1)

    t = Table(title=f"Market blend ({blend['n_rows']} graded rows, {blend['n_dates']} dates)", box=box.SIMPLE_HEAVY)
    for col in ("Prop", "Rows", "Model weight", "Clip", "Market Brier", "Model Brier",
                "CV blend Brier", "CV Δ vs market", "Dates improved"):
        t.add_column(col, justify="left" if col == "Prop" else "right")
    for prop, m in blend["metrics"].items():
        w = blend["weights"].get(prop, blend["default"] if prop == "all" else {})
        t.add_row(
            prop, str(m["n"]), f"{w.get('weight', m['weight']):.3f}",
            "none" if m["clip"] >= 99 else f"{m['clip']:.2f}",
            f"{m['market_brier']:.4f}", f"{m['model_brier']:.4f}", f"{m['cv_blend_brier']:.4f}",
            f"{m['cv_delta_vs_market']:+.5f}", f"{m['dates_improved']}/{m['n_dates']}",
        )
    console.print(t)
    console.print(
        f"[dim]Unlisted prop types use weight {blend['default']['weight']:.3f}. "
        f"Weight 0 = model did not beat the market out of sample, so the price is pure consensus.[/dim]"
    )
    sp = ", ".join(f"{k} {v:+.2f}" for k, v in corr["same_player"].items())
    sg = ", ".join(f"{k} {v:+.2f}" for k, v in corr["same_game"].items() if v) or "none significant"
    console.print(f"Same-player stat correlations: {sp}")
    console.print(f"Same-game, different-player correlations: {sg}")
    console.print(f"[green]Saved[/green] {DEFAULT_PARAMS_PATH} and {DEFAULT_CORR_PATH}")


def run_clv_command(game_date) -> None:
    """Record closing prices for pregame rows and grade final games for *game_date*."""
    from engine.clv import grade_predictions, load_ledger, record_closing

    if not load_ledger(game_date):
        console.print(f"[yellow]No saved predictions for {game_date}. Run a scan first.[/yellow]")
        return
    rec = record_closing(game_date)
    console.print(
        f"Closing prices: {rec['updated']} rows updated for {rec.get('pregame_games', 0)} pregame games "
        f"({rec.get('closing_rows', 0)} stored) → {rec['path']}"
    )
    summary = grade_predictions(game_date)
    if not summary.get("final_games"):
        console.print("[dim]No final games yet; grading skipped. Re-run after games finish.[/dim]")
        return
    t = Table(title=f"Graded {game_date} ({summary['final_games']} final games)", box=box.SIMPLE_HEAVY)
    for col in ("Set", "Rows", "Decided", "Hit rate", "Avg prob", "Brier model", "Brier market",
                "Flat ROI/bet", "Avg CLV", "CLV>0"):
        t.add_column(col, justify="left" if col == "Set" else "right")
    for name in ("all", "positive_edge"):
        s = summary[name]

        def f(k, fmt):
            return fmt.format(s[k]) if s.get(k) is not None else "–"

        t.add_row(name, str(s["n"]), str(s["decided"]), f("hit_rate", "{:.1%}"),
                  f("avg_model_probability", "{:.1%}"), f("brier_model", "{:.4f}"),
                  f("brier_market", "{:.4f}"), f("flat_roi_per_bet", "{:+.3f}"),
                  f("avg_clv", "{:+.4f}"), f("clv_positive_share", "{:.0%}"))
    console.print(t)
    console.print(f"[green]Saved[/green] {summary['path']}")


def run_arb_command(args) -> None:
    """Scan all open Kalshi + Polymarket markets for depth-verified, fee-inclusive arbitrage."""
    from arbitrage.scanner import propose, scan

    if args.propose:
        t = Table(title="Candidate pairs to review (NOT arbs)", box=box.SIMPLE_HEAVY)
        for col in ("Score", "Kalshi", "Polymarket", "Kalshi ticker", "Polymarket id"):
            t.add_column(col)
        for r in propose(limit=args.top):
            t.add_row(str(r["score"]), r["kalshi"], r["polymarket"], r["kalshi_ticker"], r["polymarket_id"])
        console.print(t)
        console.print("[dim]Read both rulebooks, then add confirmed pairs to data/arb_pairs.json.[/dim]")
        return
    with console.status("Scanning every open Kalshi + Polymarket market (about a minute)..."):
        res = scan(min_profit=args.min_profit, min_annual=args.min_annual)
    console.print(f"[bold]{datetime.now():%H:%M:%S}[/bold]  " +
                  " | ".join(f"{k.replace('_', ' ')}: {v:,}" for k, v in res["stats"].items()))
    for title, rows in (("Arbitrage (passes every filter)", res["arbs"]),
                        ("Depth-real but below --min-profit/--min-annual", res["near"][: args.top])):
        t = Table(title=f"{title}: {len(rows)}", box=box.SIMPLE_HEAVY)
        for col in ("Kind", "Event", "Contracts", "Cost", "Locked $", "ROI", "Annual", "Closes", "Legs"):
            t.add_column(col, justify="right" if col in {"Contracts", "Cost", "Locked $", "ROI", "Annual"} else "left")
        for r in rows:
            t.add_row(r["kind"], r["title"], f"{r['contracts']:,.0f}", f"${r['cost']:,.2f}", f"${r['profit']:,.2f}",
                      f"{r['roi']:.2%}", f"{r['annualized']:.0%}", (r["closes"] or "")[:16], "\n".join(r["legs"]))
        console.print(t)
    console.print("[dim]Prices move: re-check each book before every leg. Read both venues' rules for cross-venue legs.[/dim]")


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    setup_logging(level="DEBUG" if args.verbose else "WARNING")

    if args.command == "arb":
        while True:
            run_arb_command(args)
            if not args.watch or args.propose:
                return
            console.print(f"[dim]Next scan in {args.watch:g} min; hits are logged to data/arbs/. Ctrl+C to stop.[/dim]")
            time.sleep(args.watch * 60)

    if args.debug_api:
        install_api_debug_logger()

    if args.date and args.date.strip().lower() != "today":
        try:
            game_date = parse_date(args.date)
        except (ValueError, TypeError):
            console.print(
                f"[red]Invalid date format:[/red] [bold]{args.date}[/bold]  "
                f"— expected YYYY-MM-DD (e.g. 2026-04-16)"
            )
            sys.exit(1)
    else:
        game_date = today_eastern()

    if game_date > today_eastern():
        console.print(
            f"[yellow]Future date selected ({game_date}).[/yellow] "
            f"Data may be incomplete or unavailable for upcoming games."
        )

    if args.command == "advisor":
        run_advisor_command(args, game_date)
        return

    if args.command == "fit-blend":
        run_fit_blend_command()
        return

    if args.command == "clv":
        run_clv_command(game_date)
        return

    if args.command == "backtest" and getattr(args, "experiment", None) == "interaction":
        from engine.backtest.experiment import estimate_february_cost, run_interaction_experiment

        print_header(game_date)
        planned = estimate_february_cost()
        console.print(
            f"[cyan]Interaction experiment.[/cyan] January is training. "
            f"February cold estimate {planned['total_credits']} credits "
            f"for {planned['n_games']} games on {planned['n_dates']} dates. March is not opened."
        )
        report = run_interaction_experiment()
        change = report.get("production_change") or {}
        console.print(change.get("reason", ""))
        february = report.get("february") or {}
        console.print(f"February games: {february.get('n_games')}  Dates: {february.get('n_dates')}")
        for name, block in (february.get("variants") or {}).items():
            console.print(f"  {name}: n={block.get('n')} brier={block.get('brier')} log_loss={block.get('log_loss')}")
        effect = february.get("e_minus_a") or {}
        if effect:
            console.print(
                "E minus A Brier: "
                f"{effect.get('mean_brier_difference')}  date SE {effect.get('date_cluster_se')}"
            )
        console.print("[dim]Wrote data/backtests/interaction_experiment.json[/dim]")
        return

    if args.command == "backtest" and getattr(args, "experiment", None) == "accuracy":
        from engine.accuracy.run import estimate_march_cost, run_accuracy_experiment

        print_header(game_date)
        planned = estimate_march_cost()
        console.print(
            f"[cyan]Accuracy experiment.[/cyan] Weights are chosen on 2025 games before January. "
            f"March cold estimate {planned['total_credits']} credits "
            f"for {planned['n_games']} games. Production weights are not changed."
        )
        report = run_accuracy_experiment()
        console.print((report.get("production_change") or {}).get("reason", ""))
        march = report.get("march_probability") or {}
        console.print(f"March status: {march.get('status')}  rows: {march.get('n')}")
        for name, block in (march.get("models") or {}).items():
            console.print(
                f"  {name}: n={block.get('n')} brier={block.get('brier')} log_loss={block.get('log_loss')}"
            )
        effect = march.get("c_minus_a") or {}
        if effect:
            console.print(
                "C minus A Brier: "
                f"{effect.get('mean_brier_difference')}  date SE {effect.get('date_cluster_se')}"
            )
        console.print("[dim]Wrote data/backtests/accuracy_experiment.json[/dim]")
        return

    if args.command == "backtest" and int(getattr(args, "games", 1) or 1) > 1:
        from engine.backtest.expanded import estimate_expanded_cost, run_expanded_backtest

        print_header(game_date)
        planned = estimate_expanded_cost(8, args.games)
        console.print(
            f"[cyan]Expanded backtest.[/cyan] Cold estimate {planned['total_credits']} credits "
            f"for {args.games} games before any prop download. "
            "Prices are not fabricated, and production weights are not changed."
        )
        if planned["total_credits"] > 1500:
            console.print(
                f"[yellow]Estimate {planned['total_credits']} is above 1,500 credits. "
                "No prices will be downloaded until that spend is approved.[/yellow]"
            )
        report = run_expanded_backtest(max_games=args.games)
        coverage = report.get("coverage") or {}
        console.print(f"Status: {coverage.get('historical_odds')}")
        if coverage.get("reason"):
            console.print(coverage["reason"])
        console.print(
            f"Games: {report.get('n_games')}  Dates: {report.get('n_dates')}  Rows: {report.get('n_rows')}"
        )
        for variant in report.get("variants_over") or []:
            console.print(
                f"  {variant['variant']}: n={variant['n']} brier={variant['brier']} log_loss={variant['log_loss']}"
            )
        effect = report.get("positional_effect") or {}
        if effect:
            console.print(
                "Positional Brier difference (B minus A): "
                f"{effect.get('mean_brier_difference')}  "
                f"game SE {effect.get('game_cluster_se')}  date SE {effect.get('date_cluster_se')}"
            )
        console.print(f"[dim]{report.get('parameter_update', {}).get('reason', '')}[/dim]")
        console.print("[dim]Wrote data/backtests/expanded_report.json[/dim]")
        return

    if args.command == "backtest":
        from engine.backtest.runner import run_sample_backtest

        print_header(game_date)
        console.print(
            "[cyan]Historical sample.[/cyan] One events-list call, then one game "
            "if the credit cap allows. Prices are not fabricated when the call fails."
        )
        report = run_sample_backtest(game_date, max_credits=args.max_credits)
        coverage = report.get("coverage") or {}
        console.print(f"Status: {coverage.get('historical_odds')}")
        if coverage.get("reason"):
            console.print(coverage["reason"])
        console.print(f"Games graded: {report.get('n_games')}  Over rows: {report.get('n_over_rows')}")
        for variant in report.get("variants") or []:
            console.print(
                f"  {variant['variant']}: n={variant['n']} brier={variant['brier']} "
                f"log_loss={variant['log_loss']}"
            )
        if report.get("sample_warning"):
            console.print(f"[yellow]{report['sample_warning']}[/yellow]")
        console.print(f"[dim]{report.get('parameter_update', {}).get('reason', '')}[/dim]")
        console.print("[dim]Wrote data/backtests/latest_report.json[/dim]")
        return

    print_header(game_date)
    print_provider_status()

    prop_types = [PropType(pt) for pt in args.prop_types] if args.prop_types else None

    # Scan slate
    console.print(f"\n[cyan]Scanning NBA slate for {game_date}...[/cyan]")
    from engine.board import scan_board

    board = scan_board(game_date, prop_types=prop_types, is_playoff=args.playoff)
    all_props = board.props
    if board.odds_source_counts or board.odds_source_errors:
        parts = [
            f"{name}={count}" for name, count in sorted(board.odds_source_counts.items())
        ]
        console.print(
            f"[dim]Prop lines by source: {', '.join(parts) or 'none'}; "
            f"game-line sides: {len(board.game_lines)}[/dim]"
        )
        for name, err in sorted(board.odds_source_errors.items()):
            console.print(f"[yellow]{name} odds error: {err}[/yellow]")
    n = len(all_props)
    console.print(
        f"[green]Evaluated {n} prop line{'s' if n != 1 else ''}.[/green]"
    )
    cand = board.candidate_status or {}
    if cand.get("status") == "ok":
        edges = cand.get("edges") or {}
        console.print(
            f"[dim]Candidate model {cand.get('version')}: scored {cand.get('attached', 0)} lines, "
            f"sets the edge on {edges.get('applied', 0)}.[/dim]"
        )
    elif cand:
        console.print(f"[yellow]Candidate model not used: {cand.get('status')} {cand.get('reason', '')}[/yellow]")
    if board.agreement.get("model_disagrees"):
        console.print(
            f"[dim]{board.agreement['model_disagrees']} of {board.agreement['positive_edge']} positive-edge "
            "lines have an edge the model itself does not back (flagged, confidence lowered).[/dim]"
        )
    if n:
        console.print(
            "[dim]Pregame only: games in progress or final are excluded from the slate.[/dim]"
        )

    if not all_props:
        if game_date > today_eastern():
            console.print(
                f"[yellow]No props found for {game_date}.[/yellow] "
                f"This is a future date — lines may not be posted yet."
            )
        elif board.raw_games > 0 and board.pregame_games == 0:
            console.print(
                f"[yellow]No pregame props for {game_date}.[/yellow] "
                f"The slate had [bold]{board.raw_games}[/bold] game(s), "
                f"but every one had already started or finished — only upcoming games are evaluated."
            )
        else:
            console.print(
                f"[red]No props found for {game_date}.[/red] "
                f"No games from the schedule provider, or odds/lines missing. "
                f"Check API keys and that games are scheduled for this date."
            )
        sys.exit(0)

    # Filter qualifying props (same leg-odds band as parlays)
    qualifying = [
        p for p in all_props
        if p.edge >= args.min_edge
        and leg_odds_match_constraints(p.sportsbook_odds, args.min_odds, args.max_odds)
    ]
    console.print(
        f"[green]{len(qualifying)} props with >={format_edge(args.min_edge)} edge "
        f"and leg odds in [{args.min_odds}, {args.max_odds}] (American).[/green]"
    )

    print_props_table(qualifying, top_n=20)

    from engine.prediction_ledger import save_pregame_predictions
    ledger_path = save_pregame_predictions(game_date, all_props)
    console.print(f"[dim]Saved pregame snapshot: {ledger_path}[/dim]")

    # Build parlays
    console.print(f"\n[cyan]Building parlays (max {args.max_legs} legs, min edge >= {format_edge(args.min_edge)})...[/cyan]")
    constraints = ParlayConstraints(
        min_edge=args.min_edge,
        max_legs=args.max_legs,
        min_legs=args.min_legs,
        min_leg_odds=args.min_odds,
        max_leg_odds=args.max_odds,
        min_parlay_odds=args.min_parlay_odds,
        max_parlay_odds=args.max_parlay_odds,
        min_confidence=args.min_confidence,
        allowed_prop_types=prop_types,
        max_results=500,
    )
    parlays = build_parlays(all_props, constraints)
    console.print(f"[green]Generated {len(parlays)} valid parlays.[/green]")

    if not parlays:
        console.print("[yellow]No parlays met the criteria. Try relaxing constraints.[/yellow]")
        sys.exit(0)

    # Rank
    sort_field = SortField(args.sort)
    ranked = rank_parlays(parlays, sort_by=sort_field, top_n=args.top * 5)

    # Apply stake
    apply_stake_to_all(ranked, args.stake)

    # Print results
    print_parlays(ranked, top_n=args.top)

    console.print(
        f"\n[dim]Total valid parlays: {len(parlays)} | "
        f"Showing top {min(args.top, len(ranked))} by {args.sort}[/dim]"
    )


if __name__ == "__main__":
    main()
