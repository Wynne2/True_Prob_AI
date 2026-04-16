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
from datetime import date

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich import box

from config import get_credentials
from domain.enums import PropType, SortField
from engine.bankroll_engine import apply_stake_to_all
from engine.parlay_builder import ParlayConstraints, build_parlays
from engine.ranking_engine import rank_parlays
from engine.slate_scanner import SlateScanner
from utils.date_utils import parse_date, today_eastern
from utils.formatting import format_american, format_edge, format_prob, format_currency
from utils.api_debug import redact_url
from utils.logging_utils import setup_logging

console = Console()


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

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="NBA Prop AI – True Probability Parlay Builder",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python main.py
  python main.py --min-edge 0.07 --max-legs 2 --stake 200
  python main.py --date 2025-04-14 --prop-types points assists
  python main.py --min-odds -150 --max-parlay-odds +500 --top 20
        """,
    )
    parser.add_argument("--date", help="Game date YYYY-MM-DD (default: today)")
    parser.add_argument("--min-edge", type=float, default=0.05, help="Min edge per leg (default 0.05 = 5%%)")
    parser.add_argument("--max-legs", type=int, default=3, help="Max legs per parlay (default 3)")
    parser.add_argument("--min-legs", type=int, default=2, help="Min legs per parlay (default 2)")
    parser.add_argument("--stake", type=float, default=100.0, help="Stake amount in $ (default 100)")
    parser.add_argument("--min-odds", type=int, default=-200, help="Min per-leg American odds (default -200)")
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
    return parser.parse_args()


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


def print_props_table(props: list, top_n: int = 20) -> None:
    """Print top qualifying props sorted by edge."""
    sorted_props = sorted(props, key=lambda p: p.edge, reverse=True)[:top_n]
    if not sorted_props:
        console.print("[yellow]No qualifying props found.[/yellow]")
        return

    table = Table(title=f"Top {top_n} Qualifying Props by Edge", box=box.ROUNDED)
    table.add_column("Player", style="bold white", min_width=20)
    table.add_column("Prop", style="cyan")
    table.add_column("Line", justify="right")
    table.add_column("Side", justify="center")
    table.add_column("Proj", justify="right")
    table.add_column("True%", justify="right", style="green")
    table.add_column("Impl%", justify="right", style="yellow")
    table.add_column("Edge", justify="right", style="bold green")
    table.add_column("Odds", justify="right")
    table.add_column("Book", style="dim")
    table.add_column("Conf", style="dim")

    for p in sorted_props:
        edge_color = "green" if p.edge >= 0.08 else "yellow" if p.edge >= 0.05 else "red"
        table.add_row(
            p.player_name,
            p.prop_type.value,
            f"{p.line:.1f}",
            p.side.value.upper(),
            f"{p.projected_value:.1f}",
            format_prob(p.true_probability),
            format_prob(p.implied_probability),
            f"[{edge_color}]{format_edge(p.edge)}[/{edge_color}]",
            format_american(p.sportsbook_odds),
            p.best_book.value,
            p.confidence.value,
        )
    console.print(table)


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

        # Legs table
        leg_table = Table(box=box.SIMPLE, show_header=True, padding=(0, 1))
        leg_table.add_column("Player", min_width=22)
        leg_table.add_column("Prop")
        leg_table.add_column("Line", justify="right")
        leg_table.add_column("Side", justify="center")
        leg_table.add_column("Proj", justify="right")
        leg_table.add_column("True%", justify="right")
        leg_table.add_column("Edge", justify="right", style="green")
        leg_table.add_column("Odds", justify="right")

        for leg in parlay.legs:
            leg_table.add_row(
                leg.player_name,
                leg.prop_type.value,
                f"{leg.line:.1f}",
                leg.side.value.upper(),
                f"{leg.projected_value:.1f}",
                format_prob(leg.true_probability),
                format_edge(leg.edge),
                format_american(leg.sportsbook_odds),
            )
        console.print(leg_table)

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
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    args = parse_args()
    setup_logging(level="DEBUG" if args.verbose else "WARNING")

    if args.debug_api:
        install_api_debug_logger()

    if args.date:
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

    print_header(game_date)
    print_provider_status()

    prop_types = [PropType(pt) for pt in args.prop_types] if args.prop_types else None

    # Scan slate
    console.print(f"\n[cyan]Scanning NBA slate for {game_date}...[/cyan]")
    scanner = SlateScanner()
    all_props = scanner.scan(game_date, prop_types=prop_types)
    console.print(f"[green]Evaluated {len(all_props)} prop lines.[/green]")

    if not all_props:
        if game_date > today_eastern():
            console.print(
                f"[yellow]No props found for {game_date}.[/yellow] "
                f"This is a future date — lines may not be posted yet."
            )
        else:
            console.print(
                f"[red]No props found for {game_date}.[/red] "
                f"Check your data providers or API keys. "
                f"There may be no games scheduled on this date."
            )
        sys.exit(0)

    # Filter qualifying props
    qualifying = [p for p in all_props if p.edge >= args.min_edge]
    console.print(f"[green]{len(qualifying)} props with >={format_edge(args.min_edge)} edge.[/green]")

    print_props_table(qualifying, top_n=20)

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
