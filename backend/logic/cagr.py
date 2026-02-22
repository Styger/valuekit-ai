import logging
from typing import Dict, List
import sys
from pathlib import Path
import logging

log = logging.getLogger(__name__)

# Stelle sicher, dass das Root-Verzeichnis im Python-Path ist
root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))

from backend.api import fmp_api

import pandas as pd

import logging

logging.basicConfig(
    level=logging.DEBUG,
    format="[%(asctime)s][%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)


def _calculate_cagr(start, end, years):
    """
    Calculates the Compound Annual Growth Rate (CAGR) between two values over a specified time period.

    CAGR = (End Value / Start Value)^(1/years) - 1

    Args:
        start (float): Initial value
        end (float): Final value
        years (float): Number of years between start and end value

    Returns:
        float: CAGR as a decimal (e.g., 0.15 for 15% growth)
               Returns 0 if input values are invalid (negative, zero, or non-numeric)
    """
    logging.debug(f"calculate_cagr(start={start}, end={end}, years={years})")
    try:
        start = float(start)
        end = float(end)
        years = float(years)
    except Exception as e:
        logging.warning(f"Conversion failed: {e}")
        return 0

    if start <= 0 or end <= 0 or years <= 0:
        logging.warning("Invalid input for CAGR calculation. Returning 0.")
        return 0

    return (end / start) ** (1 / years) - 1


def _auto_detect_year_column(df: pd.DataFrame) -> str:
    """Findet die richtige Jahres-Spalte im DataFrame."""
    for col in df.columns:
        if col.strip().lower() == "year":
            return col
    raise ValueError("Keine gültige 'year'-Spalte gefunden.")


def _mos_growth_estimate_auto(
    data_dict: Dict[str, List[float]],
    start_year: int,
    end_year: int,
    period_years: int = 5,
    known_start_year: int = None,
    include_book: bool = True,
    include_eps: bool = True,
    include_revenue: bool = True,
    include_cashflow: bool = True,
    include_fcf: bool = True,
) -> Dict[str, float]:
    """
    Calculates CAGR metrics from start_year to end_year, inferring the base year from data or using known_start_year.

    Args:
        data_dict: Dictionary with metric names as keys and lists of values
        start_year: Starting year for CAGR calculation
        end_year: Ending year for CAGR calculation
        period_years: Number of years in the period
        known_start_year: Optional known start year of the data
        include_book: Whether to include book value in calculation
        include_eps: Whether to include EPS in calculation
        include_revenue: Whether to include revenue in calculation
        include_cashflow: Whether to include cashflow in calculation
        include_fcf: Whether to include free cash flow per share in calculation
    """
    metrics = [v for v in data_dict.values() if isinstance(v, list)]
    if not metrics:
        raise ValueError("No valid metric data found.")

    num_years = len(metrics[0])

    if known_start_year:
        earliest_possible_year = known_start_year
    else:
        earliest_possible_year = end_year - (num_years - 1)

    start_index = start_year - earliest_possible_year
    end_index = end_year - earliest_possible_year

    if start_index < 0 or end_index >= num_years:
        raise ValueError(
            f"Insufficient data range to compute CAGR (need years {start_year}-{end_year}, but data only covers {earliest_possible_year}-{earliest_possible_year + num_years - 1})"
        )

    # Map für Boolean-Flags
    include_flags = {
        "book": include_book,
        "eps": include_eps,
        "revenue": include_revenue,
        "cashflow": include_cashflow,
        "fcf": include_fcf,
    }

    details = {}
    growths = []

    for key, values in data_dict.items():
        # Überspringe Metriken, die nicht inkludiert werden sollen
        if key in include_flags and not include_flags[key]:
            continue

        if len(values) <= end_index:
            details[key] = 0
            continue

        start = values[start_index]
        end = values[end_index]

        if start > 0 and end > 0:
            cagr = _calculate_cagr(start, end, period_years)
            growths.append(cagr)
            details[key] = round(cagr * 100, 2)
        else:
            details[key] = 0
            growths.append(0)

    avg_growth = sum(growths) / len(growths) if growths else 0
    details["avg"] = round(avg_growth * 100, 2)

    return details


def run_analysis(
    ticker: str,
    start_year: int,
    end_year: int,
    period_years: int = 10,
    include_book: bool = True,
    include_eps: bool = True,
    include_revenue: bool = True,
    include_cashflow: bool = True,
    include_fcf: bool = True,
):
    """
    Führt die CAGR-Analyse für einen gegebenen Ticker durch.
    Gibt eine Tabelle mit jährlichen CAGR-Werten aus.

    Args:
        ticker: Stock ticker symbol
        start_year: Starting year for analysis
        end_year: Ending year for analysis
        period_years: Period length for CAGR calculation
        include_book: Include book value per share in calculation
        include_eps: Include EPS in calculation
        include_revenue: Include revenue per share in calculation
        include_cashflow: Include cashflow per share in calculation
        include_fcf: Include free cash flow per share in calculation
    """
    required_years = end_year - start_year
    data, mos_input = fmp_api.get_year_data_by_range(
        ticker, start_year, years=required_years
    )
    df = pd.DataFrame(data)

    year_col = _auto_detect_year_column(df)
    year_range = sorted(df[year_col].astype(int).tolist())
    earliest_year = min(year_range)
    latest_year = max(year_range)

    # Zeige an, welche Metriken aktiv sind
    active_metrics = []
    if include_book:
        active_metrics.append("book")
    if include_eps:
        active_metrics.append("eps")
    if include_revenue:
        active_metrics.append("revenue")
    if include_cashflow:
        active_metrics.append("cashflow")
    if include_fcf:
        active_metrics.append("fcf")

    log.info(f"\n==== {ticker.upper()} Yearly CAGR ({period_years}y periods) ====")
    log.info(f"Active metrics: {', '.join(active_metrics)}\n")

    results_list = []

    for start in range(earliest_year, latest_year - period_years + 1):
        end = start + period_years
        try:
            result = _mos_growth_estimate_auto(
                data_dict=mos_input,
                start_year=start,
                end_year=end,
                period_years=period_years,
                known_start_year=earliest_year,
                include_book=include_book,
                include_eps=include_eps,
                include_revenue=include_revenue,
                include_cashflow=include_cashflow,
                include_fcf=include_fcf,
            )
            result["from"] = start
            result["to"] = end
            results_list.append(result)
        except ValueError as e:
            log.warning(f"Skipping {start}-{end}: {e}")

    if results_list:
        result_df = pd.DataFrame(results_list)

        # Dynamische Spaltenauswahl basierend auf Boolean-Flags
        cols = ["from", "to"]
        if include_book:
            cols.append("book")
        if include_eps:
            cols.append("eps")
        if include_revenue:
            cols.append("revenue")
        if include_cashflow:
            cols.append("cashflow")
        if include_fcf:
            cols.append("fcf")
        cols.append("avg")

        result_df = result_df[cols]
        log.info("\n%s", result_df.to_string(index=False))
    else:
        log.warning("Keine gültigen CAGR-Zeiträume gefunden.")


def get_cagr_for_screening(ticker: str, period_years: int = 5) -> float:
    """
    Get average CAGR for screening purposes with automatic fallback

    Uses the most recent COMPLETE data available. If data for the most recent
    year is not available, it automatically falls back to earlier years.

    Example:
    - Try 2019-2024 (5 years)
    - If no data → Try 2018-2023 (5 years)
    - If no data → Try 2017-2022 (5 years)
    - etc.

    Args:
        ticker: Stock ticker symbol
        period_years: Period for CAGR calculation (default 5 years)

    Returns:
        float: Average CAGR as decimal (e.g., 0.15 for 15%)
               Returns 0.10 (10%) as default if all attempts fail
    """
    try:
        from datetime import datetime

        current_year = datetime.now().year

        # ✅ Try multiple end years with fallback
        # Start with most recent possible year and go backwards
        max_attempts = 5  # Try up to 5 different year ranges

        for attempt in range(max_attempts):
            # Calculate end_year for this attempt
            # Attempt 0: current_year - 2 (e.g., 2026 - 2 = 2024)
            # Attempt 1: current_year - 3 (e.g., 2026 - 3 = 2023)
            # Attempt 2: current_year - 4 (e.g., 2026 - 4 = 2022)
            # etc.
            end_year = current_year - 2 - attempt
            start_year = end_year - period_years

            logging.info(
                f"CAGR attempt {attempt + 1}/{max_attempts} for {ticker}: {start_year}-{end_year}"
            )

            try:
                # Get data
                data, mos_input = fmp_api.get_year_data_by_range(
                    ticker, start_year, years=period_years
                )

                if not mos_input or not data:
                    logging.warning(
                        f"No data for {ticker} ({start_year}-{end_year}), trying earlier period..."
                    )
                    continue

                # Calculate CAGR using all available metrics
                result = _mos_growth_estimate_auto(
                    data_dict=mos_input,
                    start_year=start_year,
                    end_year=end_year,
                    period_years=period_years,
                    known_start_year=start_year,
                    include_book=True,
                    include_eps=True,
                    include_revenue=True,
                    include_cashflow=True,
                    include_fcf=True,
                )

                # Return average CAGR as decimal
                avg_cagr_pct = result.get("avg", 0.0)

                # ✅ Check if result is valid (not 0)
                if avg_cagr_pct != 0.0:
                    logging.info(
                        f"✅ CAGR found for {ticker} ({start_year}-{end_year}): {avg_cagr_pct:.2f}%"
                    )
                    return avg_cagr_pct / 100.0  # Convert percentage to decimal
                else:
                    logging.warning(
                        f"CAGR is 0% for {ticker} ({start_year}-{end_year}), trying earlier period..."
                    )
                    continue

            except ValueError as e:
                # Insufficient data range for this period
                logging.warning(
                    f"ValueError for {ticker} ({start_year}-{end_year}): {e}"
                )
                continue
            except Exception as e:
                # Other errors - log and try next period
                logging.warning(f"Error for {ticker} ({start_year}-{end_year}): {e}")
                continue

        # ✅ All attempts failed - return default
        logging.warning(f"All CAGR attempts failed for {ticker}, using default 10%")
        return 0.10  # Default 10% growth

    except Exception as e:
        logging.error(f"CAGR calculation completely failed for {ticker}: {e}")
        return 0.10  # Default 10% growth


def compute_cagr(start_value: float, end_value: float, years: int) -> float:
    if start_value <= 0 or end_value <= 0 or years <= 0:
        raise ValueError("All inputs must be positive.")
    return (end_value / start_value) ** (1 / years) - 1


def _main():
    # all metrics (default)
    ticker = "aapl"
    start_year = 2010
    end_year = 2024
    period_years = 5

    log.info("\n" + "=" * 60)
    log.info("all metrics (default - including FCF)")
    log.info("=" * 60)
    run_analysis(
        ticker=ticker,
        start_year=start_year,
        end_year=end_year,
        period_years=period_years,
        include_book=True,
        include_eps=True,
        include_revenue=True,
        include_cashflow=True,
        include_fcf=True,
    )


if __name__ == "__main__":
    _main()
