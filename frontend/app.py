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

from backend.valuekit_ai.config.config import (
    PIPELINE_VERSION,
    RAGConfig,
    DEFAULT_DISCOUNT_RATE,
    DEFAULT_MARGIN_OF_SAFETY,
    DEFAULT_BASE_YEAR,
)
from backend.valuekit_ai.config.analysis_config import AnalysisConfig
from backend.valuekit_ai.core.valuekit_integration import ValueKitAnalyzer
from backend.logic.mos import calculate_mos_value_from_ticker
from backend.logic import profitability
from backend.logic.tencap import _get_ten_cap_result, calculate_ten_cap_with_comparison
from backend.logic.pbt import calculate_pbt_from_ticker, calculate_pbt_with_comparison
from backend.logic.cagr import (
    _mos_growth_estimate_auto,
    run_analysis as cagr_run_analysis,
)
from backend.api import fmp_api
import streamlit_authenticator as stauth
import bcrypt as _bcrypt_module
import hashlib as _hashlib_module
import hmac as _hmac_module


from backend.logic.fundamentals import check_fundamentals

from backend.logic.growth_consensus import get_growth_consensus

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
    if "ov_result" not in st.session_state:
        st.session_state["ov_result"] = None
    if "pipeline_top_k" not in st.session_state:
        st.session_state["pipeline_top_k"] = RAGConfig.TOP_K_RESULTS
    if "pipeline_temperature" not in st.session_state:
        st.session_state["pipeline_temperature"] = RAGConfig.LLM_TEMPERATURE


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


# ─── Index Manager ────────────────────────────────────────────────────────────

_INDEX_TYPES = {
    "10-K": "SEC Filings (10-K)",
    "earnings_call": "Earnings Transcripts",
    "news_article": "Yahoo Finance News",
    "company_info": "Yahoo Company Info",
}

# Maps ChromaDB document_type → the substring that identifies matching CacheManager keys.
# news_article has no CacheManager entry, so it maps to None.
_DTYPE_CACHE_FRAGMENT = {
    "10-K":          "_10K_",
    "earnings_call": "_earnings_",
    "company_info":  "_yahoo_info",
    "news_article":  None,
}


def _invalidate_ticker_cache(tickers: set, dtype: str, cache) -> None:
    """
    Remove CacheManager entries for tickers whose ChromaDB chunks were just deleted.

    Only touches keys that belong to the affected tickers and correspond to the
    deleted document_type — unrelated tickers and unrelated cache namespaces are
    left untouched.
    """
    fragment = _DTYPE_CACHE_FRAGMENT.get(dtype)
    if fragment is None:
        log.debug(
            "[index_manager][cache_skip] document_type=%s has no CacheManager entries",
            dtype,
        )
        return

    for ticker in tickers:
        prefix = f"{ticker}{fragment}"
        matching = [k for k in list(cache.metadata.keys()) if k.startswith(prefix)]
        if not matching:
            log.debug(
                "[index_manager][cache_no_entry] ticker=%s document_type=%s",
                ticker,
                dtype,
            )
            continue
        for k in matching:
            dt = cache.metadata[k].get("data_type", "misc")
            cache.clear(key=k, data_type=dt)
            log.info(
                "[index_manager][cache_invalidated] ticker=%s reason=chunks_deleted "
                "document_type=%s key=%s",
                ticker,
                dtype,
                k,
            )


def _render_pipeline_config():
    """Read-only sidebar expander showing all pipeline configuration constants."""
    with st.sidebar.expander("⚙️ Pipeline Configuration", expanded=False):
        # ── RAG / Retrieval ───────────────────────────────────────────────────
        st.markdown("**RAG / Retrieval**")
        _df_rag = pd.DataFrame(
            {
                "Parameter": ["Embedding model", "Chunk size", "Chunk overlap"],
                "Value": [
                    RAGConfig.EMBEDDING_MODEL,
                    RAGConfig.CHUNK_SIZE,
                    RAGConfig.CHUNK_OVERLAP,
                ],
            }
        )
        _df_rag["Value"] = _df_rag["Value"].astype(str)
        st.table(_df_rag)
        top_k_val = st.number_input(
            "Top-k chunks",
            min_value=1,
            max_value=20,
            step=1,
            value=st.session_state["pipeline_top_k"],
            key="pipeline_top_k_input",
        )
        if top_k_val != st.session_state["pipeline_top_k"]:
            log.info(
                "[pipeline_config][override] param=top_k value=%d default=%d",
                top_k_val,
                RAGConfig.TOP_K_RESULTS,
            )
            st.session_state["pipeline_top_k"] = top_k_val

        # ── LLM ──────────────────────────────────────────────────────────────
        st.markdown("**LLM**")
        _df_llm = pd.DataFrame(
            {
                "Parameter": ["Model", "Max tokens"],
                "Value": [RAGConfig.LLM_MODEL, RAGConfig.LLM_MAX_TOKENS],
            }
        )
        _df_llm["Value"] = _df_llm["Value"].astype(str)
        st.table(_df_llm)
        temperature_val = st.slider(
            "Temperature",
            min_value=0.0,
            max_value=1.0,
            step=0.1,
            value=float(st.session_state["pipeline_temperature"]),
            key="pipeline_temperature_input",
        )
        if temperature_val != st.session_state["pipeline_temperature"]:
            log.info(
                "[pipeline_config][override] param=temperature value=%.1f default=%.1f",
                temperature_val,
                RAGConfig.LLM_TEMPERATURE,
            )
            st.session_state["pipeline_temperature"] = temperature_val

        # ── Pipeline ─────────────────────────────────────────────────────────
        st.markdown("**Pipeline**")
        _df_pipeline = pd.DataFrame(
            {
                "Parameter": ["Pipeline version", "Cache enabled"],
                "Value": [PIPELINE_VERSION, "Yes"],
            }
        )
        _df_pipeline["Value"] = _df_pipeline["Value"].astype(str)
        st.table(_df_pipeline)

        # ── Valuation Defaults ────────────────────────────────────────────────
        st.markdown("**Valuation Defaults**")
        _df_val = pd.DataFrame(
            {
                "Parameter": [
                    "Default discount rate",
                    "Default MOS",
                    "Default base year",
                ],
                "Value": [
                    f"{int(DEFAULT_DISCOUNT_RATE * 100)}%",
                    f"{int(DEFAULT_MARGIN_OF_SAFETY * 100)}%",
                    DEFAULT_BASE_YEAR,
                ],
            }
        )
        _df_val["Value"] = _df_val["Value"].astype(str)
        st.table(_df_val)


