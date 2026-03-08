"""
Peer Comparison — Generates a list of similar publicly traded companies.

Strategy:
  1. Try FMP /v4/stock_peers endpoint (fast, no LLM cost)
  2. If FMP returns empty or fails → Claude direct API call as fallback
     (works for non-US tickers, smaller companies, ETFs)

Returns:
  List of ticker strings, max 5 peers.
"""

import logging
import sys
from pathlib import Path
from typing import List, Optional

import requests

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.api.fmp_api import get_api_key
from backend.cache import get_cache_manager

log = logging.getLogger(__name__)

_cache = None


def _get_cache():
    global _cache
    if _cache is None:
        _cache = get_cache_manager()
    return _cache


# ── FMP ───────────────────────────────────────────────────────────────────────


def _fetch_peers_fmp(ticker: str) -> List[str]:
    """
    Fetch peer list from FMP /v4/stock_peers endpoint.

    Returns list of ticker strings (empty list on failure or no data).
    """
    try:
        api_key = get_api_key()
        url = f"https://financialmodelingprep.com/api/v4/stock_peers?symbol={ticker}&apikey={api_key}"
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        data = resp.json()

        # FMP returns: [{"symbol": "AAPL", "peersList": ["MSFT", "GOOGL", ...]}]
        if isinstance(data, list) and data:
            peers = data[0].get("peersList", [])
            # Exclude the ticker itself, take max 5
            cleaned = [p for p in peers if p.upper() != ticker.upper()][:5]
            log.info("[peer_comparison][fmp_ok] ticker=%s peers=%s", ticker, cleaned)
            return cleaned

    except Exception as e:
        log.warning("[peer_comparison][fmp_error] ticker=%s error=%s", ticker, e)

    return []


# ── Claude Fallback ───────────────────────────────────────────────────────────


