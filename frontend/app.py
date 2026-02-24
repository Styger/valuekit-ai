"""
ValueKit AI - Streamlit Frontend
WIPRO Edition: session security, input validation, structured error handling
Each mode calls the backend directly and renders its own results.
"""

import logging
import re
import sys
from pathlib import Path

import pandas as pd
import streamlit as st

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import PIPELINE_VERSION
from backend.valuekit_ai.config.analysis_config import AnalysisConfig
from backend.valuekit_ai.core.valuekit_integration import ValueKitAnalyzer
from backend.logic.mos import calculate_mos_value_from_ticker
from backend.logic import profitability
from backend.logic.tencap import _get_ten_cap_result
from backend.logic.pbt import calculate_pbt_from_ticker, calculate_pbt_with_comparison
from backend.logic.cagr import (
    _mos_growth_estimate_auto,
    run_analysis as cagr_run_analysis,
)
from backend.api import fmp_api

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

MAX_ANALYSES_PER_SESSION = 10

st.set_page_config(
    page_title="ValueKit AI",
    page_icon="📊",
    layout="wide",
)
# ─── Combined Score ───────────────────────────────────────────────────────────


def _compute_combined_score(
    result, w_mos, w_tencap, w_pbt, quant_vs_moat, mos_pct=0.50
):
    """Weighted combined score 0-100. Quant = price vs fair value per method."""

    def _price_score(current: float, fair: float) -> float:
        """100 = deeply undervalued, 0 = severely overvalued."""
        if not current or not fair or fair <= 0 or current <= 0:
            return None
        ratio = current / fair
        return max(0.0, min(100.0, (2.0 - ratio) * 50))

    mos = result.get("mos_result") or {}
    tc = result.get("tencap_result") or {}
    pb = result.get("pbt_result") or {}
    ai = result.get("ai_decision") or {}

    current = mos.get("Current Stock Price")
    scores = {
        "mos": _price_score(current, mos.get("Fair Value Today")),
        "tencap": _price_score(
            tc.get("current_stock_price"),
            tc.get("ten_cap_fair_value") * (1 - mos_pct)
            if tc.get("ten_cap_fair_value")
            else None,
        ),
        "pbt": _price_score(
            pb.get("current_stock_price"),
            pb.get("fair_value") * (1 - mos_pct) if pb.get("fair_value") else None,
        ),
    }

    parts = [
        (scores["mos"], w_mos),
        (scores["tencap"], w_tencap),
        (scores["pbt"], w_pbt),
    ]
    effective_w = sum(wt for s, wt in parts if s is not None)
    quant_score = (
        round(sum(s * wt for s, wt in parts if s is not None) / effective_w, 1)
        if effective_w > 0
        else None
    )

    moat_score = ai.get("overall_score") if isinstance(ai, dict) else None

    q = quant_vs_moat / 100
    if quant_score is not None and moat_score is not None:
        combined = round(quant_score * q + moat_score * (1 - q), 1)
    elif quant_score is not None:
        combined = quant_score
    elif moat_score is not None:
        combined = float(moat_score)
    else:
        combined = None

    rec = (
        "STRONG BUY"
        if combined is not None and combined >= 75
        else "BUY"
        if combined is not None and combined >= 55
        else "HOLD"
        if combined is not None and combined >= 40
        else "PASS"
        if combined is not None
        else "N/A"
    )

    log.debug(
        "[app][combined_score] quant=%s moat=%s combined=%s rec=%s",
        quant_score,
        moat_score,
        combined,
        rec,
    )
    return {
        "combined": combined,
        "quant_score": quant_score,
        "moat_score": moat_score,
        "component_scores": scores,
        "recommendation": rec,
    }


# ─── Session State ───────────────────────────────────────────────────────────


def _init_session_state():
    if "analysis_count" not in st.session_state:
        st.session_state["analysis_count"] = 0


def _check_session_limit():
    if st.session_state["analysis_count"] >= MAX_ANALYSES_PER_SESSION:
        st.error(
            f"Session limit of {MAX_ANALYSES_PER_SESSION} analyses reached. "
            "Please start a new session."
        )
        st.stop()


# ─── Input Validation ────────────────────────────────────────────────────────


def _validate_ticker(ticker: str) -> str:
    """Validate ticker at entry point — WIPRO security requirement."""
    cleaned = ticker.strip().upper()
    if not re.match(r"^[A-Z]{1,5}$", cleaned):
        raise ValueError(
            f"Invalid ticker '{ticker}'. Expected 1–5 uppercase letters (e.g. AAPL)."
        )
    return cleaned


