from typing import List, Dict, Tuple, Optional
import sys
from pathlib import Path
import logging

log = logging.getLogger(__name__)

# Stelle sicher, dass das Root-Verzeichnis im Python-Path ist
root_dir = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(root_dir))

from backend.api import fmp_api


def _calculate_pbt_price(
    fcf: float, growth_rate: float, return_full_table: bool = False
) -> Tuple[float, float, Optional[List[Dict]]]:
    """
    Berechnet Kaufpreis (8-Jahres-Payback) und fairen Wert (Doppelt).
    """
    years = 8
    total = 0.0
    table = []

    for year in range(years + 1):
        if year == 0:
            income = fcf
        else:
            income = fcf * ((1 + growth_rate) ** year)

        if year > 0:
            total += income

        row = {
            "Jahr": year,
            "Einnahme": round(income, 2),
            "Summe_Cashflows": round(total, 2),
        }
        table.append(row)

    buy_price = round(table[8]["Summe_Cashflows"], 2)
    fair_value = round(buy_price * 2, 2)

    if return_full_table:
        return buy_price, fair_value, table
    return buy_price, fair_value, None


def _get_pbt_result(ticker: str, year: int, growth_rate: float) -> Optional[dict]:
    """
    Holt PBT-Daten für ein bestimmtes Jahr - ähnlich wie _get_ten_cap_result
    """
    try:
        # FCF pro Aktie holen mit Fallback
        key_metrics = fmp_api.get_key_metrics(ticker, limit=20)

        # First try requested year, then fall back if FCF not available
        fcf = None
        # Use utility function to find latest common year
        year_info = fmp_api.get_latest_common_year(
            year, key_metrics, ticker=ticker, show_warning=True
        )

        actual_year = year_info["year"]

        # Try to find FCF for requested year
        for entry in key_metrics:
            if str(entry.get("calendarYear")) == str(year):
                fcf = entry.get("freeCashFlowPerShare")
                if fcf and fcf > 0:  # Check FCF is valid
                    break
                else:
                    fcf = None  # Year exists but FCF is missing

        # If FCF not found for requested year, use fallback logic
        if fcf is None:
            # Find years with valid FCF data
            years_with_fcf = []
            for entry in key_metrics:
                year_val = entry.get("calendarYear")
                fcf_val = entry.get("freeCashFlowPerShare")
                if year_val and fcf_val and fcf_val > 0:
                    years_with_fcf.append(int(year_val))

            if not years_with_fcf:
                log.info(f"Kein gültiges FCF für {ticker} gefunden.")
                return None

            # Use latest year with FCF
            actual_year = max(years_with_fcf)

            if actual_year != year:
                log.info(
                    f"  ℹ️  Year {year} FCF not available for {ticker}, using {actual_year}"
                )

            # Get FCF for fallback year
            for entry in key_metrics:
                if str(entry.get("calendarYear")) == str(actual_year):
                    fcf = entry.get("freeCashFlowPerShare")
                    break

        if fcf is None or fcf <= 0:
            log.info(f"Kein gültiges FCF pro Aktie für {ticker} gefunden.")
            return None

        # Buy Price und Fair Value berechnen
        buy_price, fair_value, _ = _calculate_pbt_price(fcf, growth_rate, False)

        # Aktuellen Aktienkurs holen
        current_price = None
        price_comparison = "N/A"
        percentage_diff_fair = 0
        percentage_diff_buy = 0

        try:
            current_price = fmp_api.get_current_price(ticker)

            if current_price is not None and fair_value > 0:
                # Vergleich mit Fair Value
                percentage_diff_fair = ((current_price - fair_value) / fair_value) * 100

                # Vergleich mit Buy Price
                percentage_diff_buy = ((current_price - buy_price) / buy_price) * 100

                if current_price <= buy_price:
                    price_comparison = (
                        f"Below buy price by {abs(percentage_diff_buy):.1f}%"
                    )
                elif current_price <= fair_value:
                    price_comparison = (
                        f"Below fair value by {abs(percentage_diff_fair):.1f}%"
                    )
                else:
                    price_comparison = f"Overvalued by {abs(percentage_diff_fair):.1f}%"

        except Exception as e:
            log.info(f"Could not fetch current price for {ticker}: {e}")

        # Investment Recommendation
        investment_recommendation = _get_investment_recommendation(
            current_price, fair_value, buy_price
        )

        return {
            "ticker": ticker,
            "year": actual_year,
            "year_fallback": year_info["fallback"],
            "requested_year": year_info["requested_year"],
            "fcf_per_share": fcf,
            "growth_rate": growth_rate,
            "buy_price": buy_price,
            "fair_value": fair_value,
            "current_stock_price": current_price,
            "price_comparison": price_comparison,
            "percentage_diff_fair": percentage_diff_fair,
            "percentage_diff_buy": percentage_diff_buy,
            "investment_recommendation": investment_recommendation,
        }

    except Exception as e:
        log.error(f"Error in _get_pbt_result: {e}")
        return None


