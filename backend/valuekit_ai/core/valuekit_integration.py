"""
ValueKit Integration Module
Connects existing ValueKit formulas (MOS, CAGR, Profitability) with AI Moat Analysis
"""

import logging
import sys
from pathlib import Path
from typing import Dict, Optional

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.analysis_config import AnalysisConfig
from backend.valuekit_ai.config.config import PIPELINE_VERSION
from backend.valuekit_ai.core.investment_analyzer import IntegratedAnalyzer
from backend.logic.mos import calculate_mos_value_from_ticker
from backend.logic.cagr import _mos_growth_estimate_auto
from backend.logic.growth_consensus import get_growth_consensus
from backend.logic.tencap import _get_ten_cap_result
from backend.logic.pbt import _get_pbt_result
from backend.logic import profitability
from backend.api import fmp_api

log = logging.getLogger(__name__)


class ValueKitAnalyzer:
    """
    Integrated ValueKit Analysis
    Combines quantitative formulas with qualitative AI moat analysis
    """

    def __init__(self):
        self.ai_analyzer = IntegratedAnalyzer()

    def estimate_growth_rate(
        self,
        ticker: str,
        period_years: int = 5,
        end_year: int = 2024,
        include_book: bool = True,
        include_eps: bool = True,
        include_revenue: bool = True,
        include_cashflow: bool = True,
    ) -> Dict[str, float]:
        """
        Estimate growth rate using CAGR analysis

        Args:
            ticker: Stock ticker
            period_years: CAGR calculation period
            end_year: End year for calculation
            include_book/eps/revenue/cashflow: Metrics to include

        Returns:
            Dict with CAGR metrics and average growth rate
        """
        start_year = end_year - period_years

        log.info(
            "[valuekit_integration][estimate_growth] ticker=%s start=%d end=%d "
            "period=%d pipeline_version=%s",
            ticker,
            start_year,
            end_year,
            period_years,
            PIPELINE_VERSION,
        )

        data, mos_input = fmp_api.get_year_data_by_range(
            ticker, start_year, years=period_years
        )

        if not data or not mos_input:
            raise ValueError(f"No data available for {ticker}")

        growth_metrics = _mos_growth_estimate_auto(
            data_dict=mos_input,
            start_year=start_year,
            end_year=end_year,
            period_years=period_years,
            known_start_year=start_year,
            include_book=include_book,
            include_eps=include_eps,
            include_revenue=include_revenue,
            include_cashflow=include_cashflow,
        )

        log.debug(
            "[valuekit_integration][growth_result] ticker=%s metrics=%s",
            ticker,
            growth_metrics,
        )
        return growth_metrics

    def analyze_stock_complete(
        self,
        ticker: str,
        year: int = 2024,
        growth_rate: Optional[float] = None,
        auto_estimate_growth: bool = True,
        discount_rate: float = 0.15,
        margin_of_safety: float = 0.50,
        load_sec_data: bool = False,
        load_earnings_data: bool = False,
        config: Optional[AnalysisConfig] = None,
    ) -> Dict:
        """
        Complete stock analysis combining all ValueKit components

        Args:
            ticker: Stock ticker
            year: Base year for calculations
            growth_rate: Manual growth rate override
            auto_estimate_growth: Use CAGR-based growth estimate
            discount_rate: Discount rate for MOS calculation
            margin_of_safety: Safety margin percentage
            load_sec_data: Reload SEC filings
            load_earnings_data: Load earnings transcripts
            config: AnalysisConfig (controls which components run)

        Returns:
            Complete analysis dict
        """
        log.info(
            "[valuekit_integration][analyze_start] ticker=%s year=%d "
            "discount_rate=%.2f mos=%.2f auto_growth=%s pipeline_version=%s",
            ticker,
            year,
            discount_rate,
            margin_of_safety,
            auto_estimate_growth,
            PIPELINE_VERSION,
        )

        # Step 1: Estimate growth rate via 3-source consensus
        growth_consensus_result = None
        if auto_estimate_growth and growth_rate is None:
            try:
                growth_consensus_result = get_growth_consensus(ticker, year)
                growth_rate = growth_consensus_result["rate"]
                log.info(
                    "[valuekit_integration][growth_consensus] ticker=%s rate=%.4f "
                    "method=%s capped=%s",
                    ticker, growth_rate,
                    growth_consensus_result["method"],
                    growth_consensus_result["capped"],
                )
            except Exception as e:
                log.warning(
                    "[valuekit_integration][growth_consensus_failed] ticker=%s error=%s",
                    ticker, e,
                )
                growth_rate = 0.10

        # Step 2: MOS calculation
        mos_result = None
        if config is None or config.run_mos:
            try:
                mos_result = calculate_mos_value_from_ticker(
                    ticker=ticker,
                    year=year,
                    growth_rate=growth_rate,
                    discount_rate=discount_rate,
                    margin_of_safety=margin_of_safety,
                )
                log.info(
                    "[valuekit_integration][mos_complete] ticker=%s "
                    "fair_value=%s mos_price=%s",
                    ticker,
                    mos_result.get("Fair Value Today") if mos_result else None,
                    mos_result.get("MOS Price") if mos_result else None,
                )
            except Exception as e:
                log.warning(
                    "[valuekit_integration][mos_failed] ticker=%s error=%s", ticker, e
                )

        # Step 3: Profitability analysis
        profitability_result = None
        if config is None or config.run_profitability:
            try:
                profitability_result = (
                    profitability.calculate_profitability_metrics_from_ticker(
                        ticker, year
                    )
                )
                log.info(
                    "[valuekit_integration][profitability_complete] ticker=%s "
                    "roe=%s roic=%s",
                    ticker,
                    profitability_result.get("roe") if profitability_result else None,
                    profitability_result.get("roic") if profitability_result else None,
                )
            except Exception as e:
                log.warning(
                    "[valuekit_integration][profitability_failed] ticker=%s error=%s",
                    ticker,
                    e,
                )

        # Step 3b: TenCap
        tencap_result = None
        if config is None or getattr(config, "run_tencap", True):
            try:
                tencap_result = _get_ten_cap_result(ticker, year)
                log.info(
                    "[valuekit_integration][tencap_complete] ticker=%s fair_value=%s",
                    ticker,
                    tencap_result.get("ten_cap_fair_value") if tencap_result else None,
                )
            except Exception as e:
                log.warning(
                    "[valuekit_integration][tencap_failed] ticker=%s error=%s",
                    ticker,
                    e,
                )

        # Step 3c: PBT
        pbt_result = None
        if config is None or getattr(config, "run_pbt", True):
            try:
                pbt_result = _get_pbt_result(ticker, year, growth_rate or 0.10)
                log.info(
                    "[valuekit_integration][pbt_complete] ticker=%s fair_value=%s",
                    ticker,
                    pbt_result.get("fair_value") if pbt_result else None,
                )
            except Exception as e:
                log.warning(
                    "[valuekit_integration][pbt_failed] ticker=%s error=%s", ticker, e
                )

        # Step 5: AI moat analysis (quality + valuation + moat scores)
        ai_decision = None
        if config is None or config.run_moat_analysis:
            ai_decision = self.ai_analyzer.analyze(
                ticker=ticker,
                profitability_result=profitability_result,
                mos_result=mos_result,
                tencap_result=tencap_result,
                pbt_result=pbt_result,
                growth_rate=growth_rate or 0.10,
                load_sec_data=load_sec_data,
                load_earnings_data=load_earnings_data,
                config=config,
            )

        final_recommendation = self._combine_recommendations(mos_result, ai_decision)

        log.info(
            "[valuekit_integration][complete] ticker=%s recommendation=%s",
            ticker,
            final_recommendation,
        )

        return {
            "ticker": ticker,
            "year": year,
            "growth_rate": growth_rate,
            "growth_consensus": growth_consensus_result,
            "discount_rate": discount_rate,
            "margin_of_safety_pct": margin_of_safety,
            "mos_result": mos_result,
            "tencap_result": tencap_result,
            "pbt_result": pbt_result,
            "profitability_result": profitability_result,
            "ai_decision": ai_decision.to_dict() if ai_decision else None,
            "final_recommendation": final_recommendation,
            "pipeline_version": PIPELINE_VERSION,
        }

    def _combine_recommendations(
        self,
        mos_result: Optional[Dict],
        ai_decision,
    ) -> str:
        """
        Returns the final recommendation string.
        Delegates entirely to ai_decision.decision — the single authoritative
        decision from IntegratedAnalyzer._make_decision().
        No independent logic here to avoid contradictions.
        """
        if not ai_decision:
            if mos_result:
                return mos_result.get("Investment Recommendation", "N/A")
            return "Insufficient data for recommendation"

        decision = ai_decision.decision
        score = ai_decision.overall_score
        moat = ai_decision.moat_analysis.moat_strength
        flags = len(ai_decision.moat_analysis.red_flags)

        return (
            f"{decision} — Combined Score: {score}/100 | "
            f"Moat: {moat} | Red Flags: {flags}"
        )