# ─── Sidebar ─────────────────────────────────────────────────────────────────


def _render_sidebar() -> str:
    st.sidebar.title("📊 ValueKit AI")
    st.sidebar.caption(f"Value Investing Analysis — Pipeline v{PIPELINE_VERSION}")
    st.sidebar.markdown("---")

    selected = st.sidebar.selectbox(
        "Select Mode",
        [
            "📊 Overview",
            "📈 CAGR Growth Estimate",
            "🛡️ Margin of Safety (MOS)",
            "💰 Profitability Metrics",
            "⏱️ Payback Time (PBT)",
            "🤖 AI Moat Analysis",
        ],
        key="nav_mode",
    )

    st.sidebar.markdown("---")
    st.sidebar.caption(f"Pipeline Version: {PIPELINE_VERSION}")
    st.sidebar.caption(
        f"Analyses this session: "
        f"{st.session_state.get('analysis_count', 0)}/{MAX_ANALYSES_PER_SESSION}"
    )
    return selected


# ─── Shared ticker input helper ──────────────────────────────────────────────


def _ticker_input(key: str, default: str = "AAPL") -> str:
    return st.text_input("Ticker Symbol", value=default, max_chars=5, key=key)


def _year_input(key: str, label: str = "Base Year", default: int = 2024) -> int:
    return st.number_input(
        label, min_value=2010, max_value=2030, value=default, step=1, key=key
    )


# ─── Recommendation color helper ─────────────────────────────────────────────


def _show_recommendation(rec: str):
    rec_lower = rec.lower()
    if "strong buy" in rec_lower or "buy" in rec_lower:
        st.success(f"✅ {rec}")
    elif "hold" in rec_lower:
        st.warning(f"⚖️ {rec}")
    else:
        st.error(f"❌ {rec}")


# ─── CAGR Page ───────────────────────────────────────────────────────────────


def _page_cagr():
    st.header("📈 CAGR Growth Estimate")
    st.caption(
        "Estimates compound annual growth rate across key metrics over rolling periods."
    )

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("cagr_ticker")
        end_year = _year_input("cagr_end_year", "End Year", 2024)
    with col2:
        period = st.selectbox(
            "CAGR Period (years)", [3, 5, 7, 10], index=1, key="cagr_period"
        )
        start_year = st.number_input(
            "Start Year",
            min_value=2000,
            max_value=2024,
            value=end_year - 10,
            step=1,
            key="cagr_start",
        )

    st.markdown("**Include Metrics**")
    c1, c2, c3, c4, c5 = st.columns(5)
    inc_book = c1.checkbox("Book Value", value=True, key="cagr_book")
    inc_eps = c2.checkbox("EPS", value=True, key="cagr_eps")
    inc_rev = c3.checkbox("Revenue", value=True, key="cagr_rev")
    inc_cf = c4.checkbox("Cashflow", value=True, key="cagr_cf")
    inc_fcf = c5.checkbox("FCF", value=True, key="cagr_fcf")

    if st.button("Run CAGR Analysis", type="primary", key="cagr_run"):
        _check_session_limit()
        try:
            ticker = _validate_ticker(ticker_input)
        except ValueError as e:
            st.error(str(e))
            return

        log.info(
            "[app][cagr_start] ticker=%s start=%d end=%d period=%d pipeline_version=%s",
            ticker,
            start_year,
            end_year,
            period,
            PIPELINE_VERSION,
        )

        with st.spinner(f"Fetching CAGR data for {ticker}..."):
            try:
                required_years = end_year - start_year
                data, mos_input = fmp_api.get_year_data_by_range(
                    ticker, start_year, years=required_years
                )

                if not data or not mos_input:
                    st.error(
                        f"No data available for {ticker} ({start_year}–{end_year})."
                    )
                    return

                df_raw = pd.DataFrame(data)
                # Detect year column
                year_col = next(
                    (c for c in df_raw.columns if c.strip().lower() == "year"), None
                )
                if year_col is None:
                    st.error("Could not detect year column in data.")
                    return

                year_range = sorted(df_raw[year_col].astype(int).tolist())
                earliest_year = min(year_range)
                latest_year = max(year_range)

                results_list = []
                for s in range(earliest_year, latest_year - period + 1):
                    e = s + period
                    try:
                        result = _mos_growth_estimate_auto(
                            data_dict=mos_input,
                            start_year=s,
                            end_year=e,
                            period_years=period,
                            known_start_year=earliest_year,
                            include_book=inc_book,
                            include_eps=inc_eps,
                            include_revenue=inc_rev,
                            include_cashflow=inc_cf,
                            include_fcf=inc_fcf,
                        )
                        result["Period"] = f"{s}–{e}"
                        results_list.append(result)
                    except ValueError as ve:
                        log.warning("[app][cagr_skip] period=%d-%d reason=%s", s, e, ve)

                if not results_list:
                    st.warning(
                        "No valid CAGR periods found. Try a shorter period or different date range."
                    )
                    return

                st.session_state["analysis_count"] += 1

                # Build display DataFrame
                cols = ["Period"]
                if inc_book:
                    cols.append("book")
                if inc_eps:
                    cols.append("eps")
                if inc_rev:
                    cols.append("revenue")
                if inc_cf:
                    cols.append("cashflow")
                if inc_fcf:
                    cols.append("fcf")
                cols.append("avg")

                result_df = pd.DataFrame(results_list)[cols]
                result_df.columns = [c.title() for c in result_df.columns]

                # Latest period summary metrics
                latest = results_list[-1]
                avg = latest.get("avg", 0)

                st.subheader(f"CAGR Results — {ticker}")
                m1, m2, m3 = st.columns(3)
                m1.metric("Latest Avg CAGR", f"{avg:.1f}%")
                m2.metric("Period", f"{period}Y")
                m3.metric(
                    "EPS CAGR (latest)" if inc_eps else "Book CAGR (latest)",
                    f"{latest.get('eps' if inc_eps else 'book', 0):.1f}%",
                )

                st.markdown("**Rolling CAGR Table (%)**")
                st.dataframe(result_df, use_container_width=True, hide_index=True)

                log.info(
                    "[app][cagr_complete] ticker=%s periods=%d avg_latest=%.2f",
                    ticker,
                    len(results_list),
                    avg,
                )

            except Exception as e:
                log.error("[app][cagr_error] ticker=%s error=%s", ticker_input, e)
                st.error("An error occurred during CAGR analysis. Please try again.")