def _get_investment_recommendation(
    current_price: float, fair_value: float, buy_price: float
) -> str:
    """
    Gibt eine Investitionsempfehlung basierend auf den Preisvergleichen.
    """
    if current_price is None or current_price <= 0:
        return "No price data available"

    if current_price <= buy_price:
        return "Strong Buy (At or below payback price)"
    elif current_price <= fair_value:
        return "Buy (Below fair value)"
    elif current_price <= fair_value * 1.1:
        return "Hold (Near fair value)"
    else:
        return "Avoid (Overvalued)"


def calculate_pbt_from_ticker(
    ticker: str,
    year: int,
    growth_estimate: float,
    return_full_table: bool = False,
) -> Tuple[float, float, Optional[List[Dict]], Dict]:
    """
    Legacy-Funktion für Kompatibilität - verwendet _get_pbt_result
    """
    result = _get_pbt_result(ticker, year, growth_estimate)

    if not result:
        raise ValueError(f"Could not calculate PBT for {ticker} in {year}")

    # Tabelle nur wenn explizit angefordert
    table = None
    if return_full_table:
        key_metrics = fmp_api.get_key_metrics(ticker, limit=20)
        fcf = None
        for entry in key_metrics:
            if str(entry.get("calendarYear")) == str(year):
                fcf = entry.get("freeCashFlowPerShare")
                break

        if fcf:
            _, _, table = _calculate_pbt_price(fcf, growth_estimate, True)

    # Legacy price_info Format
    price_info = {
        "Current Stock Price": result["current_stock_price"] or 0.0,
        "Buy Price (8Y Payback)": result["buy_price"],
        "Fair Value (2x Payback)": result["fair_value"],
        "Price Comparison": result["price_comparison"],
        "% vs Buy Price": result["percentage_diff_buy"],
        "% vs Fair Value": result["percentage_diff_fair"],
        "FCF per Share": result["fcf_per_share"],
        "Investment Recommendation": result["investment_recommendation"],
    }

    return result["buy_price"], result["fair_value"], table, price_info


def calculate_pbt_with_comparison(
    ticker: str, year: int, growth_rate: float
) -> Optional[dict]:
    """
    Neue Funktion analog zu calculate_ten_cap_with_comparison
    """
    return _get_pbt_result(ticker, year, growth_rate)


default_language = {
    "pbt_calc_title": "PBT Analyse fürr",
    "pbt_year": "Jahr",
    "pbt_income": "Einnahme",
    "pbt_cumulative": "Kumuliert",
    "pbt_fcf_per_share": "FCF pro Aktie:",
    "pbt_growth_rate": "Wachstumsrate:",
    "pbt_buy_price": "Buy Price (8Y Payback):",
    "pbt_fair_value": "Fair Value (2x):",
    "current_stock_price": "Current Stock Price:",
    "price_comparison": "Price vs. Fair Value:",
}


