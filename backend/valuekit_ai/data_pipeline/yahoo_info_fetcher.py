"""
Company Info Fetcher — using FMP API
Fetches company metadata for moat analysis context.
Previously used yfinance .info (rate-limited); now uses FMP profile + ratios-ttm.
"""

import re as _re
import logging
import sys
from pathlib import Path
from typing import Dict

import requests

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.cache import get_cache_manager
from backend.valuekit_ai.utils.sanitize import _sanitize_for_prompt

log = logging.getLogger(__name__)

_TICKER_RE = _re.compile(r"^[A-Z]{1,5}$")

_FMP_BASE = "https://financialmodelingprep.com/api/v3"


def fetch_yahoo_info(ticker: str) -> Dict:
    """
    Fetch company metadata for moat analysis context via FMP API.

    Extracts: longBusinessSummary, sector, industry, fullTimeEmployees,
    marketCap, trailingPE, returnOnEquity, grossMargins.
    Result is cached with SHA-256 integrity check (7-day TTL).

    Args:
        ticker: Stock ticker (1–5 uppercase letters).

    Returns:
        Dict with extracted fields, or empty dict on any error.
    """
    ticker = ticker.strip().upper()
    if not _TICKER_RE.match(ticker):
        log.error("[yahoo_info_fetcher][invalid_ticker] ticker=%s", ticker)
        return {}

    cache = get_cache_manager()
    cache_key = f"{ticker}_yahoo_info"

    cached = cache.get(cache_key, "fundamentals")
    if cached is not None:
        log.info("[yahoo_info_fetcher][cache_hit] ticker=%s", ticker)
        return cached

    log.info("[yahoo_info_fetcher][fetch] ticker=%s", ticker)

    try:
        from backend.api.fmp_api import get_api_key
        api_key = get_api_key()
    except Exception as e:
        log.error("[yahoo_info_fetcher][api_key_error] ticker=%s error=%s", ticker, e)
        return {}

    result: Dict = {}

    # ── Company profile (sector, industry, description, employees, mktCap) ───
    try:
        resp = requests.get(
            f"{_FMP_BASE}/profile/{ticker}",
            params={"apikey": api_key},
            timeout=10,
        )
        data = resp.json()
        if isinstance(data, list) and data:
            p = data[0]
            desc = p.get("description") or ""
            if desc:
                result["longBusinessSummary"] = _sanitize_for_prompt(desc)
            for src, dst in [
                ("sector", "sector"),
                ("industry", "industry"),
                ("fullTimeEmployees", "fullTimeEmployees"),
                ("mktCap", "marketCap"),
            ]:
                val = p.get(src)
                if val is not None:
                    result[dst] = val
        log.info(
            "[yahoo_info_fetcher][fmp_profile] ticker=%s fields=%d", ticker, len(result)
        )
    except Exception as e:
        log.warning(
            "[yahoo_info_fetcher][fmp_profile_error] ticker=%s error=%s", ticker, e
        )

    # ── TTM ratios (PE, ROE, gross margins) ──────────────────────────────────
    try:
        resp = requests.get(
            f"{_FMP_BASE}/ratios-ttm/{ticker}",
            params={"apikey": api_key},
            timeout=10,
        )
        data = resp.json()
        if isinstance(data, list) and data:
            r = data[0]
            for src, dst in [
                ("peRatioTTM", "trailingPE"),
                ("returnOnEquityTTM", "returnOnEquity"),
                ("grossProfitMarginTTM", "grossMargins"),
            ]:
                val = r.get(src)
                if val is not None:
                    result[dst] = val
        log.info("[yahoo_info_fetcher][fmp_ratios] ticker=%s", ticker)
    except Exception as e:
        log.warning(
            "[yahoo_info_fetcher][fmp_ratios_error] ticker=%s error=%s", ticker, e
        )

    if not result:
        log.error("[yahoo_info_fetcher][no_data] ticker=%s", ticker)
        return {}

    log.info(
        "[yahoo_info_fetcher][complete] ticker=%s fields_extracted=%d",
        ticker,
        len(result),
    )

    # PIPELINE_VERSION is embedded automatically by cache_manager.set()
    cache.set(cache_key, "fundamentals", result)
    return result


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    info = fetch_yahoo_info("AAPL")
    log.info("Result: %d fields", len(info))
    for k, v in info.items():
        snippet = str(v)[:120] if isinstance(v, str) else v
        log.info("  %s = %s", k, snippet)
