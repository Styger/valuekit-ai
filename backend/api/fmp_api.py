import requests
from datetime import datetime, timedelta
from typing import Optional, Tuple
import os
import sys
import logging
import re
import os
from backend.cache import get_cache_manager

log = logging.getLogger(__name__)

from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


def get_latest_common_year(
    requested_year: int, *data_sources, ticker: str = None, show_warning: bool = True
) -> dict:
    """
    Find latest year that exists in ALL provided data sources

    Returns:
        dict with:
            - year: Actual year to use
            - fallback: Boolean indicating if fallback was used
            - requested_year: The originally requested year
    """
    # Get available years from each source
    year_sets = []
    for data in data_sources:
        if data:
            years = {int(x.get("calendarYear")) for x in data if x.get("calendarYear")}
            year_sets.append(years)

    # If no data sources provided, return requested year (no fallback)
    if not year_sets:
        return {
            "year": requested_year,
            "fallback": False,
            "requested_year": requested_year,
        }

    # Find intersection (years that exist in ALL sources)
    common_years = set.intersection(*year_sets)

    # If no common years found, fallback to current year - 2
    if not common_years:
        fallback = datetime.now().year - 2
        if show_warning:
            ticker_str = f" for {ticker}" if ticker else ""
            print(f"  ⚠️  No common years found{ticker_str}, using fallback {fallback}")
        return {"year": fallback, "fallback": True, "requested_year": requested_year}

    latest_common = max(common_years)

    # Check if requested year is available
    if requested_year <= latest_common:
        return {
            "year": requested_year,
            "fallback": False,
            "requested_year": requested_year,
        }

    # Fallback to latest available
    if show_warning:
        ticker_str = f" for {ticker}" if ticker else ""
        print(
            f"  ℹ️  Year {requested_year} not fully available{ticker_str}, using {latest_common}"
        )

    return {"year": latest_common, "fallback": True, "requested_year": requested_year}


def resource_path(relative_path):
    """Pfad zur Datei – funktioniert in PyInstaller & VS Code"""
    try:
        # Wenn PyInstaller läuft
        base_path = sys._MEIPASS
    except AttributeError:
        # Wenn in VS Code ausgeführt
        base_path = os.path.abspath(os.path.dirname(__file__))
    return os.path.join(base_path, relative_path)


def get_api_key() -> str:
    try:
        import streamlit as st

        key = st.secrets.get("fmp", {}).get("api_key", "")
    except Exception:
        key = os.environ.get("FMP_API_KEY", "")
    if not key:
        raise ValueError(
            "FMP API key not configured in secrets.toml or FMP_API_KEY env var"
        )
    log.debug("[fmp_api][get_api_key] key=%s***", key[:8])
    return key


# 🆕 Singleton cache instance
_cache = None


def _get_cache():
    """Get cache manager instance"""
    global _cache
    if _cache is None:
        _cache = get_cache_manager()
    return _cache


# ============================================================================
# BALANCE SHEET - with cache
# ============================================================================


def get_balance_sheet(ticker, limit=20):
    """Get balance sheet with caching (7 day TTL)"""
    cache = _get_cache()

    # Try cache with expected latest year (current year - 1)
    expected_year = datetime.now().year - 1
    cache_key = f"{ticker}_balance_sheet_Y{expected_year}_L{limit}"

    cached = cache.get(cache_key, "fundamentals")
    if cached is not None:
        return cached

    # Cache miss - fetch fresh data
    data = _fetch_balance_sheet_uncached(ticker, limit)

    # Use actual latest year from data for cache key
    if data and len(data) > 0:
        actual_year = data[0].get("calendarYear", expected_year)
        cache_key = f"{ticker}_balance_sheet_Y{actual_year}_L{limit}"

    cache.set(cache_key, "fundamentals", data)
    return data


def _fetch_balance_sheet_uncached(ticker, limit):
    """Fetch balance sheet without cache (internal use)"""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/balance-sheet-statement/{ticker}?limit={limit}&apikey={api_key}"
    response = requests.get(url)
    return response.json()


# ============================================================================
# INCOME STATEMENT - with cache
# ============================================================================