# ─── MOS Page ────────────────────────────────────────────────────────────────


def _page_mos():
    st.header("🛡️ Margin of Safety (MOS)")
    st.caption("Calculates intrinsic value and MOS-adjusted buy price.")

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("mos_ticker")
    with col2:
        multi_year = st.checkbox("Multiple Years?", value=False, key="mos_multi")

    if multi_year:
        col1, col2, col3 = st.columns(3)
        with col1:
            start_year = st.number_input(
                "Start Year",
                min_value=2000,
                max_value=2030,
                value=2020,
                step=1,
                key="mos_start",
            )
        with col2:
            end_year = st.number_input(
                "End Year",
                min_value=2000,
                max_value=2030,
                value=2024,
                step=1,
                key="mos_end",
            )
        with col3:
            growth_rate = (
                st.number_input(
                    "Growth Rate (%)",
                    min_value=0.0,
                    max_value=50.0,
                    value=15.0,
                    step=0.5,
                    key="mos_gr",
                )
                / 100
            )
        years = list(range(start_year, end_year + 1))
    else:
        col1, col2 = st.columns(2)
        with col1:
            single_year = st.number_input(
                "Year",
                min_value=2000,
                max_value=2030,
                value=2024,
                step=1,
                key="mos_single",
            )
        with col2:
            growth_rate = (
                st.number_input(
                    "Growth Rate (%)",
                    min_value=0.0,
                    max_value=50.0,
                    value=15.0,
                    step=0.5,
                    key="mos_gr",
                )
                / 100
            )
        years = [single_year]

    col_a, col_b = st.columns(2)
    discount_rate = (
        col_a.slider("Discount Rate (%)", 5, 20, 15, step=1, key="mos_dr") / 100
    )
    st.info("💡 Margin of Safety is fixed at 50%")
    MARGIN_OF_SAFETY = 0.50

    if st.button("Run MOS Analysis", type="primary", key="mos_run"):
        if multi_year and start_year >= end_year:
            st.error("Start year must be before end year.")
            return
        _check_session_limit()
        try:
            ticker = _validate_ticker(ticker_input)
        except ValueError as e:
            st.error(str(e))
            return

        log.info(
            "[app][mos_start] ticker=%s years=%s growth=%.2f pipeline_version=%s",
            ticker,
            years,
            growth_rate,
            PIPELINE_VERSION,
        )

        with st.spinner(f"Calculating MOS for {ticker}..."):
            try:
                results = []
                for year in years:
                    r = calculate_mos_value_from_ticker(
                        ticker=ticker,
                        year=year,
                        growth_rate=growth_rate,
                        discount_rate=discount_rate,
                        margin_of_safety=MARGIN_OF_SAFETY,
                    )
                    if r:
                        results.append(r)

                if not results:
                    st.error(f"No MOS data found for {ticker}.")
                    return

                st.session_state["analysis_count"] += 1
                st.success(f"MOS Analysis completed for {ticker}")

                latest = max(results, key=lambda r: r.get("Year", 0))

                if len(results) == 1:
                    # Single year: full metric display
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric(
                        "Fair Value", f"${latest.get('Fair Value Today', 0):,.2f}"
                    )
                    c2.metric("MOS Buy Price", f"${latest.get('MOS Price', 0):,.2f}")
                    c3.metric(
                        "Current Price", f"${latest.get('Current Stock Price', 0):,.2f}"
                    )
                    c4.metric("EPS", f"${latest.get('EPS_now', 0):.2f}")
                    _show_recommendation(latest.get("Investment Recommendation", "N/A"))
                else:
                    # Multi-year: show latest metrics + full table
                    c1, c2, c3 = st.columns(3)
                    c1.metric(
                        "Fair Value (latest)",
                        f"${latest.get('Fair Value Today', 0):,.2f}",
                    )
                    c2.metric(
                        "MOS Buy Price (latest)", f"${latest.get('MOS Price', 0):,.2f}"
                    )
                    c3.metric(
                        "Current Price", f"${latest.get('Current Stock Price', 0):,.2f}"
                    )
                    _show_recommendation(latest.get("Investment Recommendation", "N/A"))

                # Table for all cases
                table = []
                for r in results:
                    row = {
                        "Year": r.get("Year"),
                        "EPS": f"${r.get('EPS_now', 0):.2f}",
                        "Fair Value": f"${r.get('Fair Value Today', 0):,.2f}",
                        "MOS Price": f"${r.get('MOS Price', 0):,.2f}",
                    }
                    if r.get("Year") == latest.get("Year"):
                        row["Current Price"] = (
                            f"${r.get('Current Stock Price', 0):,.2f}"
                        )
                        row["Recommendation"] = r.get(
                            "Investment Recommendation", "N/A"
                        )
                    table.append(row)

                st.dataframe(
                    pd.DataFrame(table), use_container_width=True, hide_index=True
                )

            except Exception as e:
                log.error("[app][mos_error] ticker=%s error=%s", ticker_input, e)
                st.error("An error occurred. Please try again.")