def _render_index_manager():
    """Sidebar expander to inspect and selectively delete ChromaDB index entries."""
    with st.sidebar.expander("🗄️ Manage Index", expanded=False):
        # ── Count docs per type ───────────────────────────────────────────────
        try:
            from backend.valuekit_ai.rag.vector_store import get_vector_store
            vs = get_vector_store()
            raw_col = vs.vectorstore._collection
            counts = {}
            for dtype in _INDEX_TYPES:
                result = raw_col.get(where={"document_type": dtype}, include=[])
                counts[dtype] = len(result["ids"])
            total = sum(counts.values())
        except Exception as e:
            st.error(f"Could not read index: {e}")
            return

        col_cnt, col_btn = st.columns([3, 1])
        col_cnt.caption(f"Total chunks in index: **{total}**")
        if col_btn.button("↻", key="idx_refresh", help="Refresh counts"):
            st.rerun()

        for dtype, label in _INDEX_TYPES.items():
            st.caption(f"- {label}: {counts[dtype]}")

        st.markdown("---")
        st.markdown("**Select types to delete:**")

        selected_types = []
        for dtype, label in _INDEX_TYPES.items():
            if counts[dtype] > 0:
                checked = st.checkbox(
                    f"{label} ({counts[dtype]})",
                    value=False,
                    key=f"idx_del_{dtype}",
                )
                if checked:
                    selected_types.append(dtype)
            else:
                st.caption(f"{label}: empty")

        if not selected_types:
            return

        # ── Step 1: first button ──────────────────────────────────────────────
        if st.button("Delete Selected", key="idx_del_step1", type="secondary"):
            st.session_state["idx_pending_delete"] = selected_types

        pending = st.session_state.get("idx_pending_delete", [])
        if not pending:
            return

        # ── Step 2: confirm ───────────────────────────────────────────────────
        labels = [_INDEX_TYPES[t] for t in pending]
        st.warning(
            f"Delete **{sum(counts[t] for t in pending)} chunks** "
            f"from: {', '.join(labels)}?\n\nThis cannot be undone."
        )
        if st.button("Confirm Delete", key="idx_del_step2", type="primary"):
            try:
                from backend.cache import get_cache_manager
                cache = get_cache_manager()

                for dtype in pending:
                    n = counts[dtype]

                    # Collect affected tickers before deletion so we can target
                    # only their cache entries — do not touch unrelated tickers.
                    meta_result = raw_col.get(
                        where={"document_type": dtype}, include=["metadatas"]
                    )
                    affected_tickers = {
                        m.get("ticker")
                        for m in meta_result["metadatas"]
                        if m.get("ticker")
                    }

                    raw_col.delete(where={"document_type": dtype})
                    log.info(
                        "[index_manager][delete] document_type=%s count=%d tickers=%s",
                        dtype,
                        n,
                        sorted(affected_tickers),
                    )

                    _invalidate_ticker_cache(affected_tickers, dtype, cache)

                st.success("Deleted. Refresh the page to see updated counts.")
            except Exception as e:
                log.error("[index_manager][delete_error] error=%s", e)
                st.error(f"Deletion failed: {e}")
            finally:
                st.session_state.pop("idx_pending_delete", None)


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
            "🔟 TenCap Valuation",
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

    st.sidebar.markdown("---")
    _render_index_manager()
    _render_pipeline_config()

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

    col1, col2, col3 = st.columns(3)
    with col1:
        ticker_input = _ticker_input("mos_ticker")
    with col2:
        multi_year = st.checkbox("Multiple Years?", value=False, key="mos_multi")
    with col3:
        auto_estimate = st.checkbox(
            "Auto-estimate growth rate",
            value=False,
            key="mos_auto",
            help="Blends 5-year historical CAGR (60%) with FMP analyst estimates (40%), capped at 25%",
            disabled=multi_year,
        )

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
            if not auto_estimate:
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
            else:
                growth_rate = None  # resolved after ticker validation
                st.info("Growth rate will be estimated after analysis starts.")
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

        # Auto-estimate growth rate via 3-source consensus (single year only)
        if auto_estimate and not multi_year:
            try:
                from backend.logic.growth_consensus import get_growth_consensus

                with st.spinner("Estimating growth rate..."):
                    consensus = get_growth_consensus(ticker, years[0])
                growth_rate = consensus["rate"]
                _METHOD_LABELS = {
                    "consensus":      "Historical CAGR (60%) + Analyst estimate (40%)",
                    "own_cagr_only":  "Historical CAGR only (no analyst data available)",
                    "analyst_only":   "Analyst estimate only (no CAGR data available)",
                    "fallback":       "Standard fallback 10% (no data available)",
                }
                label = _METHOD_LABELS.get(consensus["method"], consensus["method"])
                cap_note = "  \n⚠️ Capped at 25%." if consensus["capped"] else ""
                own = consensus["sources"]["own_cagr"]
                ana = consensus["sources"]["analyst_estimate"]
                st.info(
                    f"**Auto Growth Rate: {growth_rate * 100:.1f}%**{cap_note}  \n"
                    f"Method: {label}  \n"
                    f"Own CAGR: {own * 100:.1f}%  |  "
                    f"Analyst estimate: {ana * 100:.1f}%"
                    if own is not None and ana is not None
                    else f"**Auto Growth Rate: {growth_rate * 100:.1f}%**{cap_note}  \n"
                    f"Method: {label}"
                )
                log.info(
                    "[app][mos_consensus] ticker=%s rate=%.4f method=%s",
                    ticker, growth_rate, consensus["method"],
                )
            except Exception as ce:
                log.warning("[app][mos_consensus_error] ticker=%s error=%s", ticker, ce)
                growth_rate = 0.10
                st.warning("Could not estimate growth rate — using 10% fallback.")

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


