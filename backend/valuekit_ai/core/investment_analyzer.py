"""
Integrated Investment Analyzer
Combines quantitative metrics with moat analysis for final decision
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, Optional

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.core.moat_analyzer import MoatAnalyzer, MoatAnalysis
from backend.valuekit_ai.config.analysis_config import AnalysisConfig
from backend.valuekit_ai.config.config import PIPELINE_VERSION

log = logging.getLogger(__name__)


@dataclass
class InvestmentDecision:
    """Final investment decision with full traceability"""

    ticker: str
    decision: str  # "STRONG BUY", "BUY", "HOLD", "PASS"
    confidence: str  # "High", "Medium", "Low"
    quantitative_score: int  # 0-100
    qualitative_score: int  # 0-100 (normalized moat score)
    overall_score: int  # 0-100
    reasoning: str
    moat_analysis: MoatAnalysis
    quantitative_metrics: Dict[str, Any]
    mos_result: Optional[Dict[str, Any]] = None
    profitability_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable representation for traceability"""
        return {
            "ticker": self.ticker,
            "decision": self.decision,
            "confidence": self.confidence,
            "quantitative_score": self.quantitative_score,
            "qualitative_score": self.qualitative_score,
            "overall_score": self.overall_score,
            "reasoning": self.reasoning,
            "moat_strength": self.moat_analysis.moat_strength,
            "moat_score": self.moat_analysis.overall_score,
            "red_flags": self.moat_analysis.red_flags,
            "quantitative_metrics": self.quantitative_metrics,
            "pipeline_version": PIPELINE_VERSION,
        }