def get_income_statement(ticker, limit=20):
    """Get income statement with caching (7 day TTL)"""
    cache = _get_cache()

    # Try cache with expected latest year (current year - 1)
    expected_year = datetime.now().year - 1
    cache_key = f"{ticker}_income_statement_Y{expected_year}_L{limit}"

    cached = cache.get(cache_key, "fundamentals")
    if cached is not None:
        return cached

    # Cache miss - fetch fresh data
    data = _fetch_income_statement_uncached(ticker, limit)

    # Use actual latest year from data for cache key
    if data and len(data) > 0:
        actual_year = data[0].get("calendarYear", expected_year)
        cache_key = f"{ticker}_income_statement_Y{actual_year}_L{limit}"

    cache.set(cache_key, "fundamentals", data)
    return data


def _fetch_income_statement_uncached(ticker, limit):
    """Fetch income statement without cache (internal use)"""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/income-statement/{ticker}?limit={limit}&apikey={api_key}"
    response = requests.get(url)
    return response.json()


# ============================================================================
# CASHFLOW STATEMENT - with cache
# ============================================================================


def get_cashflow_statement(ticker, limit=20):
    """Get cashflow statement with caching (7 day TTL)"""
    cache = _get_cache()

    # Try cache with expected latest year (current year - 1)
    expected_year = datetime.now().year - 1
    cache_key = f"{ticker}_cashflow_statement_Y{expected_year}_L{limit}"

    cached = cache.get(cache_key, "fundamentals")
    if cached is not None:
        return cached

    # Cache miss - fetch fresh data
    data = _fetch_cashflow_statement_uncached(ticker, limit)

    # Use actual latest year from data for cache key
    if data and len(data) > 0:
        actual_year = data[0].get("calendarYear", expected_year)
        cache_key = f"{ticker}_cashflow_statement_Y{actual_year}_L{limit}"

    cache.set(cache_key, "fundamentals", data)
    return data


def _fetch_cashflow_statement_uncached(ticker, limit):
    """Fetch cashflow statement without cache (internal use)"""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/cash-flow-statement/{ticker}?limit={limit}&apikey={api_key}"
    return requests.get(url).json()


# ============================================================================
# KEY METRICS - with cache
# ============================================================================


def get_key_metrics(ticker, limit=20):
    """Get key metrics with caching (7 day TTL)"""
    cache = _get_cache()

    # Try cache with expected latest year (current year - 1)
    expected_year = datetime.now().year - 1
    cache_key = f"{ticker}_key_metrics_Y{expected_year}_L{limit}"

    cached = cache.get(cache_key, "fundamentals")
    if cached is not None:
        return cached

    # Cache miss - fetch fresh data
    data = _fetch_key_metrics_uncached(ticker, limit)

    # Use actual latest year from data for cache key
    if data and len(data) > 0:
        actual_year = data[0].get("calendarYear", expected_year)
        cache_key = f"{ticker}_key_metrics_Y{actual_year}_L{limit}"

    cache.set(cache_key, "fundamentals", data)
    return data


def _fetch_key_metrics_uncached(ticker, limit):
    """Fetch key metrics without cache (internal use)"""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/key-metrics/{ticker}?limit={limit}&apikey={api_key}"
    return requests.get(url).json()


# ============================================================================
# DCF - with cache
# ============================================================================


def get_dcf(ticker):
    """Get DCF with caching (90 day TTL)"""
    cache = _get_cache()
    cache_key = f"{ticker}_dcf"

    return cache.get_or_fetch(
        key=cache_key,
        data_type="fundamentals",
        fetch_fn=lambda: _fetch_dcf_uncached(ticker),
    )


def _fetch_dcf_uncached(ticker):
    """Fetch DCF without cache (internal use)"""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/discounted-cash-flow/{ticker}?apikey={api_key}"
    return requests.get(url).json()


# ============================================================================
# HISTORICAL PRICES - with cache (never expires)
# ============================================================================


