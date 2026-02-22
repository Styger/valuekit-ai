"""
ValueKit AI - Streamlit Frontend
WIPRO Edition: session security, input validation, structured error handling
"""

import logging
import re
import sys
from pathlib import Path

import streamlit as st

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import PIPELINE_VERSION
from backend.valuekit_ai.core.valuekit_integration import ValueKitAnalyzer
from backend.valuekit_ai.config.analysis_config import AnalysisConfig

# Logging setup
logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# Session security constants
MAX_ANALYSES_PER_SESSION = 10

# ─── Page Config ────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="ValueKit AI",
    page_icon="📊",
    layout="wide",
)


def _init_session_state():
    """Initialize session state with defaults"""
    if "analysis_count" not in st.session_state:
        st.session_state["analysis_count"] = 0
    if "last_result" not in st.session_state:
        st.session_state["last_result"] = None


def _validate_ticker(ticker: str) -> str:
    """
    Validate ticker symbol at entry point

    Args:
        ticker: Raw ticker input

    Returns:
        Validated uppercase ticker

    Raises:
        ValueError: If ticker does not match expected pattern
    """
    cleaned = ticker.strip().upper()
    if not re.match(r"^[A-Z]{1,5}$", cleaned):
        raise ValueError(
            f"Invalid ticker symbol '{ticker}'. Expected 1-5 uppercase letters (e.g. AAPL)."
        )
    return cleaned


def _check_session_limit():
    """Enforce analysis limit per session"""
    if st.session_state["analysis_count"] >= MAX_ANALYSES_PER_SESSION:
        st.error(
            f"Session limit of {MAX_ANALYSES_PER_SESSION} analyses reached. "
            "Please start a new session."
        )
        st.stop()


def _render_sidebar() -> dict:
    """Render sidebar with analysis parameters"""
    st.sidebar.header("Analysis Parameters")

    ticker_input = st.sidebar.text_input(
        "Ticker Symbol",
        value="AAPL",
        max_chars=5,
        help="Enter a valid stock ticker (e.g. AAPL, MSFT)",
    )

    year = st.sidebar.number_input(
        "Base Year",
        min_value=2010,
        max_value=2024,
        value=2024,
        step=1,
    )

    discount_rate = (
        st.sidebar.slider(
            "Discount Rate (%)",
            min_value=5,
            max_value=20,
            value=15,
            step=1,
        )
        / 100
    )

    mos_pct = (
        st.sidebar.slider(
            "Margin of Safety (%)",
            min_value=10,
            max_value=60,
            value=50,
            step=5,
        )
        / 100
    )

    st.sidebar.divider()
    st.sidebar.subheader("Components")

    run_cagr = st.sidebar.checkbox("CAGR Growth Estimate", value=True)
    run_mos = st.sidebar.checkbox("Margin of Safety (MOS)", value=True)
    run_profitability = st.sidebar.checkbox("Profitability Metrics", value=True)
    run_moat = st.sidebar.checkbox("AI Moat Analysis", value=True)
    load_sec = st.sidebar.checkbox("Load SEC Filings", value=False)
    load_earnings = st.sidebar.checkbox("Load Earnings Transcripts", value=False)

    st.sidebar.divider()
    st.sidebar.caption(f"Pipeline Version: {PIPELINE_VERSION}")
    st.sidebar.caption(
        f"Analyses this session: {st.session_state.get('analysis_count', 0)}/{MAX_ANALYSES_PER_SESSION}"
    )

    return {
        "ticker_input": ticker_input,
        "year": year,
        "discount_rate": discount_rate,
        "mos_pct": mos_pct,
        "run_cagr": run_cagr,
        "run_mos": run_mos,
        "run_profitability": run_profitability,
        "run_moat": run_moat,
        "load_sec": load_sec,
        "load_earnings": load_earnings,
    }