def _fetch_peers_claude(ticker: str) -> List[str]:
    """
    Ask Claude for peers via direct Anthropic API call.
    Used as fallback when FMP returns no data.

    Returns list of ticker strings (empty list on failure).
    """
    try:
        import os
        import toml

        # Load Anthropic API key
        api_key = None
        try:
            secrets_paths = [
                Path(".streamlit/secrets.toml"),
                Path("../.streamlit/secrets.toml"),
                Path("../../.streamlit/secrets.toml"),
            ]
            for p in secrets_paths:
                if p.exists():
                    s = toml.load(p)
                    api_key = s.get("anthropic", {}).get("api_key")
                    if api_key:
                        break
        except Exception:
            pass

        if not api_key:
            api_key = os.environ.get("ANTHROPIC_API_KEY")

        if not api_key:
            log.warning("[peer_comparison][claude_no_key] ticker=%s", ticker)
            return []

        from anthropic import Anthropic

        client = Anthropic(api_key=api_key)

        prompt = (
            f"List exactly 4-5 main publicly traded competitors of {ticker}. "
            f"Return ONLY the ticker symbols as a comma-separated list, nothing else. "
            f"Example format: MSFT, GOOGL, META, AMZN\n"
            f"If you are uncertain about any ticker, omit it. "
            f"Do not include {ticker} itself."
        )

        message = client.messages.create(
            model="claude-sonnet-4-20250514",
            max_tokens=80,
            temperature=0.0,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        # Parse comma-separated tickers, validate format
        import re

        tickers = [
            t.strip().upper()
            for t in raw.split(",")
            if re.match(r"^[A-Z]{1,5}(-[A-Z])?$", t.strip().upper())
            and t.strip().upper() != ticker.upper()
        ][:5]

        log.info("[peer_comparison][claude_ok] ticker=%s peers=%s", ticker, tickers)
        return tickers

    except Exception as e:
        log.error("[peer_comparison][claude_error] ticker=%s error=%s", ticker, e)
        return []


# ── Public API ────────────────────────────────────────────────────────────────


def get_peers(ticker: str) -> List[str]:
    """
    Get peer list for a ticker. FMP first, Claude as fallback.
    Results are cached (7 days via fundamentals TTL).

    Args:
        ticker: Stock ticker (e.g. "AAPL")

    Returns:
        List of peer ticker symbols (max 5), empty list if nothing found.
    """
    ticker = ticker.strip().upper()
    cache_key = f"{ticker}_peers"
    cache = _get_cache()

    cached = cache.get(cache_key, "fundamentals")
    if cached is not None:
        log.info("[peer_comparison][cache_hit] ticker=%s peers=%s", ticker, cached)
        return cached

    # 1. Try FMP
    peers = _fetch_peers_fmp(ticker)

    # 2. Claude fallback if FMP returned nothing
    if not peers:
        log.info("[peer_comparison][fmp_empty_fallback_to_claude] ticker=%s", ticker)
        peers = _fetch_peers_claude(ticker)

    # Cache result (even empty list to avoid repeated calls)
    cache.set(cache_key, "fundamentals", peers)
    return peers


# ── Peer Metrics Table ────────────────────────────────────────────────────────


def _fetch_peer_row(ticker: str, year: int, is_subject: bool) -> dict:
    """
    Fetch ROIC, Net Margin, CAGR (5Y), and MOS% for a single ticker.
    All failures return 'N/A' so one bad ticker never blocks the table.
    """
    row: dict = {"Ticker": ticker, "_is_subject": is_subject}

    # ── Profitability (ROIC + Net Margin) ─────────────────────────────────────
    try:
        from backend.logic.profitability import calculate_profitability_metrics_from_ticker
        prof = calculate_profitability_metrics_from_ticker(ticker, year)
        roic = prof.get("roic")
        nm   = prof.get("net_margin")
        row["ROIC"]       = f"{roic * 100:.1f}%" if roic is not None else "N/A"
        row["Net Margin"] = f"{nm   * 100:.1f}%" if nm   is not None else "N/A"
    except Exception as e:
        log.warning("[peer_metrics][profitability_error] ticker=%s error=%s", ticker, e)
        row["ROIC"] = "N/A"
        row["Net Margin"] = "N/A"

    # ── CAGR (5Y) ─────────────────────────────────────────────────────────────
    cagr_val: Optional[float] = None
    try:
        from backend.logic.cagr import get_cagr_for_screening
        cagr_val = get_cagr_for_screening(ticker, period_years=5)
        row["CAGR (5Y)"] = f"{cagr_val * 100:.1f}%" if cagr_val is not None else "N/A"
    except Exception as e:
        log.warning("[peer_metrics][cagr_error] ticker=%s error=%s", ticker, e)
        row["CAGR (5Y)"] = "N/A"

    # ── MOS% (upside to fair value) ───────────────────────────────────────────
    try:
        from backend.logic.mos import calculate_mos_value_from_ticker
        mos_result = calculate_mos_value_from_ticker(
            ticker=ticker,
            year=year,
            growth_rate=cagr_val if cagr_val is not None else 0.10,
            discount_rate=0.15,
            margin_of_safety=0.50,
        )
        fair_value = mos_result.get("Fair Value Today")
        current    = mos_result.get("Current Stock Price")
        if fair_value and current and current > 0:
            pct = (fair_value - current) / current * 100
            row["MOS%"] = f"{pct:+.1f}%"
        else:
            row["MOS%"] = "N/A"
    except Exception as e:
        log.warning("[peer_metrics][mos_error] ticker=%s error=%s", ticker, e)
        row["MOS%"] = "N/A"

    return row


def get_peer_metrics(ticker: str, year: int):
    """
    Build a comparison table for ticker + its peers.

    Returns:
        pandas.DataFrame with columns:
            Ticker | ROIC | Net Margin | CAGR (5Y) | MOS% | _is_subject
        _is_subject is True for the subject row (used for highlighting).
        N/A is used for any metric that could not be fetched.
    """
    import pandas as pd

    ticker = ticker.strip().upper()
    peers  = get_peers(ticker)
    all_tickers = [ticker] + peers

    log.info(
        "[peer_metrics][start] ticker=%s year=%d peers=%s",
        ticker, year, peers,
    )

    rows = []
    for t in all_tickers:
        row = _fetch_peer_row(t, year, is_subject=(t == ticker))
        rows.append(row)
        log.debug("[peer_metrics][row] %s", row)

    df = pd.DataFrame(rows, columns=["Ticker", "ROIC", "Net Margin", "CAGR (5Y)", "MOS%", "_is_subject"])
    log.info("[peer_metrics][done] ticker=%s rows=%d", ticker, len(df))
    return df


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    for test_ticker in ["AAPL", "NESN", "NOVO-B", "TSLA"]:
        result = get_peers(test_ticker)
        print(f"{test_ticker:10} → {result}")