# ─── Profitability Page ───────────────────────────────────────────────────────


def _page_profitability():
    st.header("💰 Profitability Metrics")
    st.caption("Analyses ROE, ROA, ROIC, and margin quality.")

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("prof_ticker")
    with col2:
        multi_year = st.checkbox("Multiple Years?", value=False, key="prof_multi")

    if multi_year:
        col1, col2 = st.columns(2)
        with col1:
            start_year = st.number_input(
                "Start Year",
                min_value=2000,
                max_value=2030,
                value=2020,
                step=1,
                key="prof_start",
            )
        with col2:
            end_year = st.number_input(
                "End Year",
                min_value=2000,
                max_value=2030,
                value=2024,
                step=1,
                key="prof_end",
            )
        years = list(range(start_year, end_year + 1))
    else:
        single_year = st.number_input(
            "Year",
            min_value=2000,
            max_value=2030,
            value=2024,
            step=1,
            key="prof_single",
        )
        years = [single_year]

    if st.button("Run Profitability Analysis", type="primary", key="prof_run"):
        if multi_year and start_year >= end_year:
            st.error("Start year must be before end year.")
            return
        _check_session_limit()
        try:
            ticker = _validate_ticker(ticker_input)
        except ValueError as e:
            st.error(str(e))
            return

        log.info(
            "[app][profitability_start] ticker=%s years=%s pipeline_version=%s",
            ticker,
            years,
            PIPELINE_VERSION,
        )

        def pct(v):
            return f"{v * 100:.1f}%" if v is not None else "N/A"

        with st.spinner(f"Fetching profitability data for {ticker}..."):
            try:
                if multi_year:
                    results = profitability.calculate_profitability_metrics_multi_year(
                        ticker, start_year, end_year
                    )
                else:
                    r = profitability.calculate_profitability_metrics_from_ticker(
                        ticker, years[0]
                    )
                    results = [r] if r and not r.get("error") else []

                if not results:
                    st.error(f"No profitability data found for {ticker}.")
                    return

                st.session_state["analysis_count"] += 1
                st.success(f"Profitability Analysis completed for {ticker}")

                latest = results[-1]

                # Latest year metrics
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("ROE", pct(latest.get("roe")))
                c2.metric("ROA", pct(latest.get("roa")))
                c3.metric("ROIC", pct(latest.get("roic")))
                c4.metric("Net Margin", pct(latest.get("net_margin")))

                # Multi-year table
                if len(results) > 1:
                    table_rows = [
                        {
                            "Year": r.get("year"),
                            "ROE": pct(r.get("roe")),
                            "ROA": pct(r.get("roa")),
                            "ROIC": pct(r.get("roic")),
                            "Gross Margin": pct(r.get("gross_margin")),
                            "Operating Margin": pct(r.get("operating_margin")),
                            "Net Margin": pct(r.get("net_margin")),
                        }
                        for r in results
                    ]
                    st.dataframe(
                        pd.DataFrame(table_rows),
                        use_container_width=True,
                        hide_index=True,
                    )

            except Exception as e:
                log.error(
                    "[app][profitability_error] ticker=%s error=%s", ticker_input, e
                )
                st.error("An error occurred. Please try again.")