def _page_tencap():
    st.header("🔟 TenCap Valuation")
    st.caption(
        "Owner Earnings ÷ 10% cap rate. "
        "Fair Value = 2× Buy Price. Buy Price already includes a 50% margin of safety."
    )

    col1, col2 = st.columns(2)
    with col1:
        ticker_input = _ticker_input("tencap_ticker")
    with col2:
        multi_year = st.checkbox("Multiple Years?", value=False, key="tencap_multi")

    if multi_year:
        col1, col2 = st.columns(2)
        with col1:
            start_year = st.number_input(
                "Start Year",
                min_value=2000,
                max_value=2030,
                value=2020,
                step=1,
                key="tencap_start",
            )
        with col2:
            end_year = st.number_input(
                "End Year",
                min_value=2000,
                max_value=2030,
                value=2024,
                step=1,
                key="tencap_end",
            )
        years = list(range(start_year, end_year + 1))
    else:
        single_year = st.number_input(
            "Year", min_value=2000, max_value=2030, value=2024, step=1, key="tencap_single"
        )
        years = [single_year]

    show_details = st.checkbox(
        "Show calculation details", value=False, key="tencap_details"
    )

    if st.button("Run TenCap Analysis", type="primary", key="tencap_run"):
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
            "[app][tencap_start] ticker=%s years=%s pipeline_version=%s",
            ticker, years, PIPELINE_VERSION,
        )

        with st.spinner(f"Calculating TenCap for {ticker}..."):
            try:
                results = []
                for year in years:
                    try:
                        data = calculate_ten_cap_with_comparison(ticker, year)
                        if data:
                            results.append(data)
                        else:
                            log.warning("[app][tencap_year_skip] ticker=%s year=%d no_data", ticker, year)
                    except Exception as ye:
                        log.warning("[app][tencap_year_skip] year=%d error=%s", year, ye)

                if not results:
                    st.error(f"No TenCap data found for {ticker}.")
                    return

                st.session_state["analysis_count"] += 1
                latest = results[-1]

                log.info(
                    "[app][tencap_complete] ticker=%s fair_value=%s buy_price=%s pipeline_version=%s",
                    ticker,
                    latest.get("ten_cap_fair_value"),
                    latest.get("ten_cap_buy_price"),
                    PIPELINE_VERSION,
                )

                st.success(f"TenCap Analysis completed for {ticker}")

                # ── Key metrics ───────────────────────────────────────────────
                c1, c2, c3 = st.columns(3)
                fair_value   = latest.get("ten_cap_fair_value")
                buy_price    = latest.get("ten_cap_buy_price")
                current_price = latest.get("current_stock_price")
                c1.metric("Fair Value",    f"${fair_value:,.2f}"    if fair_value    else "N/A")
                c2.metric("Buy Price",     f"${buy_price:,.2f}"     if buy_price     else "N/A")
                c3.metric("Current Price", f"${current_price:,.2f}" if current_price else "N/A")

                # ── Valuation verdict ─────────────────────────────────────────
                comparison = latest.get("price_vs_fair_value_tencap", "N/A")
                if "Undervalued" in str(comparison):
                    st.success(f"📈 {comparison}")
                elif "Overvalued" in str(comparison):
                    st.warning(f"📉 {comparison}")
                else:
                    st.info(f"⚖️ {comparison}")

                recommendation = latest.get("investment_recommendation", "N/A")
                _show_recommendation(recommendation)

                if latest.get("year_fallback"):
                    st.info(
                        f"ℹ️ Data Fallback: requested {latest.get('requested_year')} "
                        f"→ used {latest.get('year')}"
                    )

                # ── Calculation detail breakdown ──────────────────────────────
                if show_details:
                    with st.expander("📐 Calculation Details", expanded=True):
                        d1, d2 = st.columns(2)
                        d1.metric(
                            "Profit Before Tax",
                            f"${latest.get('profit_before_tax', 0):,.2f}M",
                        )
                        d1.metric(
                            "Depreciation & Amortisation",
                            f"${latest.get('depreciation', 0):,.2f}M",
                        )
                        d1.metric(
                            "Δ Working Capital",
                            f"${latest.get('working_capital_change', 0):,.2f}M",
                        )
                        d1.metric(
                            "50% Maintenance CapEx",
                            f"${latest.get('maintenance_capex', 0) * 0.5:,.2f}M",
                        )
                        d2.metric(
                            "Owner Earnings",
                            f"${latest.get('owner_earnings', 0):,.2f}M",
                        )
                        d2.metric(
                            "Shares Outstanding",
                            f"{latest.get('shares_outstanding', 0):,.2f}M",
                        )
                        d2.metric(
                            "Earnings per Share (Owner)",
                            f"${latest.get('earnings_per_share', 0):,.2f}",
                        )
                        st.caption(
                            "Formula: Owner Earnings = PBT + D&A + ΔWC − 50% CapEx  |  "
                            "Buy Price = EPS ÷ 10%  |  Fair Value = Buy Price × 2"
                        )

                # ── Multi-year summary table ──────────────────────────────────
                if len(results) > 1:
                    st.markdown("**Year-by-Year Summary**")
                    table_rows = []
                    for r in results:
                        fv = r.get("ten_cap_fair_value")
                        bp = r.get("ten_cap_buy_price")
                        row = {
                            "Year":       r.get("year", "N/A"),
                            "Fair Value": f"${fv:,.2f}" if fv else "N/A",
                            "Buy Price":  f"${bp:,.2f}" if bp else "N/A",
                            "Owner EPS":  f"${r.get('earnings_per_share', 0):,.2f}",
                        }
                        table_rows.append(row)
                    # Append current price & comparison to the latest year row
                    if current_price and fair_value:
                        table_rows[-1]["Current Price"] = f"${current_price:,.2f}"
                        table_rows[-1]["vs Fair Value"] = comparison
                    st.dataframe(
                        pd.DataFrame(table_rows),
                        use_container_width=True,
                        hide_index=True,
                    )

            except Exception as e:
                log.error("[app][tencap_error] ticker=%s error=%s", ticker_input, e)
                st.error("An error occurred. Please try again.")