class IntegratedAnalyzer:
    """
    Integrated Investment Analysis combining quantitative formulas
    with qualitative AI moat analysis
    """

    def __init__(self):
        self.moat_analyzer = MoatAnalyzer()

    def _calculate_quantitative_score(self, metrics: Dict[str, Any]) -> int:
        """
        Calculate quantitative score (0-100)

        Inputs:
            margin_of_safety: percentage
            roic: percentage
            fcf_yield: percentage (optional)
        """
        score = 0

        # Margin of Safety: 0-40 points
        mos = float(str(metrics.get("margin_of_safety", "0%")).replace("%", ""))
        if mos >= 25:
            score += 40
        elif mos >= 15:
            score += 30
        elif mos >= 10:
            score += 20
        elif mos >= 5:
            score += 10

        # ROIC: 0-40 points
        roic_str = str(metrics.get("roic", "0%")).replace("%", "")
        if roic_str and roic_str.lower() != "none":
            roic = float(roic_str)
            if roic >= 30:
                score += 40
            elif roic >= 20:
                score += 30
            elif roic >= 15:
                score += 20
            elif roic >= 10:
                score += 10

        # FCF Yield: 0-20 points
        if "fcf_yield" in metrics:
            fcf = float(str(metrics["fcf_yield"]).replace("%", ""))
            if fcf >= 8:
                score += 20
            elif fcf >= 5:
                score += 15
            elif fcf >= 3:
                score += 10
            elif fcf >= 1:
                score += 5

        log.debug(
            "[investment_analyzer][quant_score] mos=%s roic=%s score=%d",
            metrics.get("margin_of_safety"),
            metrics.get("roic"),
            score,
        )
        return min(100, score)

    def _combine_scores(
        self, quant_score: int, moat_analysis: MoatAnalysis
    ) -> InvestmentDecision:
        """Combine quantitative and qualitative scores"""
        num_moats = len(moat_analysis.moats)
        max_moat = num_moats * 10 if num_moats > 0 else 50

        moat_normalized = (
            int((moat_analysis.overall_score / max_moat) * 100) if max_moat > 0 else 0
        )

        overall = int((quant_score * 0.6) + (moat_normalized * 0.4))

        # Red flag penalty: -5 per flag, max -25
        red_flags = len(moat_analysis.red_flags)
        overall = max(0, overall - min(25, red_flags * 5))

        if overall >= 80 and red_flags == 0:
            decision, confidence = "STRONG BUY", "High"
        elif overall >= 70 and red_flags <= 1:
            decision = "BUY"
            confidence = "High" if red_flags == 0 else "Medium"
        elif overall >= 60:
            decision, confidence = "BUY", "Medium"
        elif overall >= 50 or (moat_analysis.moat_strength == "Wide" and overall >= 45):
            decision = "HOLD"
            confidence = "Medium" if red_flags <= 2 else "Low"
        else:
            decision, confidence = "PASS", "Low"

        reasoning = self._generate_reasoning(
            moat_analysis.ticker, quant_score, moat_analysis, decision
        )

        log.debug(
            "[investment_analyzer][combine_scores] quant=%d moat_normalized=%d "
            "red_flags=%d overall=%d decision=%s confidence=%s",
            quant_score,
            moat_normalized,
            red_flags,
            overall,
            decision,
            confidence,
        )

        return InvestmentDecision(
            ticker=moat_analysis.ticker,
            decision=decision,
            confidence=confidence,
            quantitative_score=quant_score,
            qualitative_score=moat_normalized,
            overall_score=overall,
            reasoning=reasoning,
            moat_analysis=moat_analysis,
            quantitative_metrics={},
        )

    def _generate_reasoning(
        self,
        ticker: str,
        quant_score: int,
        moat_analysis: MoatAnalysis,
        decision: str,
    ) -> str:
        parts = [f"{ticker} analysis indicates a {decision} recommendation."]

        if quant_score >= 70:
            parts.append("Quantitative metrics suggest attractive valuation.")
        elif quant_score >= 50:
            parts.append("Quantitative metrics suggest fair valuation.")
        else:
            parts.append("Quantitative metrics suggest limited margin of safety.")

        if moat_analysis.moat_strength == "Wide":
            parts.append("The company appears to possess a wide economic moat.")
        elif moat_analysis.moat_strength == "Narrow":
            parts.append("Evidence indicates a narrow competitive advantage.")
        else:
            parts.append("Available documents do not indicate a durable economic moat.")

        if moat_analysis.red_flags:
            parts.append(
                f"{len(moat_analysis.red_flags)} red flag(s) identified requiring attention."
            )

        return " ".join(parts)

    def analyze(
        self,
        ticker: str,
        quantitative_metrics: Dict,
        load_sec_data: bool = False,
        load_earnings_data: bool = False,
        config: Optional[AnalysisConfig] = None,
        mos_result: Optional[Dict] = None,
        profitability_result: Optional[Dict] = None,
    ) -> InvestmentDecision:
        """
        Run complete investment analysis

        Args:
            ticker: Stock ticker
            quantitative_metrics: Dict with margin_of_safety, roic, etc.
            load_sec_data: Reload SEC 10-K data
            load_earnings_data: Load earnings transcripts
            config: AnalysisConfig (controls which components run)
            mos_result: Pre-calculated MOS result
            profitability_result: Pre-calculated profitability metrics

        Returns:
            InvestmentDecision
        """
        log.info(
            "[investment_analyzer][start] ticker=%s pipeline_version=%s "
            "load_sec=%s load_earnings=%s",
            ticker,
            PIPELINE_VERSION,
            load_sec_data,
            load_earnings_data,
        )
        log.debug(
            "[investment_analyzer][params] metrics=%s config=%s",
            quantitative_metrics,
            config,
        )

        if load_sec_data:
            from backend.valuekit_ai.data_pipeline.load_sec_data import (
                load_company_data,
            )

            sec_result = load_company_data(ticker)
            if sec_result.get("status") != "success":
                log.warning("[investment_analyzer][sec_load_failed] ticker=%s", ticker)

        if load_earnings_data:
            from backend.valuekit_ai.data_pipeline.load_earnings_data import (
                load_earnings_data as _load_earnings,
            )

            quarters = getattr(config, "earnings_quarters", 4)
            earnings_result = _load_earnings(ticker, quarters=quarters)
            if earnings_result.get("status") != "success":
                log.warning(
                    "[investment_analyzer][earnings_load_failed] ticker=%s", ticker
                )

        quant_score = self._calculate_quantitative_score(quantitative_metrics)
        log.info(
            "[investment_analyzer][quant_score] ticker=%s score=%d", ticker, quant_score
        )

        if config is None or config.run_moat_analysis:
            moat_analysis = self.moat_analyzer.analyze_moats(ticker, config=config)
        else:
            from backend.valuekit_ai.core.moat_analyzer import MoatAnalysis

            moat_analysis = MoatAnalysis(
                ticker=ticker,
                overall_score=0,
                moat_strength="None",
                moats={},
                red_flags=[],
                competitive_position="Moat analysis skipped",
                recommendation="N/A",
            )
            log.info("[investment_analyzer][moat_skipped] ticker=%s", ticker)

        decision = self._combine_scores(quant_score, moat_analysis)
        decision.quantitative_metrics = quantitative_metrics
        decision.mos_result = mos_result
        decision.profitability_result = profitability_result

        log.info(
            "[investment_analyzer][complete] ticker=%s decision=%s confidence=%s "
            "overall_score=%d pipeline_version=%s",
            ticker,
            decision.decision,
            decision.confidence,
            decision.overall_score,
            PIPELINE_VERSION,
        )

        return decision
