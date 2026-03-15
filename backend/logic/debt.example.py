from typing import Dict, List, Optional
import sys
from pathlib import Path

# Stelle sicher, dass das Root-Verzeichnis im Python-Path ist
root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))

from backend.api import fmp_api


def calculate_debt_metrics_from_ticker(
    ticker: str,
    year: int,
    use_total_debt: bool = False,
    metric_type: str = "net_income",  # "net_income", "ebitda", or "operating_cash_flow"
) -> Dict:
    """
    Calculates debt metrics for a company for a single year.

    Args:
        ticker: Stock symbol
        year: Year for analysis
        use_total_debt: If True, use total debt; if False, use long-term debt only
        metric_type: Which metric to use for ratio calculation
                    - "net_income": Debt / Net Income
                    - "ebitda": Debt / EBITDA
                    - "operating_cash_flow": Debt / Operating Cash Flow

    Returns:
        Dict with debt metrics (pure numbers, no text/ratings)
    """
    # Fetch Balance Sheet, Income Statement, and Cashflow
    balance_sheet = fmp_api.get_balance_sheet(ticker, limit=20)
    income_statement = fmp_api.get_income_statement(ticker, limit=20)
    cashflow_statement = fmp_api.get_cashflow_statement(ticker, limit=20)

    # Use utility function to find latest common year
    year_info = fmp_api.get_latest_common_year(
        year,
        balance_sheet,
        income_statement,
        cashflow_statement,
        ticker=ticker,
        show_warning=True,
    )

    actual_year = year_info["year"]

    # Find data for the actual year (with fallback)
    def get_by_year(data, target_year):
        for entry in data:
            if str(entry.get("calendarYear")) == str(target_year):
                return entry
        return {}

    bs_data = get_by_year(balance_sheet, actual_year)
    is_data = get_by_year(income_statement, actual_year)
    cf_data = get_by_year(cashflow_statement, actual_year)

    # Extract debt values
    long_term_debt = bs_data.get("longTermDebt", 0)
    total_debt = bs_data.get("totalDebt", 0)
    debt_used = total_debt if use_total_debt else long_term_debt

    # Extract metric values based on metric_type
    net_income = is_data.get("netIncome", 0)

    # EBITDA calculation
    # FMP Income Statement has "ebitda" field directly
    ebitda = is_data.get("ebitda", None)
    ebitda_source = "direct"  # Track the source

    # If EBITDA not available directly, calculate it
    if ebitda is None or ebitda == 0:
        # EBITDA = Operating Income + Depreciation & Amortization
        operating_income = is_data.get("operatingIncome", 0)
        depreciation = cf_data.get("depreciationAndAmortization", 0)
        ebitda = operating_income + depreciation
        ebitda_source = "calculated"
        print(
            f"[CALCULATED] EBITDA: Operating Income (${operating_income / 1_000_000:,.2f}M) + Depreciation (${depreciation / 1_000_000:,.2f}M) = ${ebitda / 1_000_000:,.2f}M"
        )
    else:
        print(f"[DIRECT] EBITDA found in API: ${ebitda / 1_000_000:,.2f}M")

    # Operating Cash Flow
    operating_cash_flow = cf_data.get("operatingCashFlow", 0)

    # Select the metric to use for ratio calculation
    if metric_type == "ebitda":
        metric_value = ebitda
        metric_name = "ebitda"
    elif metric_type == "operating_cash_flow":
        metric_value = operating_cash_flow
        metric_name = "operating_cash_flow"
    else:  # default: net_income
        metric_value = net_income
        metric_name = "net_income"

    # Calculate ratio
    if metric_value <= 0:
        debt_ratio = None  # Cannot calculate with negative/zero metric
    else:
        debt_ratio = debt_used / metric_value

    # Return pure data
    result = {
        "ticker": ticker.upper(),
        "year": actual_year,
        "year_fallback": year_info["fallback"],
        "requested_year": year_info["requested_year"],
        "long_term_debt": long_term_debt,
        "total_debt": total_debt,
        "debt_used": debt_used,
        "use_total_debt": use_total_debt,
        "net_income": net_income,
        "ebitda": ebitda,
        "ebitda_source": ebitda_source,  # "direct" or "calculated"
        "operating_cash_flow": operating_cash_flow,
        "metric_type": metric_type,
        "metric_value": metric_value,
        "metric_name": metric_name,
        "debt_ratio": debt_ratio,
        # Keep legacy field name for backward compatibility
        "debt_to_income_ratio": debt_ratio,
    }

    print(f"Debt Analysis Result ({metric_type}): %s", result)
    return result