def fetch_historical_price_json(ticker, date):
    """
    Fetch historical price with caching (never expires - historical data is immutable)

    Args:
        ticker: Stock ticker
        date: Date in YYYY-MM-DD format

    Returns:
        Price data JSON
    """
    cache = _get_cache()
    cache_key = f"{ticker}_price_{date}"

    return cache.get_or_fetch(
        key=cache_key,
        data_type="historical_prices",
        fetch_fn=lambda: _fetch_historical_price_uncached(ticker, date),
    )


def _fetch_historical_price_uncached(ticker, date):
    """
    Fetch historical price without cache (internal use)
    Keep it simple so it's easy to mock in tests.
    """
    api = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/historical-price-full/{ticker}"
    params = {"from": date, "to": date, "apikey": api}
    return requests.get(url, params=params).json()


# ============================================================================
# CURRENT PRICE - with cache (1 day TTL)
# ============================================================================


def get_current_price(ticker: str) -> Optional[float]:
    """
    Get current price with caching (1 day TTL)
    Versucht zuerst /v3/quote-short (schnell, nur Preis),
    fällt dann zurück auf /v3/quote (nur Preis extrahiert).

    Returns:
        Optional[float]: Aktueller Preis oder None falls nicht verfügbar
    """
    cache = _get_cache()
    cache_key = f"{ticker}_current_price"

    return cache.get_or_fetch(
        key=cache_key,
        data_type="current_price",
        fetch_fn=lambda: _fetch_current_price_uncached(ticker),
    )


def _fetch_current_price_uncached(ticker: str) -> Optional[float]:
    """Fetch current price without cache (internal use)"""
    # 1. Versuch: quote-short
    try:
        data = fetch_quote_short(ticker)
        if isinstance(data, list) and data:
            price = data[0].get("price")
            if price is not None:
                return float(price)
    except Exception as e:
        log.error(f"[get_current_price] quote-short error: {e}")

    # 2. Versuch: detailierte quote
    try:
        data2 = fetch_quote(ticker)
        if isinstance(data2, list) and data2:
            item = data2[0]
            price = item.get("price")
            if price is not None:
                return float(price)
    except Exception as e:
        log.error(f"[get_current_price] quote error: {e}")

    return None


def fetch_quote_short(ticker: str):
    """Low-level HTTP: holt einfache Quote-Daten (/v3/quote-short)."""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/quote-short/{ticker}?apikey={api_key}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


def fetch_quote(ticker: str):
    """Low-level HTTP: holt detailierte Quote-Daten (/v3/quote)."""
    api_key = get_api_key()
    url = f"https://financialmodelingprep.com/api/v3/quote/{ticker}?apikey={api_key}"
    resp = requests.get(url, timeout=15)
    resp.raise_for_status()
    return resp.json()


# ============================================================================
# HELPER FUNCTIONS (use cached functions above)
# ============================================================================


def get_year_data_by_range(ticker, start_year, years=4):
    """
    Get year data by range (uses cached data automatically)
    Automatically falls back to latest complete year if requested year unavailable
    """
    income = get_income_statement(ticker)
    balance = get_balance_sheet(ticker)
    cashflow = get_cashflow_statement(ticker)
    metrics = get_key_metrics(ticker)

    # Use utility function to find latest common year
    year_info = get_latest_common_year(
        start_year, income, balance, cashflow, ticker=ticker, show_warning=True
    )

    start_year = year_info["year"]

    def get_by_year(data, year):
        for entry in data:
            if str(entry.get("calendarYear")) == str(year):
                return entry
        return {}

    results = []
    book_list, eps_list, revenue_list, cashflow_list, fcf_list = (
        [],
        [],
        [],
        [],
        [],
    )

    for year in range(start_year, start_year + years + 1):
        i = get_by_year(income, year)
        c = get_by_year(cashflow, year)
        m = get_by_year(metrics, year)

        # Skip years with missing or incomplete income data
        eps = i.get("eps", 0)
        if not i or eps == 0:
            continue

        revenue = i.get("revenue", 0) / 1_000_000
        fcf = c.get("freeCashFlow", 0) / 1_000_000
        roic = m.get("roic", None)

        results.append(
            {
                "Year": year,
                "Revenue (Mio)": round(revenue, 2),
                "Free Cash Flow (Mio)": round(fcf, 2),
                "EPS": round(eps, 2),
                "ROIC": f"{round(roic * 100, 2)} %" if roic is not None else "–",
            }
        )

        book_list.append(m.get("bookValuePerShare", 0))
        eps_list.append(i.get("eps", 0))
        revenue_list.append(m.get("revenuePerShare", 0))
        cashflow_list.append(m.get("operatingCashFlowPerShare", 0))
        fcf_list.append(m.get("freeCashFlowPerShare", 0))

    mos_metrics = {
        "book": book_list,
        "eps": eps_list,
        "revenue": revenue_list,
        "cashflow": cashflow_list,
        "fcf": fcf_list,
    }

    # Add year info to first result for fallback detection
    if results and len(results) > 0:
        results[0]["year_info"] = year_info

    return results, mos_metrics