# ─── Shared rendering helpers ─────────────────────────────────────────────────


def _get_data_quality_tier(avg_relevance: float, sources: int) -> dict:
    """
    Map RAG stats to a data quality tier for user-facing display.

    Thresholds:
      High   — avg_relevance ≥ 0.65 AND sources ≥ 5
      Medium — avg_relevance ≥ 0.45 OR  sources ≥ 3
      Low    — anything below Medium
    """
    if avg_relevance >= 0.65 and sources >= 5:
        return {"level": "high",   "label": "High data quality",   "color": "success"}
    elif avg_relevance >= 0.45 or sources >= 3:
        return {"level": "medium", "label": "Medium data quality",  "color": "warning"}
    else:
        return {"level": "low",    "label": "Low data quality",     "color": "error"}


def _render_moat_results(ticker: str, year: int, ai: dict, bm_result=None):
    """
    Render the full moat analysis UI block.
    Called at top level from _page_moat() and inside an expander from _page_overview().

    Args:
        ticker:    Stock ticker — passed to check_fundamentals()
        year:      Analysis year
        ai:        ai_decision dict (InvestmentDecision.to_dict())
        bm_result: Optional result from analyze_business_model()
    """
    # ── RAG data quality indicator ────────────────────────────────────────────
    avg_rel   = ai.get("avg_relevance_score", 0.0)
    total_src = ai.get("total_sources_used", 0)
    tier = _get_data_quality_tier(avg_rel, total_src)
    _QUALITY_MSG = {
        "high": (
            f"**{tier['label']}** — {total_src} relevant document sections "
            f"analysed (avg. relevance {avg_rel:.2f})."
        ),
        "medium": (
            f"**{tier['label']}** — {total_src} document section(s) analysed "
            f"(avg. relevance {avg_rel:.2f}). Result may be incomplete."
        ),
        "low": (
            f"**{tier['label']}** — Only {total_src} document section(s) found "
            f"(avg. relevance {avg_rel:.2f}). "
            "Enable 'Load SEC Filings' or load more data for a better result."
        ),
    }
    msg = _QUALITY_MSG[tier["level"]]
    if tier["level"] == "high":
        st.success(msg)
    elif tier["level"] == "medium":
        st.warning(msg)
    else:
        st.error(msg)

    # ── Business Model ────────────────────────────────────────────────────────
    if bm_result and bm_result.get("status") == "success":
        with st.expander("📋 Business Model", expanded=False):
            st.markdown(bm_result["description"])
            if bm_result.get("sources_used", 0) > 0:
                st.caption(
                    f"Sources used: {bm_result['sources_used']} document sections"
                )

    # ── Summary metrics ───────────────────────────────────────────────────────
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Decision",     ai.get("decision", "N/A"))
    c2.metric("Confidence",   ai.get("confidence", "N/A"))
    c3.metric("Overall Score", f"{ai.get('overall_score', 0)}/100")
    c4.metric("Moat Strength", ai.get("moat_strength", "N/A"))

    st.markdown("---")
    st.markdown(f"**Reasoning:** {ai.get('reasoning', 'N/A')}")

    # ── Per-type moat scores with progress bars ───────────────────────────────
    moat_details = ai.get("moat_details") or {}
    if moat_details:
        with st.expander("🔍 Moat Breakdown", expanded=False):
            _CONF_COLOR = {"High": "🟢", "Medium": "🟡", "Low": "🔴"}
            for _key, md in moat_details.items():
                score     = md.get("score", 0)
                bar       = "█" * score + "░" * (10 - score)
                conf      = md.get("confidence", "Low")
                conf_icon = _CONF_COLOR.get(conf, "⚪")
                srcs      = md.get("sources_used", 0)
                st.markdown(
                    f"**{md['name']}** — {score}/10 &nbsp; "
                    f"`{bar}` &nbsp; {conf_icon} {conf} confidence"
                    + (f"  ·  {srcs} sources" if srcs else "")
                )
                for ev in (md.get("evidence") or []):
                    st.caption(f"› {ev}")



