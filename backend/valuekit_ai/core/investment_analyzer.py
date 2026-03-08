"""
Integrated Investment Analyzer
Combines quantitative metrics with moat analysis for final decision.

3-Score Model:
  Quality Score   (40%) — ROIC, FCF Yield, Net Margin, CAGR
  Valuation Score (20%) — price vs fair value (MOS / TenCap / PBT)
  Moat Score      (40%) — RAG qualitative moat analysis
"""

import logging
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, List, Optional

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
    decision: str           # "STRONG BUY", "BUY", "HOLD", "PASS"
    confidence: str         # "High", "Medium", "Low"
    quantitative_score: int  # Quality Score 0-100
    valuation_score: int     # Valuation Score 0-100
    qualitative_score: int   # Moat Score normalized 0-100
    overall_score: int       # Combined Score 0-100
    reasoning: str
    moat_analysis: MoatAnalysis
    quantitative_metrics: Dict[str, Any] = field(default_factory=dict)
    mos_result: Optional[Dict[str, Any]] = None
    profitability_result: Optional[Dict[str, Any]] = None

    def to_dict(self) -> Dict[str, Any]:
        """JSON-serializable representation for traceability"""
        return {
            "ticker": self.ticker,
            "decision": self.decision,
            "confidence": self.confidence,
            "quantitative_score": self.quantitative_score,
            "valuation_score": self.valuation_score,
            "qualitative_score": self.qualitative_score,
            "overall_score": self.overall_score,
            "reasoning": self.reasoning,
            "moat_strength": self.moat_analysis.moat_strength,
            "moat_score": self.moat_analysis.overall_score,
            "red_flags": self.moat_analysis.red_flags,
            "avg_relevance_score": self.moat_analysis.avg_relevance_score,
            "total_sources_used": self.moat_analysis.total_sources_used,
            "moat_details": {
                key: {
                    "name": ms.name,
                    "score": ms.score,          # 0–10
                    "confidence": ms.confidence,
                    "sources_used": ms.sources_used,
                    "evidence": ms.evidence,
                }
                for key, ms in self.moat_analysis.moats.items()
            },
            "quantitative_metrics": self.quantitative_metrics,
            "pipeline_version": PIPELINE_VERSION,
        }


