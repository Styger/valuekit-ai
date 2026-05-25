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
                   (Debt/Equity, Debt/EBITDA, ROIC).
        base_year: Explicit anchor for 3-year trend windows.
                   Trend loops use base_year-2 → base_year-1 → base_year.

    Checks:
      1. Debt/EBITDA        — Flag if > 5.0, Warning if > 3.0            (actual_year)
      2. FCF Trend (3Y)     — OK if positive & growing, else Warning/Flag (base_year)
      3. Gross Margin Trend — OK if stable/improving, Warning -5pp, Flag -10pp (base_year)
      4. Net Margin Trend   — OK if stable/improving, Warning -3pp, Flag -7pp  (base_year)
      5. ROIC               — Flag if < 10%, Warning if < 15%, OK if >= 15%    (actual_year)

    Returns:
        List of dicts with keys: metric, value, status, note, raw
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

    total_debt = bs.get("totalDebt") or 0
    equity = bs.get("totalStockholdersEquity") or bs.get("totalEquity") or 0

    # ── 1. Debt / EBITDA ─────────────────────────────────────────────────────
    ebitda = is_.get("ebitda")
    if not ebitda:
        op_income = is_.get("operatingIncome") or 0
        da = cf.get("depreciationAndAmortization") or 0
        ebitda = op_income + da if (op_income or da) else None
        ebitda_source = "operatingIncome + D&A"
    else:
        ebitda_source = "income statement"

    if ebitda and ebitda > 0:
        debt_ebitda = round(total_debt / ebitda, 2) if total_debt is not None else None
    else:
        debt_ebitda = None

    log.debug(
        "[fundamentals][debt_ebitda] ticker=%s actual_year=%d "
        "total_debt=%s ebitda=%s source=%s ratio=%s",
        ticker, actual_year, total_debt, ebitda, ebitda_source, debt_ebitda,
    )

    results.append(
        {
            "metric": "Debt / EBITDA",
            "value": f"{debt_ebitda:.2f}×" if debt_ebitda is not None else "N/A",
            "raw": debt_ebitda,
            "status": _status(debt_ebitda, 3.0, 5.0, lower_is_better=True)
            if debt_ebitda is not None
            else "N/A",
            "note": (
                "High debt relative to earnings — elevated repayment risk"
                if debt_ebitda is not None and debt_ebitda > 5.0
                else "Moderate leverage relative to earnings"
                if debt_ebitda is not None and debt_ebitda > 3.0
                else "Manageable debt load relative to earnings"
                if debt_ebitda is not None
                else "Insufficient data"
            ),
        }
    )

    # ── 3. FCF Trend (3 years) — anchored to base_year ───────────────────────
    fcf_years = []
    for y in [base_year - 2, base_year - 1, base_year]:
        row = get_year(cashflow, y)
        fcf = row.get("freeCashFlow")
        if fcf is not None:
            fcf_years.append((y, round(fcf / 1_000_000, 0)))

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

    # ── 4. Gross Margin Trend — anchored to base_year ────────────────────────
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

    # ── 5. Net Margin Trend — anchored to base_year ──────────────────────────
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

    # ── 6. ROIC — point-in-time (actual_year) ────────────────────────────────
    net_income = is_.get("netIncome") or 0
    invested_capital = (equity or 0) + (total_debt or 0)
    roic = round(net_income / invested_capital, 4) if invested_capital > 0 else None

    log.debug(
        "[fundamentals][roic] ticker=%s actual_year=%d "
        "net_income=%s equity=%s total_debt=%s roic=%s",
        ticker, actual_year, net_income, equity, total_debt, roic,
    )

    results.append(
        {
            "metric": "ROIC",
            "value": f"{roic * 100:.1f}%" if roic is not None else "N/A",
            "raw": roic,
            "status": _status(roic, 0.15, 0.10)
            if roic is not None
            else "N/A",
            "note": (
                "Strong capital efficiency"
                if roic is not None and roic >= 0.15
                else "Moderate capital efficiency"
                if roic is not None and roic >= 0.10
                else "Weak capital returns — below cost-of-capital threshold"
                if roic is not None
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
