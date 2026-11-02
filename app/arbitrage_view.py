"""Arbitrage tab: scans every open Kalshi + Polymarket market on demand."""

from __future__ import annotations

import pandas as pd
import streamlit as st

from app import ui


def render_arbitrage_tab() -> None:
    ui.section("Arbitrage", "fee-inclusive, order-book-verified; no model, no AI")
    st.caption(
        "Checks every open Kalshi and Polymarket event for outcome baskets that pay out more than they cost "
        "after fees, then walks the live order books to size them. Prices move: re-check each book before "
        "every leg, and read both venues' rules before trading across them."
    )
    with st.container(border=True):
        c1, c2 = st.columns(2)
        min_profit = c1.number_input("Minimum locked profit ($)", min_value=0.0, value=5.0, step=1.0)
        min_annual = c2.number_input("Minimum annualized return", min_value=0.0, value=0.10, step=0.05, format="%.2f")
        run = st.button("Scan Kalshi + Polymarket", icon=":material/currency_exchange:")
    if run:
        from arbitrage.scanner import scan

        with st.spinner("Scanning every open market (about a minute)..."):
            st.session_state.arb_result = scan(min_profit=min_profit, min_annual=min_annual)
    res = st.session_state.get("arb_result")
    if not res:
        st.info("Press scan to check the markets. Nothing is fetched until you do.", icon=":material/radar:")
        return
    st.caption(" | ".join(f"{k.replace('_', ' ')}: {v:,}" for k, v in res["stats"].items()))
    for title, rows in (("Arbitrage (passes every filter)", res["arbs"]), ("Real but below your minimums", res["near"])):
        st.subheader(f"{title}: {len(rows)}")
        if rows:
            df = pd.DataFrame(rows)[["kind", "title", "contracts", "cost", "profit", "roi", "annualized", "closes", "legs"]]
            df["legs"] = df["legs"].map(" | ".join)
            st.dataframe(df, hide_index=True, use_container_width=True)