def _format_pbt_report(data: dict, table: List[Dict], language: dict) -> str:
    """
    Formatiert einen detaillierten PBT Report mit Cashflow-Tabelle
    """
    report = []
    report.append(
        f"\n{language['pbt_calc_title']} {data['ticker'].upper()} ({data['year']})"
    )
    report.append("=" * 60)
    report.append(
        f"{language['pbt_fcf_per_share']:25}  ${data['fcf_per_share']:>10,.2f}"
    )
    report.append(
        f"{language['pbt_growth_rate']:25}  {data['growth_rate'] * 100:>10.1f}%"
    )
    report.append("-" * 60)

    # Cashflow Tabelle
    report.append(f"\n{'Jahr':>6} | {'Einnahme':>15} | {'Kumuliert':>15}")
    report.append("-" * 60)

    for row in table:
        year = row["Jahr"]
        income = row["Einnahme"]
        cumulative = row["Summe_Cashflows"]

        marker = ""
        if year == 8:
            marker = " ← Buy Price"

        report.append(f"{year:>6} | ${income:>14,.2f} | ${cumulative:>14,.2f}{marker}")

    report.append("=" * 60)
    report.append(f"{language['pbt_buy_price']:25}  ${data['buy_price']:>10,.2f}")
    report.append(f"{language['pbt_fair_value']:25}  ${data['fair_value']:>10,.2f}")

    # Current Price und Vergleich hinzufügen
    if data.get("current_stock_price") is not None:
        report.append(
            f"{language['current_stock_price']:25}  ${data['current_stock_price']:>10,.2f}"
        )
        report.append(
            f"{language['price_comparison']:25} {data['price_comparison']:>15}"
        )

    return "\n".join(report)


def print_pbt_analysis(
    ticker: str, year: int, growth_rate: float, language: dict = None
):
    # Local script execution: use default flat language
    if language is None or "pbt" not in language:
        lang = default_language
    else:
        pbt = language.get("pbt", {})
        common = language.get("common", {})
        lang = {
            "pbt_calc_title": pbt.get("calc_title", default_language["pbt_calc_title"]),
            "pbt_year": common.get("year", default_language["pbt_year"]),
            "pbt_income": pbt.get("income", default_language["pbt_income"]),
            "pbt_cumulative": pbt.get("cumulative", default_language["pbt_cumulative"]),
            "pbt_fcf_per_share": pbt.get(
                "fcf_per_share", default_language["pbt_fcf_per_share"]
            ),
            "pbt_growth_rate": pbt.get(
                "growth_rate", default_language["pbt_growth_rate"]
            ),
            "pbt_buy_price": pbt.get("buy_price_8y", default_language["pbt_buy_price"]),
            "pbt_fair_value": pbt.get(
                "fair_value_2x", default_language["pbt_fair_value"]
            ),
            "current_stock_price": common.get(
                "current_stock_price", default_language["current_stock_price"]
            ),
            "price_comparison": pbt.get(
                "price_comparison", default_language["price_comparison"]
            ),
        }

    result_data = _get_pbt_result(ticker, year, growth_rate)
    if not result_data:
        log.error(f"Could not find complete data for {ticker.upper()} in {year}")
        log.error(f"{year}: N/A")
        return

    key_metrics = fmp_api.get_key_metrics(ticker, limit=20)
    fcf = None
    for entry in key_metrics:
        if str(entry.get("calendarYear")) == str(result_data.get("year", year)):
            fcf = entry.get("freeCashFlowPerShare")
            break

    if not fcf:
        log.error(f"Could not find FCF for {ticker.upper()} in {year}")
        return

    _, _, table = _calculate_pbt_price(fcf, growth_rate, return_full_table=True)
    log.info(_format_pbt_report(result_data, table, lang))


if __name__ == "__main__":
    ticker = "aapl"
    year = 2024
    growth = 0.2

    result = _get_pbt_result(ticker, year, growth)

    if result:
        log.info(f"\n=== PBT Analysis for {ticker.upper()} ===")
        log.info(f"FCF per Share ({year}): ${result['fcf_per_share']:.2f}")
        log.info(f"Growth Rate: {growth * 100:.0f}%")
        log.info(f"\n--- Valuation ---")
        log.info(f"Buy Price (8Y Payback):  ${result['buy_price']:.2f}")
        log.info(f"Fair Value (2x):         ${result['fair_value']:.2f}")
        log.info(f"\n--- Current Market ---")
        log.info(f"Current Price:           ${result['current_stock_price']:.2f}")
        log.info(f"Price Comparison:        {result['price_comparison']}")
        log.info(f"Recommendation:          {result['investment_recommendation']}")
        log.info(f"\n--- Price Differences ---")
        log.info(f"vs Buy Price:            {result['percentage_diff_buy']:+.1f}%")
        log.info(f"vs Fair Value:           {result['percentage_diff_fair']:+.1f}%")