def _render_fundamentals(ticker: str, year: int):
    """Render Quantitative Fundamentals section — standalone, outside any expander."""
    st.markdown("---")
    st.subheader("📊 Quantitative Fundamentals")
    st.caption("Financial health check — runs independently of RAG documents.")
    try:
        fund_results = check_fundamentals(ticker, year, base_year=year)
        _STATUS_ICONS = {"OK": "✅", "Warning": "⚠️", "Flag": "🚩", "N/A": "—"}
        cols = st.columns(len(fund_results))
        for col, check in zip(cols, fund_results):
            icon = _STATUS_ICONS.get(check["status"], "—")
            col.metric(
                label=f"{icon} {check['metric']}",
                value=check["value"],
                help=check["note"],
            )
        _flags = [c for c in fund_results if c["status"] == "Flag"]
        _warns = [c for c in fund_results if c["status"] == "Warning"]
        if _flags:
            st.error("🚩 **Flag(s):** " + ", ".join(f["metric"] for f in _flags))
        if _warns:
            st.warning("⚠️ **Warning(s):** " + ", ".join(w["metric"] for w in _warns))
        if not _flags and not _warns:
            st.success("✅ All fundamental checks passed")
    except Exception as _fe:
        log.warning("[app][fundamentals_error] ticker=%s error=%s", ticker, _fe)
        st.info("Could not load quantitative fundamentals.")