# ─── PBT Page ────────────────────────────────────────────────────────────────


def _page_pbt():
    st.header("⏱️ Payback Time (PBT)")
    st.caption("Calculates the 8-year FCF payback price and 2× fair value.")

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("pbt_ticker")
    with col2:
        multi_year = st.checkbox("Multiple Years?", value=False, key="pbt_multi")

    if multi_year:
        col1, col2 = st.columns(2)
        with col1:
            start_year = st.number_input(
                "Start Year",
                min_value=2000,
                max_value=2030,
                value=2020,
                step=1,
                key="pbt_start",
            )
        with col2:
            end_year = st.number_input(
                "End Year",
                min_value=2000,
                max_value=2030,
                value=2024,
                step=1,
                key="pbt_end",
            )
        years = list(range(start_year, end_year + 1))
    else:
        single_year = st.number_input(
            "Year", min_value=2000, max_value=2030, value=2024, step=1, key="pbt_single"
        )
        years = [single_year]

    col_a, col_b = st.columns(2)
    growth_rate = (
        col_a.number_input(
            "Growth Rate (%)",
            min_value=0.0,
            max_value=50.0,
            value=15.0,
            step=0.5,
            key="pbt_gr",
        )
        / 100
    )
    show_table = col_b.checkbox(
        "Show cashflow table (single year only)", value=True, key="pbt_table"
    )

    if st.button("Run PBT Analysis", type="primary", key="pbt_run"):
        if multi_year and start_year >= end_year:
            st.error("Start year must be before end year.")
            return
        _check_session_limit()
        try:
            ticker = _validate_ticker(ticker_input)
        except ValueError as e:
            st.error(str(e))
            return

        log.info(
            "[app][pbt_start] ticker=%s years=%s growth=%.2f pipeline_version=%s",
            ticker,
            years,
            growth_rate,
            PIPELINE_VERSION,
        )

        with st.spinner(f"Calculating PBT for {ticker}..."):
            try:
                results = []
                for year in years:
                    try:
                        bp, fv, table, price_info = calculate_pbt_from_ticker(
                            ticker=ticker,
                            year=year,
                            growth_estimate=growth_rate,
                            return_full_table=(show_table and len(years) == 1),
                        )
                        results.append(
                            {
                                "year": year,
                                "buy_price": bp,
                                "fair_value": fv,
                                "table": table,
                                "price_info": price_info,
                            }
                        )
                    except Exception as ye:
                        log.warning("[app][pbt_year_skip] year=%d error=%s", year, ye)

                if not results:
                    st.error(f"No PBT data found for {ticker}.")
                    return

                st.session_state["analysis_count"] += 1
                st.success(f"PBT Analysis completed for {ticker}")

                latest = results[-1]

                if len(results) == 1:
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Buy Price (8Y)", f"${latest['buy_price']:,.2f}")
                    c2.metric("Fair Value (2×)", f"${latest['fair_value']:,.2f}")
                    c3.metric(
                        "Current Price",
                        f"${latest['price_info'].get('Current Stock Price', 0):,.2f}",
                    )
                    col_a, col_b = st.columns(2)
                    col_a.markdown(
                        f"**FCF/Share:** ${latest['price_info'].get('FCF per Share', 0):,.2f}"
                    )
                    col_a.markdown(
                        f"**vs Buy Price:** {latest['price_info'].get('% vs Buy Price', 'N/A')}"
                    )
                    col_b.markdown(
                        f"**vs Fair Value:** {latest['price_info'].get('% vs Fair Value', 'N/A')}"
                    )
                    _show_recommendation(
                        latest["price_info"].get("Investment Recommendation", "N/A")
                    )

                    if show_table and latest["table"]:
                        st.markdown("**8-Year FCF Cashflow Table**")
                        df_t = pd.DataFrame(latest["table"])
                        df_t.columns = ["Year", "FCF Income ($)", "Cumulative ($)"]
                        df_t["FCF Income ($)"] = df_t["FCF Income ($)"].map(
                            "${:,.2f}".format
                        )
                        df_t["Cumulative ($)"] = df_t["Cumulative ($)"].map(
                            "${:,.2f}".format
                        )
                        st.dataframe(df_t, use_container_width=True, hide_index=True)
                else:
                    # Multi-year summary table
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Buy Price (latest)", f"${latest['buy_price']:,.2f}")
                    c2.metric("Fair Value (latest)", f"${latest['fair_value']:,.2f}")
                    c3.metric(
                        "Current Price",
                        f"${latest['price_info'].get('Current Stock Price', 0):,.2f}",
                    )
                    _show_recommendation(
                        latest["price_info"].get("Investment Recommendation", "N/A")
                    )

                    table_rows = [
                        {
                            "Year": r["year"],
                            "Buy Price": f"${r['buy_price']:,.2f}",
                            "Fair Value": f"${r['fair_value']:,.2f}",
                            "FCF/Share": f"${r['price_info'].get('FCF per Share', 0):,.2f}",
                            "Recommendation": r["price_info"].get(
                                "Investment Recommendation", "N/A"
                            ),
                        }
                        for r in results
                    ]
                    st.dataframe(
                        pd.DataFrame(table_rows),
                        use_container_width=True,
                        hide_index=True,
                    )

            except Exception as e:
                log.error("[app][pbt_error] ticker=%s error=%s", ticker_input, e)
                st.error("An error occurred. Please try again.")