def _render_mos_result(mos_result: dict):
    """Render MOS calculation results"""
    if not mos_result:
        return

    st.subheader("Margin of Safety Analysis")
    col1, col2, col3, col4 = st.columns(4)

    col1.metric("Fair Value", f"${mos_result.get('Fair Value Today', 0):.2f}")
    col2.metric("MOS Price", f"${mos_result.get('MOS Price', 0):.2f}")
    col3.metric(
        "Current Price",
        f"${mos_result.get('Current Stock Price', 0):.2f}",
    )
    col4.metric("EPS", f"${mos_result.get('EPS_now', 0):.2f}")

    st.info(
        f"**Price vs Fair Value:** {mos_result.get('Price vs Fair Value', 'N/A')}  \n"
        f"**Recommendation:** {mos_result.get('Investment Recommendation', 'N/A')}  \n"
        f"**Growth Rate Used:** {mos_result.get('Growth Rate', 0):.1f}%  \n"
        f"**Margin of Safety Applied:** {mos_result.get('Margin of Safety', 'N/A')}"
    )


def _render_profitability_result(prof_result: dict):
    """Render profitability metrics"""
    if not prof_result or prof_result.get("error"):
        return

    st.subheader("Profitability Metrics")
    col1, col2, col3, col4 = st.columns(4)

    def pct(val):
        return f"{val * 100:.1f}%" if val is not None else "N/A"

    col1.metric("ROE", pct(prof_result.get("roe")))
    col2.metric("ROA", pct(prof_result.get("roa")))
    col3.metric("ROIC", pct(prof_result.get("roic")))
    col4.metric("Net Margin", pct(prof_result.get("net_margin")))


def _render_moat_result(ai_decision):
    """Render AI moat analysis results"""
    if not ai_decision:
        return

    st.subheader("AI Moat Analysis")

    col1, col2, col3 = st.columns(3)
    col1.metric("Decision", ai_decision.get("decision", "N/A"))
    col2.metric("Confidence", ai_decision.get("confidence", "N/A"))
    col3.metric("Overall Score", f"{ai_decision.get('overall_score', 0)}/100")

    st.write("**Reasoning:**", ai_decision.get("reasoning", ""))

    if ai_decision.get("red_flags"):
        st.warning(
            "**Red Flags Identified:**\n"
            + "\n".join(f"- {rf}" for rf in ai_decision["red_flags"])
        )


def main():
    _init_session_state()

    st.title("📊 ValueKit AI")
    st.caption(f"Value Investing Analysis — Pipeline v{PIPELINE_VERSION}")

    params = _render_sidebar()

    if st.button("Run Analysis", type="primary"):
        _check_session_limit()

        # Input validation at entry point
        try:
            ticker = _validate_ticker(params["ticker_input"])
        except ValueError as e:
            st.error(str(e))
            log.warning("[app][invalid_ticker] input='%s'", params["ticker_input"])
            st.stop()

        log.info(
            "[app][analysis_start] ticker=%s year=%d discount=%.2f mos=%.2f "
            "pipeline_version=%s",
            ticker,
            params["year"],
            params["discount_rate"],
            params["mos_pct"],
            PIPELINE_VERSION,
        )

        config = AnalysisConfig(
            run_cagr=params["run_cagr"],
            run_mos=params["run_mos"],
            run_profitability=params["run_profitability"],
            run_moat_analysis=params["run_moat"],
        )

        with st.spinner(f"Analysing {ticker}..."):
            try:
                analyzer = ValueKitAnalyzer()
                result = analyzer.analyze_stock_complete(
                    ticker=ticker,
                    year=params["year"],
                    auto_estimate_growth=params["run_cagr"],
                    discount_rate=params["discount_rate"],
                    margin_of_safety=params["mos_pct"],
                    load_sec_data=params["load_sec"],
                    load_earnings_data=params["load_earnings"],
                    config=config,
                )

                st.session_state["analysis_count"] += 1
                st.session_state["last_result"] = result

                # Final recommendation banner
                rec = result.get("final_recommendation", "N/A")
                st.success(f"**Final Recommendation:** {rec}")

                # Render components
                _render_mos_result(result.get("mos_result"))
                st.divider()
                _render_profitability_result(result.get("profitability_result"))
                st.divider()
                _render_moat_result(result.get("ai_decision"))

                log.info(
                    "[app][analysis_complete] ticker=%s recommendation=%s",
                    ticker,
                    rec,
                )

            except Exception as e:
                # Never expose raw errors to UI — log server-side only
                log.error("[app][analysis_error] ticker=%s error=%s", ticker, e)
                st.error(
                    "An error occurred during analysis. Please try again or contact support."
                )


if __name__ == "__main__":
    main()