def _render_quant_pipeline(result: dict, mos_pct: float = 0.50):
    """
    Render Robustness Score, Valuation Score, MOS, TenCap, PBT, Profitability.
    Called from _page_overview() and the 'Full Pipeline Overview' expander on
    _page_moat().  No RAG calls — only FMP quantitative data (all cached).

    Args:
        result:  dict from ValueKitAnalyzer.analyze_stock_complete()
        mos_pct: margin of safety % for buy-price display (default 50%)
    """
    def _pct(v):
        return f"{v * 100:.1f}%" if v is not None else "N/A"

    # Quality + Valuation scores (from ai_decision, populated even without moat)
    ai = result.get("ai_decision") or {}
    if ai:
        sc1, sc2 = st.columns(2)
        sc1.metric(
            "Robustness Score (40%)",
            f"{ai.get('robustness_score', 0)} / 100",
            help="ROIC · FCF Yield · Net Margin · CAGR",
        )
        sc2.metric(
            "Valuation Score (20%)",
            f"{ai.get('valuation_score', 0)} / 100",
            help="MOS · TenCap · PBT — price vs fair value",
        )
        st.divider()

    # MOS
    if result.get("mos_result"):
        r = result["mos_result"]
        st.subheader("🛡️ Margin of Safety")
        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Fair Value",    f"${r.get('Fair Value Today', 0):.2f}")
        c2.metric("MOS Price",     f"${r.get('MOS Price', 0):.2f}")
        c3.metric("Current Price", f"${r.get('Current Stock Price', 0):.2f}")
        c4.metric("EPS",           f"${r.get('EPS_now', 0):.2f}")
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
            f"TenCap Buy Price ({mos_pct * 100:.0f}% MOS)", f"${tc_mos_price:.2f}"
        )
        c2.metric("TenCap Fair Value", f"${r.get('ten_cap_fair_value', 0):.2f}")
        c3.metric("Current Price",     f"${r.get('current_stock_price', 0):.2f}")
        st.divider()

    # PBT
    if result.get("pbt_result"):
        r = result["pbt_result"]
        st.subheader("⏱️ Payback Time")
        c1, c2, c3 = st.columns(3)
        pbt_mos_price = r.get("fair_value", 0) * (1 - mos_pct)
        c1.metric(f"Buy Price ({mos_pct * 100:.0f}% MOS)", f"${pbt_mos_price:.2f}")
        c2.metric("Fair Value (2×)", f"${r.get('fair_value', 0):.2f}")
        c3.metric("Current Price",   f"${r.get('current_stock_price', 0):.2f}")
        st.divider()

    # Profitability
    if result.get("profitability_result"):
        r = result["profitability_result"]
        if not r.get("error"):
            st.subheader("💰 Profitability")
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("ROE",        _pct(r.get("roe")))
            c2.metric("ROA",        _pct(r.get("roa")))
            c3.metric("ROIC",       _pct(r.get("roic")))
            c4.metric("Net Margin", _pct(r.get("net_margin")))