def calculate_debt_metrics_multi_year(
    ticker: str,
    start_year: int,
    end_year: int,
    use_total_debt: bool = False,
    metric_type: str = "net_income",
) -> List[Dict]:
    """
    Calculates debt metrics for a company across multiple years.

    Args:
        ticker: Stock symbol
        start_year: Starting year
        end_year: Ending year
        use_total_debt: If True, use total debt; if False, use long-term debt only
        metric_type: Which metric to use for ratio calculation

    Returns:
        List of dicts with debt metrics for each year
    """
    # Fetch all data once
    balance_sheet = fmp_api.get_balance_sheet(ticker, limit=20)
    income_statement = fmp_api.get_income_statement(ticker, limit=20)
    cashflow_statement = fmp_api.get_cashflow_statement(ticker, limit=20)

    def get_by_year(data, target_year):
        for entry in data:
            if str(entry.get("calendarYear")) == str(target_year):
                return entry
        return {}

    results = []

    for year in range(start_year, end_year + 1):
        # Use utility function to find latest common year for this iteration
        year_info = fmp_api.get_latest_common_year(
            year,
            balance_sheet,
            income_statement,
            cashflow_statement,
            ticker=ticker,
            show_warning=True,
        )

        actual_year = year_info["year"]

        bs_data = get_by_year(balance_sheet, actual_year)
        is_data = get_by_year(income_statement, actual_year)
        cf_data = get_by_year(cashflow_statement, actual_year)

        # Extract debt values
        long_term_debt = bs_data.get("longTermDebt", 0)
        total_debt = bs_data.get("totalDebt", 0)
        debt_used = total_debt if use_total_debt else long_term_debt

        # Extract metric values
        net_income = is_data.get("netIncome", 0)

        # EBITDA
        ebitda = is_data.get("ebitda", None)
        ebitda_source = "direct"
        if ebitda is None or ebitda == 0:
            operating_income = is_data.get("operatingIncome", 0)
            depreciation = cf_data.get("depreciationAndAmortization", 0)
            ebitda = operating_income + depreciation
            ebitda_source = "calculated"

        # Operating Cash Flow
        operating_cash_flow = cf_data.get("operatingCashFlow", 0)

        # Select metric
        if metric_type == "ebitda":
            metric_value = ebitda
            metric_name = "ebitda"
        elif metric_type == "operating_cash_flow":
            metric_value = operating_cash_flow
            metric_name = "operating_cash_flow"
        else:
            metric_value = net_income
            metric_name = "net_income"

        # Calculate ratio
        if metric_value <= 0:
            debt_ratio = None
        else:
            debt_ratio = debt_used / metric_value

        results.append(
            {
                "ticker": ticker.upper(),
                "year": actual_year,
                "year_fallback": year_info["fallback"],
                "requested_year": year_info["requested_year"],
                "long_term_debt": long_term_debt,
                "total_debt": total_debt,
                "debt_used": debt_used,
                "use_total_debt": use_total_debt,
                "net_income": net_income,
                "ebitda": ebitda,
                "ebitda_source": ebitda_source,  # "direct" or "calculated"
                "operating_cash_flow": operating_cash_flow,
                "metric_type": metric_type,
                "metric_value": metric_value,
                "metric_name": metric_name,
                "debt_ratio": debt_ratio,
                "debt_to_income_ratio": debt_ratio,  # Legacy
            }
        )

    print(
        f"Debt Analysis for {ticker} from {start_year} to {end_year}: {len(results)} years"
    )
    return results


if __name__ == "__main__":
    # Test all three metric types
    ticker = "AAPL"
    year = 2024

    print("\n=== Debt / Net Income ===")
    result = calculate_debt_metrics_from_ticker(
        ticker=ticker, year=year, use_total_debt=False, metric_type="net_income"
    )
    print(f"Ratio: {result['debt_ratio']:.2f}" if result["debt_ratio"] else "N/A")

    print("\n=== Debt / EBITDA ===")
    result = calculate_debt_metrics_from_ticker(
        ticker=ticker, year=year, use_total_debt=False, metric_type="ebitda"
    )
    print(
        f"EBITDA: ${result['ebitda'] / 1_000_000:,.2f}M (Source: {result['ebitda_source']})"
    )
    print(f"Ratio: {result['debt_ratio']:.2f}" if result["debt_ratio"] else "N/A")

    print("\n=== Debt / Operating Cash Flow ===")
    result = calculate_debt_metrics_from_ticker(
        ticker=ticker,
        year=year,
        use_total_debt=False,
        metric_type="operating_cash_flow",
    )
    print(f"Operating CF: ${result['operating_cash_flow'] / 1_000_000:,.2f}M")
    print(f"Ratio: {result['debt_ratio']:.2f}" if result["debt_ratio"] else "N/A")

    print("\n=== Multi-Year with EBITDA ===")
    results = calculate_debt_metrics_multi_year(
        ticker=ticker,
        start_year=2022,
        end_year=2024,
        use_total_debt=False,
        metric_type="ebitda",
    )
    for r in results:
        source_indicator = "[direct]" if r["ebitda_source"] == "direct" else "[calc]"
        print(
            f"{r['year']}: Debt/EBITDA = {r['debt_ratio']:.2f} {source_indicator}"
            if r["debt_ratio"]
            else f"{r['year']}: N/A"
        )
