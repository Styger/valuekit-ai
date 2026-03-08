from typing import Dict, List, Optional
import sys
from pathlib import Path
import logging

log = logging.getLogger(__name__)

# Stelle sicher, dass das Root-Verzeichnis im Python-Path ist
root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))

from backend.api import fmp_api


def calculate_profitability_metrics_from_ticker(ticker: str, year: int) -> Dict:
    """
    Calculates profitability metrics for a company for a single year.
    Works for all stocks worldwide (not limited to US stocks).

    Args:
        ticker: Stock symbol
        year: Year for analysis

    Returns:
        Dict with profitability metrics (pure numbers, no text/ratings)
    """
    # Fetch Balance Sheet, Income Statement and Key Metrics
    balance_sheet = fmp_api.get_balance_sheet(ticker, limit=20)
    income_statement = fmp_api.get_income_statement(ticker, limit=20)
    key_metrics_data = fmp_api.get_key_metrics(ticker, limit=20)

    # Find data for the specified year
    def get_by_year(data, target_year):
        for entry in data:
            if str(entry.get("calendarYear")) == str(target_year):
                return entry
        return {}

    bs_data = get_by_year(balance_sheet, year)
    is_data = get_by_year(income_statement, year)
    km_data = get_by_year(key_metrics_data, year)

    # Extract Balance Sheet values
    total_assets = bs_data.get("totalAssets", 0)
    shareholders_equity = bs_data.get("totalStockholdersEquity", 0) or bs_data.get(
        "totalEquity", 0
    )
    total_debt = bs_data.get("totalDebt", 0)
    cash_and_equivalents = bs_data.get("cashAndCashEquivalents", 0) or bs_data.get(
        "cashAndShortTermInvestments", 0
    )

    # Extract Income Statement values
    revenue = is_data.get("revenue", 0)
    gross_profit = is_data.get("grossProfit", 0)
    operating_income = is_data.get("operatingIncome", 0)
    net_income = is_data.get("netIncome", 0)
    income_tax_expense = is_data.get("incomeTaxExpense", 0)
    income_before_tax = is_data.get("incomeBeforeTax", 0)

    # Calculate Return Ratios
    roe = net_income / shareholders_equity if shareholders_equity > 0 else None
    roa = net_income / total_assets if total_assets > 0 else None

    # Calculate ROIC
    tax_rate = (
        abs(income_tax_expense / income_before_tax) if income_before_tax != 0 else 0.25
    )
    nopat = operating_income * (1 - tax_rate)
    invested_capital = total_debt + shareholders_equity - cash_and_equivalents
    roic = nopat / invested_capital if invested_capital > 0 else None

    # Calculate Margins (as decimals, will be converted to % in GUI)
    gross_margin = gross_profit / revenue if revenue > 0 else None
    operating_margin = operating_income / revenue if revenue > 0 else None
    net_margin = net_income / revenue if revenue > 0 else None

    # Calculate Efficiency
    asset_turnover = revenue / total_assets if total_assets > 0 else None

    # FCF Yield from FMP key metrics (FCF / Market Cap, as decimal)
    fcf_yield = km_data.get("fcfYield")

    # Return pure data
    result = {
        "ticker": ticker.upper(),
        "year": year,
        "revenue": revenue,
        "gross_profit": gross_profit,
        "operating_income": operating_income,
        "net_income": net_income,
        "total_assets": total_assets,
        "shareholders_equity": shareholders_equity,
        "roe": roe,
        "roa": roa,
        "roic": roic,
        "gross_margin": gross_margin,
        "operating_margin": operating_margin,
        "net_margin": net_margin,
        "asset_turnover": asset_turnover,
        "fcf_yield": fcf_yield,
    }

    log.info(f"Profitability Analysis Result: {result}")
    return result


