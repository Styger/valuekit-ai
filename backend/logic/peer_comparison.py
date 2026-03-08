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


if __name__ == "__main__":
    import logging

    logging.basicConfig(level=logging.INFO)

    for test_ticker in ["AAPL", "NESN", "NOVO-B", "TSLA"]:
        result = get_peers(test_ticker)
        print(f"{test_ticker:10} → {result}")