# ─── AI Moat Page ─────────────────────────────────────────────────────────────


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
        load_news = st.checkbox("Load Yahoo Finance News", value=False, key="moat_news")
        load_yahoo_info = st.checkbox(
            "Load Yahoo Company Info", value=False, key="moat_yahoo_info"
        )

    st.markdown("**Moat Types to Assess**")
    c1, c2, c3, c4, c5 = st.columns(5)
    brand = c1.checkbox("Brand Power", value=True, key="moat_brand")
    switch = c2.checkbox("Switching Costs", value=True, key="moat_switch")
    network = c3.checkbox("Network Effects", value=True, key="moat_net")
    cost = c4.checkbox("Cost Advantages", value=True, key="moat_cost")
    scale = c5.checkbox("Efficient Scale", value=True, key="moat_scale")
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
        )

        log.info(
            "[app][moat_start] ticker=%s year=%d load_sec=%s load_earnings=%s load_news=%s load_yahoo_info=%s pipeline_version=%s",
            ticker,
            year,
            load_sec,
            load_earnings,
            load_news,
            load_yahoo_info,
            PIPELINE_VERSION,
        )

        with st.spinner(
            f"Running AI Moat Analysis for {ticker} — this may take a moment..."
        ):
            try:
                from backend.valuekit_ai.data_pipeline.load_sec_data import (
                    load_company_data as _load_sec,
                    load_news_data as _load_news,
                    load_yahoo_info_data as _load_yahoo_info,
                )
                if load_sec:
                    _load_sec(ticker)
                if load_news:
                    _load_news(ticker)
                if load_yahoo_info:
                    info_result = _load_yahoo_info(ticker)
                    if info_result.get("status") != "success":
                        st.warning(f"Yahoo Company Info could not be loaded: {info_result.get('message', 'unknown error')}")
                    else:
                        log.info(
                            "[app][yahoo_info_loaded] ticker=%s chunks=%d",
                            ticker, info_result.get("chunks_created", 0),
                        )

                analyzer = ValueKitAnalyzer()

                # Business Model (only when SEC/earnings/news/info data is loaded)
                bm_result = None
                if load_sec or load_earnings or load_news or load_yahoo_info:
                    bm_result = (
                        analyzer.ai_analyzer.moat_analyzer.analyze_business_model(
                            ticker,
                            top_k=st.session_state["pipeline_top_k"],
                            temperature=st.session_state["pipeline_temperature"],
                        )
                    )

                # Moat analysis
                result = analyzer.analyze_stock_complete(
                    ticker=ticker,
                    year=year,
                    auto_estimate_growth=False,
                    discount_rate=0.15,
                    margin_of_safety=0.50,
                    load_sec_data=False,        # already loaded above
                    load_earnings_data=load_earnings,
                    load_news_data=False,       # already loaded above
                    load_yahoo_info_data=False, # already loaded above
                    config=config,
                    top_k=st.session_state["pipeline_top_k"],
                    temperature=st.session_state["pipeline_temperature"],
                )

                # Quantitative pipeline (no moat re-run; FMP data is cached)
                from backend.valuekit_ai.config.analysis_config import quantitative_only
                quant_result = analyzer.analyze_stock_complete(
                    ticker=ticker,
                    year=year,
                    auto_estimate_growth=True,
                    discount_rate=0.15,
                    margin_of_safety=0.50,
                    load_sec_data=False,
                    load_earnings_data=False,
                    config=quantitative_only(),
                )

                ai = result.get("ai_decision")
                if not ai:
                    st.warning("Moat analysis returned no result.")
                    return

                st.session_state["analysis_count"] += 1

                st.subheader(f"Moat Results — {ticker} ({year})")
                _render_moat_results(ticker, year, ai, bm_result=bm_result)
                _render_fundamentals(ticker, year)

                with st.expander("📊 Full Pipeline Overview", expanded=False):
                    _render_quant_pipeline(quant_result, mos_pct=0.50)

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

    col3, col4, col5, col6 = st.columns(4)
    load_sec = col3.checkbox("Load SEC Filings", value=False, key="ov_sec")
    load_earnings = col4.checkbox(
        "Load Earnings Transcripts", value=False, key="ov_earn"
    )
    load_news = col5.checkbox(
        "Load Yahoo Finance News", value=False, key="ov_news"
    )
    load_yahoo_info = col6.checkbox(
        "Load Yahoo Company Info", value=False, key="ov_yahoo_info"
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
                # Load RAG sources directly before analysis (same pattern as moat page)
                from backend.valuekit_ai.data_pipeline.load_sec_data import (
                    load_company_data as _load_sec,
                    load_news_data as _load_news,
                    load_yahoo_info_data as _load_yahoo_info,
                )
                if load_sec:
                    _load_sec(ticker)
                if load_news:
                    _load_news(ticker)
                if load_yahoo_info:
                    info_result = _load_yahoo_info(ticker)
                    if info_result.get("status") != "success":
                        st.warning(f"Yahoo Company Info could not be loaded: {info_result.get('message', 'unknown error')}")
                    else:
                        log.info(
                            "[app][yahoo_info_loaded] ticker=%s chunks=%d",
                            ticker, info_result.get("chunks_created", 0),
                        )

                analyzer = ValueKitAnalyzer()

                # Business Model — always attempt; returns status=error if no docs indexed
                ov_bm_result = analyzer.ai_analyzer.moat_analyzer.analyze_business_model(
                    ticker,
                    top_k=st.session_state["pipeline_top_k"],
                    temperature=st.session_state["pipeline_temperature"],
                )

                result = analyzer.analyze_stock_complete(
                    ticker=ticker,
                    year=year,
                    auto_estimate_growth=True,
                    discount_rate=discount_rate,
                    margin_of_safety=mos_pct,
                    load_sec_data=False,       # already loaded above
                    load_earnings_data=load_earnings,
                    load_news_data=False,      # already loaded above
                    load_yahoo_info_data=False, # already loaded above
                    config=config,
                    top_k=st.session_state["pipeline_top_k"],
                    temperature=st.session_state["pipeline_temperature"],
                )

                st.session_state["analysis_count"] += 1
                # Persist result so it survives sidebar-triggered reruns
                st.session_state["ov_result"] = {
                    "result": result,
                    "ticker": ticker,
                    "year": year,
                    "mos_pct": mos_pct,
                    "bm_result": ov_bm_result,
                }

                log.info(
                    "[app][overview_complete] ticker=%s recommendation=%s",
                    ticker,
                    result.get("final_recommendation", "N/A"),
                )

            except Exception as e:
                log.error("[app][overview_error] ticker=%s error=%s", ticker_input, e, exc_info=True)
                st.error(f"An error occurred during analysis: {e}")

    # ── Render last result (persists across reruns, e.g. from sidebar refresh) ──
    _ov = st.session_state.get("ov_result")
    if _ov:
        result    = _ov["result"]
        ticker    = _ov["ticker"]
        year      = _ov["year"]
        mos_pct   = _ov["mos_pct"]
        ov_bm_result = _ov.get("bm_result")

        # Growth Rate transparency (consensus source)
        gc = result.get("growth_consensus")
        if gc:
            _METHOD_LABELS_OV = {
                "consensus":     "CAGR (60%) + Analyst (40%)",
                "own_cagr_only": "CAGR only",
                "analyst_only":  "Analyst only",
                "fallback":      "Fallback 10%",
            }
            gc_label = _METHOD_LABELS_OV.get(gc["method"], gc["method"])
            own = gc["sources"].get("own_cagr")
            ana = gc["sources"].get("analyst_estimate")
            cap_note = " (capped at 25%)" if gc["capped"] else ""
            parts = [f"Growth rate: **{gc['rate']*100:.1f}%{cap_note}** — {gc_label}"]
            if own is not None:
                parts.append(f"Own CAGR: {own*100:.1f}%")
            if ana is not None:
                parts.append(f"Analyst: {ana*100:.1f}%")
            st.caption("  |  ".join(parts))

        # ── 3-Score Model ─────────────────────────────────────────────────
        ai = result.get("ai_decision") or {}
        overall = ai.get("overall_score")
        if overall is not None:
            st.subheader("🎯 Combined Investment Score")

            # Row 1: the three component scores + combined
            cs1, cs2, cs3, cs4 = st.columns(4)
            cs1.metric(
                "Robustness Score (40%)",
                f"{ai.get('robustness_score', 0)} / 100",
                help="ROIC · FCF Yield · Net Margin · CAGR",
            )
            cs2.metric(
                "Valuation Score (20%)",
                f"{ai.get('valuation_score', 0)} / 100",
                help="MOS · TenCap · PBT — price vs fair value",
            )
            cs3.metric(
                "Moat Score (40%)",
                f"{ai.get('qualitative_score', 0)} / 100",
                help="RAG qualitative moat analysis",
            )
            cs4.metric(
                "Combined Score",
                f"{overall} / 100",
                help="Quality×0.40 + Valuation×0.20 + Moat×0.40",
            )

            # Row 2: moat strength + confidence
            ms1, ms2 = st.columns(2)
            ms1.metric(
                "Moat Strength",
                ai.get("moat_strength", "N/A"),
                help="Wide ≥ 65% moat score · Narrow ≥ 40% · None below 40%",
            )
            ms2.metric("Confidence", ai.get("confidence", "N/A"))

            # Decision banner
            decision = ai.get("decision", "N/A")
            _DECISION_RULES = {
                "STRONG BUY": "Score ≥ 80",
                "BUY":        "Score ≥ 70",
                "HOLD":       "Score ≥ 50",
                "PASS":       "Score < 50",
            }
            rule = _DECISION_RULES.get(decision, "—")
            rec_fn = {
                "STRONG BUY": st.success,
                "BUY":        st.success,
                "HOLD":       st.warning,
                "PASS":       st.error,
            }.get(decision, st.info)
            rec_fn(f"**{decision}** — {rule}")
            st.caption(f"Decision rule applied: Combined={overall} → {decision}")

            st.divider()

        with st.expander("🤖 AI Moat Analysis", expanded=False):
            _render_moat_results(ticker, year, ai, bm_result=ov_bm_result)

        _render_fundamentals(ticker, year)

        _render_quant_pipeline(result, mos_pct)


# ─── Auth helpers ────────────────────────────────────────────────────────────


def _apply_pepper_patch(pepper: str) -> None:
    """Patch bcrypt.checkpw to apply an HMAC-SHA256 pepper before verification.

    Only patches once per process — safe to call on every Streamlit rerun.

    The password hash stored in secrets.toml must be generated with the same
    pepper.  Use this one-liner to produce a new hash:

        python - <<'EOF'
        import bcrypt, hashlib, hmac, getpass
        pepper  = input("pepper: ")
        pw      = getpass.getpass("password: ")
        peppered = hmac.new(pepper.encode(), pw.encode(), hashlib.sha256).hexdigest()
        print(bcrypt.hashpw(peppered.encode(), bcrypt.gensalt(12)).decode())
        EOF
    """
    if getattr(_bcrypt_module, "_valuekit_pepper_patched", False):
        return
    _real_checkpw = _bcrypt_module.checkpw

    def _peppered_checkpw(password: bytes, hashed_password: bytes) -> bool:
        peppered = _hmac_module.new(
            pepper.encode(), password, _hashlib_module.sha256
        ).hexdigest().encode()
        return _real_checkpw(peppered, hashed_password)

    _bcrypt_module.checkpw = _peppered_checkpw
    _bcrypt_module._valuekit_pepper_patched = True


# ─── Main ────────────────────────────────────────────────────────────────────


def main():
    _init_session_state()

    creds = st.secrets.get("auth", {})

    pepper = creds.get("pepper", "")
    if pepper:
        _apply_pepper_patch(pepper)
    else:
        log.warning("[auth] pepper not configured in secrets.toml — falling back to bcrypt-only")

    import json

    def _secrets_to_dict(obj):
        """Recursively convert AttrDict/secrets to plain mutable dict."""
        if hasattr(obj, "items"):
            return {k: _secrets_to_dict(v) for k, v in obj.items()}
        return obj

    credentials_mutable = _secrets_to_dict(st.secrets["auth"]["credentials"])
    authenticator = stauth.Authenticate(
        credentials=credentials_mutable,
        cookie_name=creds.get("cookie_name", "valuekit_auth"),
        cookie_key=creds.get("cookie_key", "changeme"),
        cookie_expiry_days=int(creds.get("cookie_expiry_days", 1)),
    )

    authenticator.login(location="main", fields={"Form name": "ValueKit AI — Login"})
    auth_status = st.session_state.get("authentication_status")
    name = st.session_state.get("name")
    username = st.session_state.get("username")

    if auth_status is False:
        st.error("Username oder Passwort falsch.")
        return
    if auth_status is None:
        st.info("Bitte einloggen.")
        return

    # Authenticated
    authenticator.logout("Logout", "sidebar")
    st.sidebar.caption(f"Eingeloggt als: **{name}**")

    selected = _render_sidebar()
    PAGE_FN = {
        "📊 Overview": _page_overview,
        "📈 CAGR Growth Estimate": _page_cagr,
        "🛡️ Margin of Safety (MOS)": _page_mos,
        "💰 Profitability Metrics": _page_profitability,
        "⏱️ Payback Time (PBT)": _page_pbt,
        "🔟 TenCap Valuation": _page_tencap,
        "🤖 AI Moat Analysis": _page_moat,
    }
    PAGE_FN[selected]()


if __name__ == "__main__":
    main()