# ─── AI Moat Page ────────────────────────────────────────────────────────────


def _page_moat():
    st.header("🤖 AI Moat Analysis")
    st.caption(
        "RAG-based qualitative moat assessment using SEC filings and earnings transcripts."
    )

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("moat_ticker")
        year = _year_input("moat_year")
    with col2:
        load_sec = st.checkbox("Load SEC Filings", value=True, key="moat_sec")
        load_earnings = st.checkbox(
            "Load Earnings Transcripts", value=False, key="moat_earn"
        )

    st.markdown("**Moat Types to Assess**")
    c1, c2, c3, c4, c5 = st.columns(5)
    brand = c1.checkbox("Brand Power", value=True, key="moat_brand")
    switch = c2.checkbox("Switching Costs", value=True, key="moat_switch")
    network = c3.checkbox("Network Effects", value=True, key="moat_net")
    cost = c4.checkbox("Cost Advantages", value=True, key="moat_cost")
    scale = c5.checkbox("Efficient Scale", value=True, key="moat_scale")
    run_red_flags = st.checkbox("Run Red Flag Detection", value=True, key="moat_rf")

    if st.button("Run Moat Analysis", type="primary", key="moat_run"):
        _check_session_limit()
        try:
            ticker = _validate_ticker(ticker_input)
        except ValueError as e:
            st.error(str(e))
            return

        config = AnalysisConfig(
            run_cagr=False,
            run_mos=False,
            run_profitability=False,
            run_moat_analysis=True,
            run_brand_power=brand,
            run_switching_costs=switch,
            run_network_effects=network,
            run_cost_advantages=cost,
            run_efficient_scale=scale,
            run_red_flags=run_red_flags,
        )

        log.info(
            "[app][moat_start] ticker=%s year=%d load_sec=%s load_earnings=%s pipeline_version=%s",
            ticker,
            year,
            load_sec,
            load_earnings,
            PIPELINE_VERSION,
        )

        with st.spinner(
            f"Running AI Moat Analysis for {ticker} — this may take a moment..."
        ):
            try:
                analyzer = ValueKitAnalyzer()
                result = analyzer.analyze_stock_complete(
                    ticker=ticker,
                    year=year,
                    auto_estimate_growth=False,
                    discount_rate=0.15,
                    margin_of_safety=0.50,
                    load_sec_data=load_sec,
                    load_earnings_data=load_earnings,
                    config=config,
                )

                ai = result.get("ai_decision")
                if not ai:
                    st.warning("Moat analysis returned no result.")
                    return

                st.session_state["analysis_count"] += 1

                st.subheader(f"Moat Results — {ticker} ({year})")
                c1, c2, c3, c4 = st.columns(4)
                c1.metric("Moat Decision", ai.get("decision", "N/A"))
                c2.metric("Confidence", ai.get("confidence", "N/A"))
                c3.metric("Overall Score", f"{ai.get('overall_score', 0)}/100")
                c4.metric("Moat Strength", ai.get("moat_strength", "N/A"))

                st.markdown("---")
                st.markdown(f"**Reasoning:** {ai.get('reasoning', 'N/A')}")

                if ai.get("red_flags"):
                    st.warning(
                        "**Red Flags Identified:**\n"
                        + "\n".join(f"- {rf}" for rf in ai["red_flags"])
                    )

                log.info(
                    "[app][moat_complete] ticker=%s decision=%s score=%s",
                    ticker,
                    ai.get("decision"),
                    ai.get("overall_score"),
                )

            except Exception as e:
                log.error("[app][moat_error] ticker=%s error=%s", ticker_input, e)
                st.error("An error occurred during moat analysis. Please try again.")


