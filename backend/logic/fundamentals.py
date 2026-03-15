"""
Quantitative Fundamentals Check
Calculates financial health metrics for moat analysis context.
Always runs as part of AI Moat Analysis — not optional.
"""

from typing import Dict, List, Optional
import sys
from pathlib import Path
import logging

log = logging.getLogger(__name__)

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.api import fmp_api


def _status(value, ok_threshold, warn_threshold, lower_is_better=False) -> str:
    """
    Returns OK / Warning / Flag based on thresholds.
    lower_is_better=True: lower values are better (e.g. Debt/Equity)
    """
    if value is None:
        return "N/A"
    if lower_is_better:
        if value <= ok_threshold:
            return "OK"
        elif value <= warn_threshold:
            return "Warning"
        else:
            return "Flag"
    else:
        if value >= ok_threshold:
            return "OK"
        elif value >= warn_threshold:
            return "Warning"
        else:
            return "Flag"


def check_fundamentals(ticker: str, year: int, base_year: int) -> List[Dict]:
    """
    Run quantitative fundamentals check for a ticker.

    Args:
        ticker:    Stock ticker
        year:      Requested year — fed to get_latest_common_year to find
                   actual_year for balance-sheet point-in-time checks
                   (Debt/Equity, Current Ratio).
        base_year: Explicit anchor for 3-year trend windows.
                   Trend loops use base_year-2 → base_year-1 → base_year.

    Checks:
      1. Debt/Equity        — Flag if > 2.0, Warning if > 1.0  (uses actual_year)
      2. FCF Trend (3Y)     — OK if positive & growing, Warning if flat, Flag if negative  (uses base_year)
      3. Gross Margin Trend — OK if stable/improving, Warning if -5pp, Flag if -10pp  (uses base_year)
      4. Net Margin Trend   — OK if stable/improving, Warning if -3pp, Flag if -7pp  (uses base_year)
      5. Current Ratio      — Flag if < 1.0, Warning if < 1.5  (uses actual_year)

    Returns:
        List of dicts with keys: metric, value, status, note
    """
    results = []

    balance = fmp_api.get_balance_sheet(ticker, limit=5)
    income = fmp_api.get_income_statement(ticker, limit=5)
    cashflow = fmp_api.get_cashflow_statement(ticker, limit=5)

    def get_year(data, target_year):
        for e in data:
            if str(e.get("calendarYear")) == str(target_year):
                return e
        return {}

    # Use smart year fallback
    year_info = fmp_api.get_latest_common_year(
        year, balance, income, cashflow, ticker=ticker, show_warning=False
    )
    actual_year = year_info["year"]

    bs = get_year(balance, actual_year)
    is_ = get_year(income, actual_year)
    cf = get_year(cashflow, actual_year)

    # ── 1. Debt / Equity ─────────────────────────────────────────────────────
    total_debt = bs.get("totalDebt") or 0
    equity = bs.get("totalStockholdersEquity") or bs.get("totalEquity") or 0
    de_ratio = round(total_debt / equity, 2) if equity > 0 else None

    results.append(
        {
            "metric": "Debt / Equity",
            "value": f"{de_ratio:.2f}×" if de_ratio is not None else "N/A",
            "raw": de_ratio,
            "status": _status(de_ratio, 1.0, 2.0, lower_is_better=True)
            if de_ratio is not None
            else "N/A",
            "note": (
                "High leverage — potential financial risk"
                if de_ratio is not None and de_ratio > 2.0
                else "Moderate leverage"
                if de_ratio is not None and de_ratio > 1.0
                else "Conservative balance sheet"
                if de_ratio is not None
                else "Insufficient data"
            ),
        }
    )

    # ── 2. FCF Trend (3 years) ────────────────────────────────────────────────
    fcf_years = []
    for y in [base_year - 2, base_year - 1, base_year]:
        row = get_year(cashflow, y)
        fcf = row.get("freeCashFlow")
        if fcf is not None:
            fcf_years.append((y, fcf / 1_000_000))

    if len(fcf_years) >= 2:
        fcf_vals = [v for _, v in fcf_years]
        latest_fcf = fcf_vals[-1]
        positive = all(v > 0 for v in fcf_vals)
        growing = fcf_vals[-1] > fcf_vals[0]

        if positive and growing:
            fcf_status, fcf_note = "OK", "Positive and growing FCF over 3 years"
        elif positive:
            fcf_status, fcf_note = (
                "Warning",
                "Positive FCF but trend is flat or declining",
            )
        else:
            fcf_status, fcf_note = "Flag", "Negative FCF detected — cash burn risk"

        trend_str = " → ".join(f"${v:,.0f}M" for _, v in fcf_years)
        results.append(
            {
                "metric": "FCF Trend (3Y)",
                "value": trend_str,
                "raw": latest_fcf,
                "status": fcf_status,
                "note": fcf_note,
            }
        )
    else:
        results.append(
            {
                "metric": "FCF Trend (3Y)",
                "value": "N/A",
                "raw": None,
                "status": "N/A",
                "note": "Insufficient historical data",
            }
        )

    # ── 3. Gross Margin Trend ─────────────────────────────────────────────────
    gm_years = []
    for y in [base_year - 2, base_year - 1, base_year]:
        row = get_year(income, y)
        rev = row.get("revenue") or 0
        gp = row.get("grossProfit") or 0
        if rev > 0:
            gm_years.append((y, round(gp / rev * 100, 1)))

    if len(gm_years) >= 2:
        gm_first = gm_years[0][1]
        gm_last = gm_years[-1][1]
        delta = gm_last - gm_first

        if delta >= -2:
            gm_status, gm_note = "OK", f"Stable gross margin ({gm_last:.1f}%)"
        elif delta >= -5:
            gm_status, gm_note = "Warning", f"Gross margin declined {abs(delta):.1f}pp"
        else:
            gm_status, gm_note = (
                "Flag",
                f"Significant gross margin compression ({abs(delta):.1f}pp)",
            )

        trend_str = " → ".join(f"{v:.1f}%" for _, v in gm_years)
        results.append(
            {
                "metric": "Gross Margin Trend",
                "value": trend_str,
                "raw": gm_last,
                "status": gm_status,
                "note": gm_note,
            }
        )
    else:
        results.append(
            {
                "metric": "Gross Margin Trend",
                "value": "N/A",
                "raw": None,
                "status": "N/A",
                "note": "Insufficient data",
            }
        )

    # ── 4. Net Margin Trend ───────────────────────────────────────────────────
    nm_years = []
    for y in [base_year - 2, base_year - 1, base_year]:
        row = get_year(income, y)
        rev = row.get("revenue") or 0
        ni = row.get("netIncome") or 0
        if rev > 0:
            nm_years.append((y, round(ni / rev * 100, 1)))

    if len(nm_years) >= 2:
        nm_first = nm_years[0][1]
        nm_last = nm_years[-1][1]
        delta = nm_last - nm_first

        if delta >= -2:
            nm_status, nm_note = "OK", f"Stable net margin ({nm_last:.1f}%)"
        elif delta >= -5:
            nm_status, nm_note = "Warning", f"Net margin declined {abs(delta):.1f}pp"
        else:
            nm_status, nm_note = (
                "Flag",
                f"Significant net margin compression ({abs(delta):.1f}pp)",
            )

        trend_str = " → ".join(f"{v:.1f}%" for _, v in nm_years)
        results.append(
            {
                "metric": "Net Margin Trend",
                "value": trend_str,
                "raw": nm_last,
                "status": nm_status,
                "note": nm_note,
            }
        )
    else:
        results.append(
            {
                "metric": "Net Margin Trend",
                "value": "N/A",
                "raw": None,
                "status": "N/A",
                "note": "Insufficient data",
            }
        )

    # ── 5. Current Ratio ─────────────────────────────────────────────────────
    current_assets = bs.get("totalCurrentAssets") or 0
    current_liabilities = bs.get("totalCurrentLiabilities") or 0
    current_ratio = (
        round(current_assets / current_liabilities, 2)
        if current_liabilities > 0
        else None
    )

    results.append(
        {
            "metric": "Current Ratio",
            "value": f"{current_ratio:.2f}×" if current_ratio is not None else "N/A",
            "raw": current_ratio,
            "status": _status(current_ratio, 1.5, 1.0)
            if current_ratio is not None
            else "N/A",
            "note": (
                "Strong liquidity position"
                if current_ratio is not None and current_ratio >= 1.5
                else "Adequate liquidity"
                if current_ratio is not None and current_ratio >= 1.0
                else "Potential short-term liquidity risk"
                if current_ratio is not None
                else "Insufficient data"
            ),
        }
    )

    log.info(
        "[fundamentals][check_complete] ticker=%s year=%d checks=%d",
        ticker,
        base_year,
        len(results),
    )
    return results


if __name__ == "__main__":
    import json

    results = check_fundamentals("AAPL", 2024, base_year=2024)
    for r in results:
        print(f"[{r['status']:7}] {r['metric']:25} {r['value']:30} → {r['note']}")