class IntegratedAnalyzer:
    """
    Integrated Investment Analysis combining quantitative formulas
    with qualitative AI moat analysis.
    """

    def __init__(self):
        self.moat_analyzer = MoatAnalyzer()

    # ── Score calculators ────────────────────────────────────────────────────

    def _calculate_quality_score(
        self,
        profitability_result: Optional[Dict],
        growth_rate: float,
    ) -> int:
        """
        Quality Score: 0–100
        ROIC (40) + FCF Yield (20) + Net Margin (20) + CAGR (20)
        All inputs are decimals (e.g. 0.25 = 25%).
        """
        p = profitability_result or {}
        score = 0

        # ROIC — max 40 pts
        roic = p.get("roic") or 0
        if roic >= 0.30:   score += 40
        elif roic >= 0.20: score += 30
        elif roic >= 0.15: score += 20
        elif roic >= 0.10: score += 10

        # FCF Yield — max 20 pts
        fcf_yield = p.get("fcf_yield") or 0
        if fcf_yield >= 0.08:   score += 20
        elif fcf_yield >= 0.05: score += 15
        elif fcf_yield >= 0.03: score += 10
        elif fcf_yield >= 0.01: score += 5

        # Net Margin — max 20 pts
        nm = p.get("net_margin") or 0
        if nm >= 0.20:   score += 20
        elif nm >= 0.15: score += 15
        elif nm >= 0.10: score += 10
        elif nm >= 0.05: score += 5

        # CAGR — max 20 pts
        cagr = growth_rate or 0
        if cagr >= 0.20:   score += 20
        elif cagr >= 0.15: score += 15
        elif cagr >= 0.10: score += 10
        elif cagr >= 0.05: score += 5

        result = min(100, score)
        log.debug(
            "[investment_analyzer][quality_score] roic=%.3f fcf_yield=%.3f "
            "net_margin=%.3f cagr=%.3f score=%d",
            roic, fcf_yield, nm, cagr, result,
        )
        return result

    def _calculate_valuation_score(
        self,
        mos_result: Optional[Dict],
        tencap_result: Optional[Dict],
        pbt_result: Optional[Dict],
    ) -> int:
        """
        Valuation Score: 0–100
        Average of available price-vs-fair-value scores per method.
        100 = deeply undervalued, 50 = at fair value, 0 = severely overvalued.
        Returns 50 (neutral) if no valuation data is available.
        """
        def _price_score(current: float, fair: float) -> Optional[int]:
            if not current or not fair or fair <= 0 or current <= 0:
                return None
            ratio = current / fair
            return max(0, min(100, int((2.0 - ratio) * 50)))

        scores = []

        if mos_result:
            s = _price_score(
                mos_result.get("Current Stock Price"),
                mos_result.get("Fair Value Today"),
            )
            if s is not None:
                scores.append(s)

        if tencap_result:
            s = _price_score(
                tencap_result.get("current_stock_price"),
                tencap_result.get("ten_cap_fair_value"),
            )
            if s is not None:
                scores.append(s)

        if pbt_result:
            s = _price_score(
                pbt_result.get("current_stock_price"),
                pbt_result.get("fair_value"),
            )
            if s is not None:
                scores.append(s)

        result = int(sum(scores) / len(scores)) if scores else 50
        log.debug(
            "[investment_analyzer][valuation_score] method_scores=%s score=%d",
            scores, result,
        )
        return result

    def _make_decision(self, combined_score: int, red_flag_count: int):
        """
        Single authoritative decision logic — laut Flowchart v3.
        Returns: (decision, confidence)
        """
        if combined_score >= 80 and red_flag_count == 0:
            return "STRONG BUY", "High"
        elif combined_score >= 70 and red_flag_count <= 1:
            return "BUY", "Medium"
        elif combined_score >= 50:
            return "HOLD", "Medium"
        else:
            return "PASS", "Low"

    def _combine_scores(
        self,
        quality_score: int,
        valuation_score: int,
        moat_analysis: MoatAnalysis,
    ) -> InvestmentDecision:
        """
        Combine Quality (40%) + Valuation (20%) + Moat (40%) scores.
        Combined Score = Q×0.40 + V×0.20 + M×0.40 − Red Flag Penalty
        """
        num_moats = len(moat_analysis.moats)
        max_moat = num_moats * 10 if num_moats > 0 else 50
        moat_normalized = (
            int((moat_analysis.overall_score / max_moat) * 100) if max_moat > 0 else 0
        )

        combined = int(
            quality_score * 0.40
            + valuation_score * 0.20
            + moat_normalized * 0.40
        )

        red_flags = len(moat_analysis.red_flags)
        overall = max(0, combined - min(25, red_flags * 5))

        decision, confidence = self._make_decision(overall, red_flags)

        reasoning = self._generate_reasoning(
            moat_analysis.ticker, quality_score, moat_analysis, decision
        )

        log.debug(
            "[investment_analyzer][combine_scores] quality=%d valuation=%d "
            "moat_normalized=%d red_flags=%d combined=%d overall=%d "
            "decision=%s confidence=%s",
            quality_score, valuation_score, moat_normalized,
            red_flags, combined, overall, decision, confidence,
        )

        return InvestmentDecision(
            ticker=moat_analysis.ticker,
            decision=decision,
            confidence=confidence,
            quantitative_score=quality_score,
            valuation_score=valuation_score,
            qualitative_score=moat_normalized,
            overall_score=overall,
            reasoning=reasoning,
            moat_analysis=moat_analysis,
        )

    def _generate_reasoning(
        self,
        ticker: str,
        quality_score: int,
        moat_analysis: MoatAnalysis,
        decision: str,
    ) -> str:
        parts = [f"{ticker} analysis indicates a {decision} recommendation."]

        if quality_score >= 70:
            parts.append("Quantitative metrics show strong quality fundamentals.")
        elif quality_score >= 50:
            parts.append("Quantitative metrics show acceptable quality fundamentals.")
        else:
            parts.append("Quantitative metrics indicate weak quality fundamentals.")

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

    # ── Main entry point ────────────────────────────────────────────────────

    def analyze(
        self,
        ticker: str,
        profitability_result: Optional[Dict] = None,
        mos_result: Optional[Dict] = None,
        tencap_result: Optional[Dict] = None,
        pbt_result: Optional[Dict] = None,
        growth_rate: float = 0.10,
        load_sec_data: bool = False,
        load_earnings_data: bool = False,
        load_news_data: bool = False,
        config: Optional[AnalysisConfig] = None,
    ) -> InvestmentDecision:
        """
        Run complete investment analysis.

        Args:
            ticker: Stock ticker
            profitability_result: From profitability.calculate_profitability_metrics_from_ticker()
            mos_result: From mos.calculate_mos_value_from_ticker()
            tencap_result: From tencap._get_ten_cap_result()
            pbt_result: From pbt._get_pbt_result()
            growth_rate: CAGR growth estimate (decimal, e.g. 0.12)
            load_sec_data: Reload SEC 10-K data
            load_earnings_data: Load earnings transcripts
            load_news_data: Load recent Yahoo Finance news articles
            config: AnalysisConfig (controls which components run)

        Returns:
            InvestmentDecision
        """
        log.info(
            "[investment_analyzer][start] ticker=%s pipeline_version=%s "
            "load_sec=%s load_earnings=%s load_news=%s",
            ticker, PIPELINE_VERSION, load_sec_data, load_earnings_data, load_news_data,
        )

        if load_sec_data:
            from backend.valuekit_ai.data_pipeline.load_sec_data import load_company_data
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

        if load_news_data:
            from backend.valuekit_ai.data_pipeline.load_sec_data import (
                load_news_data as _load_news,
            )
            news_result = _load_news(ticker)
            if news_result.get("status") != "success":
                log.warning(
                    "[investment_analyzer][news_load_failed] ticker=%s", ticker
                )

        # Calculate the three scores
        quality_score = self._calculate_quality_score(profitability_result, growth_rate)
        valuation_score = self._calculate_valuation_score(mos_result, tencap_result, pbt_result)

        log.info(
            "[investment_analyzer][scores] ticker=%s quality=%d valuation=%d",
            ticker, quality_score, valuation_score,
        )

        if config is None or config.run_moat_analysis:
            moat_analysis = self.moat_analyzer.analyze_moats(ticker, config=config)
        else:
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

        decision = self._combine_scores(quality_score, valuation_score, moat_analysis)

        # Attach raw results for traceability
        decision.quantitative_metrics = {
            "roic": profitability_result.get("roic") if profitability_result else None,
            "fcf_yield": profitability_result.get("fcf_yield") if profitability_result else None,
            "net_margin": profitability_result.get("net_margin") if profitability_result else None,
            "growth_rate": growth_rate,
        }
        decision.mos_result = mos_result
        decision.profitability_result = profitability_result

        log.info(
            "[investment_analyzer][complete] ticker=%s decision=%s confidence=%s "
            "overall_score=%d quality=%d valuation=%d pipeline_version=%s",
            ticker, decision.decision, decision.confidence,
            decision.overall_score, quality_score, valuation_score, PIPELINE_VERSION,
        )

        return decision