# ─── Overview Page ────────────────────────────────────────────────────────────


def _page_overview():
    st.header("📊 Overview — Full Pipeline")
    st.caption(
        "Runs all components: CAGR → MOS → TenCap → PBT → Profitability → AI Moat."
    )

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("ov_ticker")
        year = _year_input("ov_year")
    with col2:
        discount_rate = (
            st.slider("Discount Rate (%)", 5, 20, 15, step=1, key="ov_dr") / 100
        )
        mos_pct = (
            st.slider("Margin of Safety (%)", 10, 60, 50, step=5, key="ov_mos") / 100
        )

    col3, col4 = st.columns(2)
    load_sec = col3.checkbox("Load SEC Filings", value=False, key="ov_sec")
    load_earnings = col4.checkbox(
        "Load Earnings Transcripts", value=False, key="ov_earn"
    )

    st.markdown("---")
    st.markdown("**Score Weights**")
    st.caption("Quant sub-weights — must sum to 100")
    wc1, wc2, wc3 = st.columns(3)
    w_mos = wc1.slider("MOS %", 0, 100, 34, 1, key="ov_w_mos")
    w_tencap = wc2.slider("TenCap %", 0, 100, 33, 1, key="ov_w_tencap")
    w_pbt = wc3.slider("PBT %", 0, 100, 33, 1, key="ov_w_pbt")
    if (w_mos + w_tencap + w_pbt) != 100:
        st.warning(f"Quant weights sum to {w_mos + w_tencap + w_pbt} — must be 100")

    quant_vs_moat = st.slider(
        "Quant vs Moat (%)",
        0,
        100,
        60,
        10,
        key="ov_qvm",
        help="60 = 60% Quant Score / 40% Moat Score",
    )

    if st.button("Run Full Analysis", type="primary", key="ov_run"):
        _check_session_limit()
        try:
            ticker = _validate_ticker(ticker_input)
        except ValueError as e:
            st.error(str(e))
            return

        log.info(
            "[app][overview_start] ticker=%s year=%d discount=%.2f mos=%.2f pipeline_version=%s",
            ticker,
            year,
            discount_rate,
            mos_pct,
            PIPELINE_VERSION,
        )

        config = AnalysisConfig(
            run_cagr=True,
            run_mos=True,
            run_profitability=True,
            run_moat_analysis=True,
        )

        with st.spinner(f"Running full analysis for {ticker}..."):
            try:
                analyzer = ValueKitAnalyzer()
                result = analyzer.analyze_stock_complete(
                    ticker=ticker,
                    year=year,
                    auto_estimate_growth=True,
                    discount_rate=discount_rate,
                    margin_of_safety=mos_pct,
                    load_sec_data=load_sec,
                    load_earnings_data=load_earnings,
                    config=config,
                )

                st.session_state["analysis_count"] += 1

                # Combined Score
                score_data = _compute_combined_score(
                    result, w_mos, w_tencap, w_pbt, quant_vs_moat, mos_pct
                )
                if score_data["combined"] is not None:
                    st.subheader("🎯 Combined Investment Score")
                    cs1, cs2, cs3 = st.columns(3)
                    cs1.metric("Combined Score", f"{score_data['combined']:.0f} / 100")
                    cs2.metric(
                        "Quant Score",
                        f"{score_data['quant_score']:.0f} / 100"
                        if score_data["quant_score"] is not None
                        else "N/A",
                    )
                    cs3.metric(
                        "Moat Score",
                        f"{score_data['moat_score']} / 100"
                        if score_data["moat_score"] is not None
                        else "N/A",
                    )
                    rec_fn = {
                        "STRONG BUY": st.success,
                        "BUY": st.success,
                        "HOLD": st.warning,
                        "PASS": st.error,
                    }.get(score_data["recommendation"], st.info)
                    rec_fn(
                        f"**Score Recommendation: {score_data['recommendation']}** "
                        f"({quant_vs_moat}% Quant / {100 - quant_vs_moat}% Moat)"
                    )
                    st.divider()

                rec = result.get("final_recommendation", "N/A")
                _show_recommendation(f"Pipeline Recommendation: {rec}")

                # MOS
                if result.get("mos_result"):
                    r = result["mos_result"]
                    st.subheader("🛡️ Margin of Safety")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Fair Value", f"${r.get('Fair Value Today', 0):.2f}")
                    c2.metric("MOS Price", f"${r.get('MOS Price', 0):.2f}")
                    c3.metric(
                        "Current Price", f"${r.get('Current Stock Price', 0):.2f}"
                    )
                    c4.metric("EPS", f"${r.get('EPS_now', 0):.2f}")
                    st.info(
                        f"Price vs Fair Value: {r.get('Price vs Fair Value', 'N/A')} | "
                        f"Growth Rate: {r.get('Growth Rate', 0):.1f}% | "
                        f"MOS: {r.get('Margin of Safety', 'N/A')}"
                    )
                    st.divider()

                # TenCap
                if result.get("tencap_result"):
                    r = result["tencap_result"]
                    st.subheader("🔟 TenCap")
                    c1, c2, c3 = st.columns(3)
                    tc_mos_price = r.get("ten_cap_fair_value", 0) * (1 - mos_pct)
                    c1.metric(
                        f"TenCap Buy Price ({mos_pct * 100:.0f}% MOS)",
                        f"${tc_mos_price:.2f}",
                    )
                    c2.metric(
                        "TenCap Fair Value", f"${r.get('ten_cap_fair_value', 0):.2f}"
                    )
                    c3.metric(
                        "Current Price", f"${r.get('current_stock_price', 0):.2f}"
                    )
                    st.divider()

                # PBT
                if result.get("pbt_result"):
                    r = result["pbt_result"]
                    st.subheader("⏱️ Payback Time")
                    c1, c2, c3 = st.columns(3)
                    pbt_mos_price = r.get("fair_value", 0) * (1 - mos_pct)
                    c1.metric(
                        f"Buy Price ({mos_pct * 100:.0f}% MOS)", f"${pbt_mos_price:.2f}"
                    )
                    c2.metric("Fair Value (2×)", f"${r.get('fair_value', 0):.2f}")
                    c3.metric(
                        "Current Price", f"${r.get('current_stock_price', 0):.2f}"
                    )
                    st.divider()

                # Profitability
                if result.get("profitability_result"):
                    r = result["profitability_result"]
                    if not r.get("error"):

                        def pct(v):
                            return f"{v * 100:.1f}%" if v is not None else "N/A"

                        st.subheader("💰 Profitability")
                        c1, c2, c3, c4 = st.columns(4)
                        c1.metric("ROE", pct(r.get("roe")))
                        c2.metric("ROA", pct(r.get("roa")))
                        c3.metric("ROIC", pct(r.get("roic")))
                        c4.metric("Net Margin", pct(r.get("net_margin")))
                        st.divider()

                # Moat
                if result.get("ai_decision"):
                    ai = result["ai_decision"]
                    st.subheader("🤖 AI Moat")
                    c1, c2, c3, c4 = st.columns(4)
                    c1.metric("Decision", ai.get("decision", "N/A"))
                    c2.metric("Confidence", ai.get("confidence", "N/A"))
                    c3.metric("Overall Score", f"{ai.get('overall_score', 0)}/100")
                    c4.metric("Moat Strength", ai.get("moat_strength", "N/A"))
                    st.markdown(f"**Reasoning:** {ai.get('reasoning', 'N/A')}")
                    if ai.get("red_flags"):
                        st.warning("**Red Flags:** " + " · ".join(ai["red_flags"]))

                log.info(
                    "[app][overview_complete] ticker=%s recommendation=%s",
                    ticker,
                    rec,
                )

            except Exception as e:
                log.error("[app][overview_error] ticker=%s error=%s", ticker_input, e)
                st.error("An error occurred during analysis. Please try again.")


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    _init_session_state()

    selected = _render_sidebar()

    PAGE_FN = {
        "📊 Overview": _page_overview,
        "📈 CAGR Growth Estimate": _page_cagr,
        "🛡️ Margin of Safety (MOS)": _page_mos,
        "💰 Profitability Metrics": _page_profitability,
        "⏱️ Payback Time (PBT)": _page_pbt,
        "🤖 AI Moat Analysis": _page_moat,
    }

    PAGE_FN[selected]()


if __name__ == "__main__":
    main()
