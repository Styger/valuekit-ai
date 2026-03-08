"""
Moat Analysis System - Warren Buffett's 5 Economic Moats
Analyzes competitive advantages from SEC 10-K filings using RAG
"""

import logging
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import PIPELINE_VERSION
from backend.valuekit_ai.rag.rag_service import get_rag_service
from backend.valuekit_ai.config.analysis_config import AnalysisConfig

log = logging.getLogger(__name__)


@dataclass
class MoatScore:
    """Individual moat type scoring result"""

    name: str
    score: int  # 0-10
    evidence: List[str]
    confidence: str  # "High", "Medium", "Low"
    sources_used: int = 0
    avg_relevance: float = 0.0


@dataclass
class MoatAnalysis:
    """Complete moat analysis result"""

    ticker: str
    overall_score: int  # sum of all moat scores
    moat_strength: str  # "None", "Narrow", "Wide"
    moats: Dict[str, MoatScore]
    red_flags: List[str]
    competitive_position: str
    recommendation: str
    avg_relevance_score: float = 0.0  # mean relevance across all moat RAG calls
    total_sources_used: int = 0       # total unique source chunks retrieved


class MoatAnalyzer:
    """Analyze economic moats from SEC filings using RAG"""

    MOAT_TYPES = {
        "brand_power": {
            "name": "Brand Power & Intangible Assets",
            "indicators": [
                "brand recognition",
                "brand value",
                "customer loyalty",
                "pricing power",
                "premium pricing",
                "brand equity",
                "trademarks",
                "patents",
                "intellectual property",
                "reputation",
                "trust",
                "consumer preference",
            ],
            "query_template": (
                "What evidence exists of {ticker}'s brand strength, pricing power, "
                "and intangible assets? Look for customer loyalty, brand recognition, "
                "and ability to charge premium prices."
            ),
        },
        "switching_costs": {
            "name": "Switching Costs",
            "indicators": [
                "customer retention",
                "switching costs",
                "lock-in",
                "integration costs",
                "training costs",
                "migration difficulty",
                "long-term contracts",
                "sticky products",
                "ecosystem",
                "data migration",
                "compatibility",
                "learning curve",
            ],
            "query_template": (
                "What switching costs or customer lock-in mechanisms does {ticker} have? "
                "Look for high customer retention, long-term contracts, integration "
                "complexity, or ecosystem effects."
            ),
        },
        "network_effects": {
            "name": "Network Effects",
            "indicators": [
                "network effect",
                "user base",
                "platform",
                "marketplace",
                "two-sided market",
                "viral growth",
                "critical mass",
                "interconnected",
                "ecosystem participants",
                "flywheel",
            ],
            "query_template": (
                "Does {ticker} benefit from network effects? Look for platforms, "
                "marketplaces, or products that become more valuable as more users join."
            ),
        },
        "cost_advantages": {
            "name": "Cost Advantages",
            "indicators": [
                "economies of scale",
                "cost advantage",
                "low cost producer",
                "operational efficiency",
                "supply chain",
                "vertical integration",
                "proprietary process",
                "automation",
                "scale benefits",
            ],
            "query_template": (
                "What cost advantages does {ticker} have over competitors? "
                "Look for economies of scale, proprietary processes, or structural "
                "cost advantages."
            ),
        },
        "efficient_scale": {
            "name": "Efficient Scale",
            "indicators": [
                "regulated",
                "monopoly",
                "oligopoly",
                "limited competition",
                "market dominance",
                "barriers to entry",
                "license",
                "regulatory approval",
                "natural monopoly",
            ],
            "query_template": (
                "Does {ticker} operate in a market with efficient scale or high "
                "barriers to entry? Look for regulatory moats, natural monopolies, "
                "or oligopoly dynamics."
            ),
        },
    }

    RED_FLAG_CATEGORIES = {
        "debt": {
            "query": "What are {ticker}'s debt levels and ability to service debt?",
        },
        "competition": {
            "query": "What serious competitive threats does {ticker} face?",
        },
        "regulation": {
            "query": "What regulatory or legal risks threaten {ticker}'s business?",
        },
    }

    def __init__(self):
        self.rag = get_rag_service()

    # ── Business Model ────────────────────────────────────────────────────────

    def analyze_business_model(self, ticker: str) -> Dict:
        """
        Generate a concise business model description using RAG.

        Covers: business model, main products/services, target markets,
        revenue sources.

        Args:
            ticker: Stock ticker

        Returns:
            Dict with 'description' (str) and 'status' ('success' | 'error')
        """
        query = (
            f"Describe {ticker}'s business model in 3-5 sentences. Cover: "
            f"(1) what the company does, (2) its main products or services, "
            f"(3) its primary target markets or customer segments, and "
            f"(4) its main revenue sources. Be factual and concise."
        )

        log.info("[moat_analyzer][business_model] ticker=%s", ticker)

        result = self.rag.analyze_with_rag(
            query=query,
            quantitative_data={"ticker": ticker},
            max_tokens=512,
        )

        if result["status"] == "success":
            sources = result.get("sources", [])
            # Ticker guard: verify at least one retrieved chunk belongs to this ticker.
            # Without this, ChromaDB returns the closest chunks from *other* indexed
            # companies when the requested ticker has no documents loaded.
            matching = [
                s for s in sources
                if s.get("metadata", {}).get("ticker", "").upper() == ticker.upper()
            ]
            if not matching:
                log.warning(
                    "[moat_analyzer][business_model_wrong_ticker] ticker=%s "
                    "sources=%d matching=0",
                    ticker, len(sources),
                )
                return {
                    "status": "error",
                    "description": (
                        f"No documents indexed for {ticker}. "
                        "Load SEC filings first."
                    ),
                    "sources_used": 0,
                }

            log.info(
                "[moat_analyzer][business_model_ok] ticker=%s chars=%d sources=%d",
                ticker,
                len(result["analysis"]),
                len(matching),
            )
            return {
                "status": "success",
                "description": result["analysis"],
                "sources_used": len(matching),
            }
        else:
            log.warning("[moat_analyzer][business_model_failed] ticker=%s", ticker)
            return {
                "status": "error",
                "description": "Business model description could not be retrieved. "
                "Ensure SEC filings are loaded for this ticker.",
                "sources_used": 0,
            }

    # ── Single Moat ───────────────────────────────────────────────────────────

    def analyze_single_moat(
        self, ticker: str, moat_key: str, moat_config: Dict
    ) -> MoatScore:
        """
        Analyze a single moat type

        Args:
            ticker: Stock ticker
            moat_key: Key from MOAT_TYPES
            moat_config: Config dict for this moat type

        Returns:
            MoatScore with 0-10 rating
        """
        query = moat_config["query_template"].format(ticker=ticker)
        result = self.rag.analyze_with_rag(
            query=query,
            quantitative_data={"ticker": ticker},
        )

        if result["status"] != "success":
            log.warning(
                "[moat_analyzer][analysis_failed] ticker=%s moat=%s", ticker, moat_key
            )
            return MoatScore(
                name=moat_config["name"],
                score=0,
                evidence=["Analysis failed"],
                confidence="Low",
                sources_used=0,
                avg_relevance=0.0,
            )

        analysis_text = result["analysis"].lower()
        indicator_count = sum(
            1 for ind in moat_config["indicators"] if ind.lower() in analysis_text
        )

        evidence = self._extract_evidence(result["analysis"], moat_config["indicators"])
        all_sources = result.get("sources", [])

        # Ticker guard: only count chunks that actually belong to this ticker.
        # ChromaDB always returns k results regardless of relevance, so without this
        # check an unindexed ticker gets scored on another company's documents.
        sources = [
            s for s in all_sources
            if s.get("metadata", {}).get("ticker", "").upper() == ticker.upper()
        ]
        if not sources:
            log.warning(
                "[moat_analyzer][no_ticker_docs] ticker=%s moat=%s — "
                "no matching chunks, returning score=0",
                ticker, moat_key,
            )
            return MoatScore(
                name=moat_config["name"],
                score=0,
                evidence=["No documents indexed for this ticker"],
                confidence="Low",
                sources_used=0,
                avg_relevance=0.0,
            )

        source_count = len(sources)
        avg_relevance = (
            sum(s.get("relevance_score", 0.0) for s in sources) / source_count
        )

        # Score calculation
        base_score = min(10, (indicator_count / len(moat_config["indicators"])) * 20)
        source_bonus = min(2, source_count / 3)
        evidence_bonus = min(2, len(evidence))
        score = int(min(10, base_score + source_bonus + evidence_bonus))

        # Confidence ceiling based on source count and indicators
        if source_count >= 3 and indicator_count >= 3:
            confidence, ceiling = "High", 10
        elif source_count >= 2 and indicator_count >= 2:
            confidence, ceiling = "Medium", 8
        else:
            confidence, ceiling = "Low", 5

        final_score = min(score, ceiling)

        log.debug(
            "[moat_analyzer][score] ticker=%s moat=%s indicators=%d sources=%d "
            "confidence=%s ceiling=%d final_score=%d",
            ticker,
            moat_key,
            indicator_count,
            source_count,
            confidence,
            ceiling,
            final_score,
        )

        return MoatScore(
            name=moat_config["name"],
            score=final_score,
            evidence=evidence[:3],
            confidence=confidence,
            sources_used=source_count,
            avg_relevance=avg_relevance,
        )

    def _extract_evidence(self, analysis_text: str, indicators: List[str]) -> List[str]:
        """Extract sentences containing moat indicators as evidence"""
        sentences = analysis_text.split(".")
        evidence = []
        for sentence in sentences:
            if any(ind.lower() in sentence.lower() for ind in indicators):
                cleaned = sentence.strip()
                if cleaned and len(cleaned) > 20:
                    evidence.append(cleaned)
        return evidence[:5]

    def detect_red_flags(
        self, ticker: str, enabled_categories: List[str] = None
    ) -> List[str]:
        """
        Detect investment red flags

        Args:
            ticker: Stock ticker
            enabled_categories: Categories to check (None = all)

        Returns:
            List of identified red flag descriptions
        """
        if enabled_categories is None:
            enabled_categories = list(self.RED_FLAG_CATEGORIES.keys())

        red_flags = []

        for category in enabled_categories:
            if category not in self.RED_FLAG_CATEGORIES:
                continue

            config = self.RED_FLAG_CATEGORIES[category]
            query = config["query"].format(ticker=ticker)
            result = self.rag.analyze_with_rag(query=query)

            if result["status"] == "success":
                analysis = result["analysis"].lower()
                risk_keywords = [
                    "significant risk",
                    "major concern",
                    "serious threat",
                    "material impact",
                    "substantial risk",
                    "critical",
                ]
                if any(kw in analysis for kw in risk_keywords):
                    red_flags.append(
                        f"{category.title()}: {result['analysis'][:200]}..."
                    )
                    log.debug(
                        "[moat_analyzer][red_flag] ticker=%s category=%s",
                        ticker,
                        category,
                    )

        log.info(
            "[moat_analyzer][red_flags] ticker=%s count=%d", ticker, len(red_flags)
        )
        return red_flags

    def analyze_moats(
        self, ticker: str, config: Optional[AnalysisConfig] = None
    ) -> MoatAnalysis:
        log.info(
            "[moat_analyzer][start] ticker=%s pipeline_version=%s",
            ticker,
            PIPELINE_VERSION,
        )

        if config:
            enabled_moats = config.get_enabled_moats()
            enabled_rf = config.get_enabled_red_flags() if config.run_red_flags else []
        else:
            enabled_moats = list(self.MOAT_TYPES.keys())
            enabled_rf = list(self.RED_FLAG_CATEGORIES.keys())

        moats = {}
        total_score = 0
        relevance_samples: List[float] = []
        total_sources = 0

        for moat_key in enabled_moats:
            if moat_key not in self.MOAT_TYPES:
                continue
            moat_config = self.MOAT_TYPES[moat_key]
            moat_score = self.analyze_single_moat(ticker, moat_key, moat_config)
            moats[moat_key] = moat_score
            total_score += moat_score.score
            if moat_score.sources_used > 0:
                relevance_samples.append(moat_score.avg_relevance)
                total_sources += moat_score.sources_used

        avg_relevance = (
            sum(relevance_samples) / len(relevance_samples)
            if relevance_samples else 0.0
        )

        red_flags = self.detect_red_flags(ticker, enabled_rf)

        max_possible = len(moats) * 10
        score_pct = (total_score / max_possible * 100) if max_possible > 0 else 0
        moat_strength = self._moat_strength_from_score(int(score_pct))

        competitive_position = self._assess_competitive_position(moats, red_flags)
        recommendation = self._generate_recommendation(
            total_score, moat_strength, red_flags
        )

        log.info(
            "[moat_analyzer][complete] ticker=%s strength=%s score=%d/%d "
            "red_flags=%d pipeline_version=%s",
            ticker,
            moat_strength,
            total_score,
            max_possible,
            len(red_flags),
            PIPELINE_VERSION,
        )

        return MoatAnalysis(
            ticker=ticker,
            overall_score=total_score,
            moat_strength=moat_strength,
            moats=moats,
            red_flags=red_flags,
            competitive_position=competitive_position,
            recommendation=recommendation,
            avg_relevance_score=avg_relevance,
            total_sources_used=total_sources,
        )

    @staticmethod
    def _moat_strength_from_score(moat_score_normalized: int) -> str:
        """
        Single authoritative mapping from moat score (0-100) to moat strength.
        Used in analyze_moats() — moat_strength is NEVER derived from combined_score.
        """
        if moat_score_normalized >= 65:
            return "Wide"
        elif moat_score_normalized >= 40:
            return "Narrow"
        else:
            return "None"

    def _assess_competitive_position(
        self, moats: Dict[str, MoatScore], red_flags: List[str]
    ) -> str:
        strong = [k for k, v in moats.items() if v.score >= 7]
        moderate = [k for k, v in moats.items() if 4 <= v.score < 7]

        if len(strong) >= 2 and len(red_flags) == 0:
            return "Strong competitive position with multiple durable moats"
        elif len(strong) >= 1 or len(moderate) >= 2:
            return "Moderate competitive position with some protective advantages"
        else:
            return "Weak competitive position with limited moat evidence"

    def _generate_recommendation(
        self, overall_score: int, moat_strength: str, red_flags: List[str]
    ) -> str:
        """
        Returns a moat quality description only.
        Investment decision (BUY/HOLD/PASS) is made exclusively by
        IntegratedAnalyzer._make_decision() based on combined_score.
        """
        flag_note = f", {len(red_flags)} risk(s) to monitor" if red_flags else ""
        if moat_strength == "Wide":
            return f"Wide economic moat — strong durable competitive advantages{flag_note}"
        elif moat_strength == "Narrow":
            return f"Narrow economic moat — moderate competitive advantages{flag_note}"
        else:
            return f"No identifiable economic moat{flag_note}"