def calculate_profitability_metrics_multi_year(
    ticker: str, start_year: int, end_year: int
) -> List[Dict]:
    """
    Calculates profitability metrics for a company across multiple years.

    Args:
        ticker: Stock symbol
        start_year: Starting year
        end_year: Ending year

    Returns:
        List of dicts with profitability metrics for each year
    """
    # Fetch all data once
    balance_sheet = fmp_api.get_balance_sheet(ticker, limit=20)
    income_statement = fmp_api.get_income_statement(ticker, limit=20)

    def get_by_year(data, target_year):
        for entry in data:
            if str(entry.get("calendarYear")) == str(target_year):
                return entry
        return {}

    results = []

    for year in range(start_year, end_year + 1):
        bs_data = get_by_year(balance_sheet, year)
        is_data = get_by_year(income_statement, year)

        # Extract Balance Sheet values
        total_assets = bs_data.get("totalAssets", 0)
        shareholders_equity = bs_data.get("totalStockholdersEquity", 0) or bs_data.get(
            "totalEquity", 0
        )
        total_debt = bs_data.get("totalDebt", 0)
        cash_and_equivalents = bs_data.get("cashAndCashEquivalents", 0) or bs_data.get(
            "cashAndShortTermInvestments", 0
        )

        # Extract Income Statement values
        revenue = is_data.get("revenue", 0)
        gross_profit = is_data.get("grossProfit", 0)
        operating_income = is_data.get("operatingIncome", 0)
        net_income = is_data.get("netIncome", 0)
        income_tax_expense = is_data.get("incomeTaxExpense", 0)
        income_before_tax = is_data.get("incomeBeforeTax", 0)

        # Calculate Return Ratios
        roe = net_income / shareholders_equity if shareholders_equity > 0 else None
        roa = net_income / total_assets if total_assets > 0 else None

        # Calculate ROIC
        tax_rate = (
            abs(income_tax_expense / income_before_tax)
            if income_before_tax != 0
            else 0.25
        )
        nopat = operating_income * (1 - tax_rate)
        invested_capital = total_debt + shareholders_equity - cash_and_equivalents
        roic = nopat / invested_capital if invested_capital > 0 else None

        # Calculate Margins
        gross_margin = gross_profit / revenue if revenue > 0 else None
        operating_margin = operating_income / revenue if revenue > 0 else None
        net_margin = net_income / revenue if revenue > 0 else None

        # Calculate Efficiency
        asset_turnover = revenue / total_assets if total_assets > 0 else None

        results.append(
            {
                "ticker": ticker.upper(),
                "year": year,
                "revenue": revenue,
                "gross_profit": gross_profit,
                "operating_income": operating_income,
                "net_income": net_income,
                "total_assets": total_assets,
                "shareholders_equity": shareholders_equity,
                "roe": roe,
                "roa": roa,
                "roic": roic,
                "gross_margin": gross_margin,
                "operating_margin": operating_margin,
                "net_margin": net_margin,
                "asset_turnover": asset_turnover,
            }
        )

    log.info(
        f"Profitability Analysis for {ticker} from {start_year} to {end_year}: {len(results)} years"
    )
    return results


if __name__ == "__main__":
    ticker = "AAPL"
    year = 2024

    log.info("\n=== Single Year Profitability Analysis ===")
    result = calculate_profitability_metrics_from_ticker(ticker, year)

    log.info(f"\nProfitability Metrics for {ticker} ({year}):")
    log.info(f"ROE: {result['roe'] * 100:.2f}%" if result["roe"] else "ROE: N/A")
    log.info(f"ROA: {result['roa'] * 100:.2f}%" if result["roa"] else "ROA: N/A")
    log.info(f"ROIC: {result['roic'] * 100:.2f}%" if result["roic"] else "ROIC: N/A")
    log.info(
        f"Gross Margin: {result['gross_margin'] * 100:.2f}%"
        if result["gross_margin"]
        else "Gross Margin: N/A"
    )
    log.info(
        f"Operating Margin: {result['operating_margin'] * 100:.2f}%"
        if result["operating_margin"]
        else "Operating Margin: N/A"
    )
    log.info(
        f"Net Margin: {result['net_margin'] * 100:.2f}%"
        if result["net_margin"]
        else "Net Margin: N/A"
    )
    log.info(
        f"Asset Turnover: {result['asset_turnover']:.2f}x"
        if result["asset_turnover"]
        else "Asset Turnover: N/A"
    )

    log.info("\n=== Multi-Year Profitability Analysis ===")
    results = calculate_profitability_metrics_multi_year(ticker, 2022, 2024)

    log.info(
        f"\n{'Year':<6} {'ROE':<8} {'ROA':<8} {'ROIC':<8} {'Gross M.':<10} {'Op. M.':<10} {'Net M.':<8}"
    )
    log.info("-" * 70)
    for r in results:
        roe_str = f"{r['roe'] * 100:.1f}%" if r["roe"] else "N/A"
        roa_str = f"{r['roa'] * 100:.1f}%" if r["roa"] else "N/A"
        roic_str = f"{r['roic'] * 100:.1f}%" if r["roic"] else "N/A"
        gm_str = f"{r['gross_margin'] * 100:.1f}%" if r["gross_margin"] else "N/A"
        om_str = (
            f"{r['operating_margin'] * 100:.1f}%" if r["operating_margin"] else "N/A"
        )
        nm_str = f"{r['net_margin'] * 100:.1f}%" if r["net_margin"] else "N/A"
        log.info(
            f"{r['year']:<6} {roe_str:<8} {roa_str:<8} {roic_str:<8} {gm_str:<10} {om_str:<10} {nm_str:<8}"
        )
