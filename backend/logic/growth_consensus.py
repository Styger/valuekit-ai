"""
3-Source Growth Rate Consensus

Sources:
  1. Own historical CAGR  — via cagr.get_cagr_for_screening()
  2. FMP analyst EPS estimates — forward average YoY EPS growth
  3. 25% cap applied to final result

Weighting when both sources available: own_cagr × 60% + analyst × 40%
"""

import logging
import sys
from pathlib import Path
from typing import Optional

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.api import fmp_api

log = logging.getLogger(__name__)

GROWTH_CAP = 0.25  # hard ceiling on any consensus result


def get_growth_consensus(ticker: str, year: int) -> dict:
    """
    Compute a blended growth rate from up to 2 sources.

    Returns:
        {
            "rate":    float,           # final rate (decimal, capped at 25%)
            "sources": {
                "own_cagr":          float | None,
                "analyst_estimate":  float | None,
            },
            "method":  str,  # "consensus" | "own_cagr_only" | "analyst_only" | "fallback"
            "capped":  bool, # True if 25% cap was applied
        }
    """
    own_cagr = _get_own_cagr(ticker, year)
    analyst  = _get_fmp_analyst_estimate(ticker, year)

    sources = {"own_cagr": own_cagr, "analyst_estimate": analyst}

    if own_cagr is not None and analyst is not None:
        raw    = own_cagr * 0.60 + analyst * 0.40
        method = "consensus"
    elif own_cagr is not None:
        raw    = own_cagr
        method = "own_cagr_only"
    elif analyst is not None:
        raw    = analyst
        method = "analyst_only"
    else:
        raw    = 0.10
        method = "fallback"

    capped = raw > GROWTH_CAP
    rate   = min(raw, GROWTH_CAP)

    log.info(
        "[growth_consensus] ticker=%s year=%d own_cagr=%s analyst=%s "
        "method=%s raw=%.4f capped=%s rate=%.4f",
        ticker, year,
        f"{own_cagr:.4f}" if own_cagr is not None else "None",
        f"{analyst:.4f}"  if analyst  is not None else "None",
        method, raw, capped, rate,
    )

    return {"rate": rate, "sources": sources, "method": method, "capped": capped}


# ── Source 1: own historical CAGR ────────────────────────────────────────────

def _get_own_cagr(ticker: str, year: int) -> Optional[float]:
    """
    5-year historical CAGR via cagr.get_cagr_for_screening().
    Returns decimal (e.g. 0.12) or None on failure.
    """
    try:
        from backend.logic.cagr import get_cagr_for_screening
        result = get_cagr_for_screening(ticker, period_years=5)
        if result is not None and result > 0:
            return float(result)
    except Exception as e:
        log.warning("[growth_consensus][own_cagr_error] ticker=%s error=%s", ticker, e)
    return None


# ── Source 2: FMP analyst EPS estimates ──────────────────────────────────────

def _get_fmp_analyst_estimate(ticker: str, year: int) -> Optional[float]:
    """
    Computes forward growth rate from FMP analyst EPS estimates.

    Takes all estimates with date > year, sorts ascending, and returns the
    average of all consecutive YoY EPS growth rates.

    Returns decimal growth rate or None if insufficient data.
    """
    try:
        data = fmp_api.get_analyst_estimates(ticker, limit=5)
        if not isinstance(data, list) or not data:
            return None

        # Keep only estimates for years strictly after `year`
        future = []
        for entry in data:
            date_str = entry.get("date", "")
            if not date_str:
                continue
            try:
                est_year = int(date_str[:4])
            except ValueError:
                continue
            eps = entry.get("estimatedEpsAvg")
            if eps is not None and eps > 0 and est_year > year:
                future.append((est_year, float(eps)))

        # Need at least 2 data points to compute a growth rate
        future.sort(key=lambda x: x[0])
        if len(future) < 2:
            return None

        # Average of consecutive YoY growth rates
        yoy_rates = []
        for i in range(1, len(future)):
            prev_year, prev_eps = future[i - 1]
            curr_year, curr_eps = future[i]
            n_years = curr_year - prev_year
            if n_years > 0 and prev_eps > 0:
                yoy = (curr_eps / prev_eps) ** (1 / n_years) - 1
                yoy_rates.append(yoy)

        if not yoy_rates:
            return None

        result = sum(yoy_rates) / len(yoy_rates)
        log.debug(
            "[growth_consensus][analyst_rates] ticker=%s yoy_rates=%s avg=%.4f",
            ticker, [f"{r:.4f}" for r in yoy_rates], result,
        )
        return result

    except Exception as e:
        log.warning(
            "[growth_consensus][analyst_error] ticker=%s error=%s", ticker, e
        )
        return None