def get_price_on_date(ticker, date):
    """
    Get price on specific date (uses cached data automatically)
    High-level parsing: extract the closing price from the fetched JSON.
    """
    log.debug(f"Requesting stock price for {ticker} on {date}")
    data = fetch_historical_price_json(ticker, date)
    if not data:
        log.warning(f"No data returned for {ticker} on {date}")
        return None

    historical = data.get("historical")
    if historical:
        price = historical[0].get("close")
        log.debug(f"Price found: {price}")
        return price
    else:
        log.warning(f"No price found for {ticker} on {date}")
        return None


def get_valid_price(
    ticker: str, base_date_str: str
) -> Tuple[Optional[float], Optional[str]]:
    """Get valid price within 14 days (uses cached data automatically)"""
    base_date = datetime.strptime(base_date_str, "%Y-%m-%d")
    for i in range(14):
        current_date = base_date - timedelta(days=i)
        date_str = current_date.strftime("%Y-%m-%d")
        price = get_price_on_date(ticker, date_str)
        log.debug(f"Trying {ticker} on {date_str}: {price}")
        if price:
            return price, date_str

    log.warning(
        f"No valid stock price found for {ticker} from {base_date_str} within 14 days"
    )
    return None, None


# ============================================================================
# TESTING
# ============================================================================

if __name__ == "__main__":
    print("=" * 70)
    print("Testing FMP API with Cache")
    print("=" * 70)

    ticker = "AAPL"

    # Test 1: Income Statement (should hit API)
    print(f"\n1st call to get_income_statement({ticker}):")
    data1 = get_income_statement(ticker, limit=5)
    print(f"   Got {len(data1)} records")

    # Test 2: Income Statement (should use cache)
    print(f"\n2nd call to get_income_statement({ticker}):")
    data2 = get_income_statement(ticker, limit=5)
    print(f"   Got {len(data2)} records")

    # Test 3: Current Price (should hit API)
    print(f"\n1st call to get_current_price({ticker}):")
    price1 = get_current_price(ticker)
    print(f"   Price: ${price1}")

    # Test 4: Current Price (should use cache)
    print(f"\n2nd call to get_current_price({ticker}):")
    price2 = get_current_price(ticker)
    print(f"   Price: ${price2}")

    # Test 5: Historical Price (should hit API)
    print(f"\n1st call to fetch_historical_price_json({ticker}, '2024-01-01'):")
    hist1 = fetch_historical_price_json(ticker, "2024-01-01")
    print(f"   Got data: {bool(hist1)}")

    # Test 6: Historical Price (should use cache - never expires!)
    print(f"\n2nd call to fetch_historical_price_json({ticker}, '2024-01-01'):")
    hist2 = fetch_historical_price_json(ticker, "2024-01-01")
    print(f"   Got data: {bool(hist2)}")

    # Cache stats
    cache = _get_cache()
    print(f"\n{'=' * 70}")
    print("CACHE STATISTICS")
    print(f"{'=' * 70}")
    stats = cache.get_stats()
    print(f"Total size: {stats['total_size_mb']} MB")
    print(f"File count: {stats['file_count']}")
    print(f"Metadata entries: {stats['metadata_entries']}")
    print()
