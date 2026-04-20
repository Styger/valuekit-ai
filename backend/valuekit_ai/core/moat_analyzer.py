"""
Moat Analysis System - Warren Buffett's 5 Economic Moats
Analyzes competitive advantages from SEC 10-K filings using RAG
"""

import logging
import re
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List, Optional, Tuple

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import PIPELINE_VERSION
from backend.valuekit_ai.rag.rag_service import get_rag_service
from backend.valuekit_ai.config.analysis_config import AnalysisConfig

log = logging.getLogger(__name__)

# ── Two-step evidence-level scoring ──────────────────────────────────────────
#
# The LLM is instructed to classify evidence strength with a structured tag.
# Python code maps that tag to a numeric score (see _EVIDENCE_LEVEL_SCORES and
# _parse_evidence_level below).  Free-form numeric score generation is removed
# from the prompt entirely to reduce LLM calibration variance.

_MOAT_SCORING_INSTRUCTION = (
    "Assess the evidence for this specific moat type using the following two-step process:\n"
    "\n"
    "Step 1 — Evidence classification: Based solely on the retrieved documents, "
    "classify the strength of evidence as EXACTLY ONE of the following labels "
    "and output it on its own line in this exact format:\n"
    "  EVIDENCE_LEVEL: NONE      — no relevant evidence found in the documents\n"
    "  EVIDENCE_LEVEL: LIMITED   — indirect mentions or weak signals only\n"
    "  EVIDENCE_LEVEL: MODERATE  — clear but non-quantified evidence of the moat mechanism\n"
    "  EVIDENCE_LEVEL: STRONG    — direct quantitative or qualitative evidence of the moat mechanism\n"
    "\n"
    "MANDATORY KEYWORD RULE: If your analysis text contains ANY of the following "
    "phrases, the EVIDENCE_LEVEL MUST be NONE or LIMITED — never MODERATE or STRONG:\n"
    "  • insufficient evidence\n"
    "  • limited evidence\n"
    "  • limited direct evidence\n"
    "  • no evidence\n"
    "  • insufficient information\n"
    "  • does not contain\n"
    "\n"
    "Step 2 — Qualitative analysis: Describe the evidence (or lack thereof) found "
    "in the retrieved documents. Do NOT output a numeric score — the numeric score "
    "is computed from EVIDENCE_LEVEL by the system."
)

# Upper bound of each evidence-level score range.
# Confidence and diversity ceilings (applied in Python) cap downward from here.
#   NONE     → 1–2  (upper bound 2)
#   LIMITED  → 3–4  (upper bound 4)
#   MODERATE → 5–7  (upper bound 7)
#   STRONG   → 8–10 (upper bound 10)
_EVIDENCE_LEVEL_SCORES: Dict[str, int] = {
    "NONE": 2,
    "LIMITED": 4,
    "MODERATE": 7,
    "STRONG": 10,
}


# Phrases that signal absent or weak evidence.  If any appear in the analysis
# text, the evidence level is capped at LIMITED regardless of what the LLM tag
# says.  This is a deterministic Python guard — prompt instructions alone are
# not reliable enough to prevent the LLM from tagging MODERATE/STRONG while
# simultaneously using hedging language that contradicts that rating.
_WEAK_EVIDENCE_PHRASES = (
    "insufficient evidence",
    "limited evidence",
    "limited direct evidence",
    "no evidence",
    "insufficient information",
    "no information",
    "does not contain",
    "cannot be assessed",
)


def _parse_evidence_level(analysis_text: str) -> Tuple[str, int]:
    """
    Extract the ``EVIDENCE_LEVEL: <LEVEL>`` tag from the LLM response,
    enforce the keyword override rule, and return ``(level_str, score)``.

    Override rule: if the analysis text contains any phrase from
    ``_WEAK_EVIDENCE_PHRASES`` the level is capped at LIMITED (≤ 4),
    even if the LLM tagged MODERATE or STRONG.

    Falls back to ``("LIMITED", 4)`` with a warning when the tag is absent.
    """
    text_lower = analysis_text.lower()

    match = re.search(
        r"EVIDENCE_LEVEL:\s*(NONE|LIMITED|MODERATE|STRONG)",
        analysis_text,
        re.IGNORECASE,
    )
    level = match.group(1).upper() if match else None

    if level is None:
        log.warning(
            "[moat_analyzer][evidence_level_missing] tag not found — defaulting to LIMITED/4"
        )
        level = "LIMITED"

    # Python-side enforcement of the keyword rule.
    if level in ("MODERATE", "STRONG"):
        triggered = [p for p in _WEAK_EVIDENCE_PHRASES if p in text_lower]
        if triggered:
            log.warning(
                "[moat_analyzer][evidence_level_override] LLM tagged %s but weak-evidence "
                "phrases detected %s — overriding to LIMITED",
                level,
                triggered,
            )
            level = "LIMITED"

    return level, _EVIDENCE_LEVEL_SCORES[level]


@dataclass
class MoatScore:
    """Individual moat type scoring result"""

    name: str
    score: int  # 0-10
    evidence: List[str]
    confidence: str  # "High", "Medium", "Low"
    sources_used: int = 0
    avg_relevance: float = 0.0
    evidence_level: str = "N/A"
    evidence_score: int = 0
    confidence_ceiling: int = 10
    diversity_level: str = "N/A"
    diversity_ceiling: int = 10


@dataclass
class MoatAnalysis:
    """Complete moat analysis result"""

    ticker: str
    overall_score: int  # sum of all moat scores
    moat_strength: str  # "None", "Narrow", "Wide"
    moats: Dict[str, MoatScore]
    competitive_position: str
    recommendation: str
    avg_relevance_score: float = 0.0  # mean relevance across all moat RAG calls
    total_sources_used: int = 0  # total unique source chunks retrieved


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

    def __init__(self):
        self.rag = get_rag_service()

    # ── Business Model ────────────────────────────────────────────────────────

    def analyze_business_model(
        self, ticker: str, top_k: Optional[int] = None, temperature: Optional[float] = None
    ) -> Dict:
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
            top_k=top_k,
            temperature=temperature,
        )

        if result["status"] == "success":
            sources = result.get("sources", [])
            # Ticker guard: verify at least one retrieved chunk belongs to this ticker.
            # Without this, ChromaDB returns the closest chunks from *other* indexed
            # companies when the requested ticker has no documents loaded.
            matching = [
                s
                for s in sources
                if s.get("metadata", {}).get("ticker", "").upper() == ticker.upper()
            ]
            if not matching:
                log.warning(
                    "[moat_analyzer][business_model_wrong_ticker] ticker=%s "
                    "sources=%d matching=0",
                    ticker,
                    len(sources),
                )
                return {
                    "status": "error",
                    "description": (
                        f"No documents indexed for {ticker}. Load SEC filings first."
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
        self,
        ticker: str,
        moat_key: str,
        moat_config: Dict,
        top_k: Optional[int] = None,
        temperature: Optional[float] = None,
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
            scoring_rules=_MOAT_SCORING_INSTRUCTION,
            top_k=top_k,
            temperature=temperature,
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

        analysis_text = result["analysis"]
        evidence = self._extract_evidence(analysis_text, moat_config["indicators"])
        all_sources = result.get("sources", [])

        # Ticker guard: only count chunks that actually belong to this ticker.
        # ChromaDB always returns k results regardless of relevance, so without this
        # check an unindexed ticker gets scored on another company's documents.
        sources = [
            s
            for s in all_sources
            if s.get("metadata", {}).get("ticker", "").upper() == ticker.upper()
        ]
        if not sources:
            log.warning(
                "[moat_analyzer][no_ticker_docs] ticker=%s moat=%s — "
                "no matching chunks, returning score=0",
                ticker,
                moat_key,
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

        # ── Source diversity check ────────────────────────────────────────────
        # Deduplicate by (document_type, year) so that five chunks that all
        # originate from the same filing year of the same doc type only count
        # as one unique origin.  The distinct document_type values in that
        # deduplicated set then determine the diversity tier.
        unique_origins: set = {
            (
                s.get("metadata", {}).get("document_type", "unknown"),
                s.get("metadata", {}).get("year"),
            )
            for s in sources
        }
        unique_doc_types: set = {origin[0] for origin in unique_origins}
        n_doc_types = len(unique_doc_types)
        has_earnings = any("earnings" in dt.lower() for dt in unique_doc_types if dt)

        # Tier definitions (per spec):
        #   High   → 3+ distinct doc types AND includes earnings → cap 10
        #   Medium → 2 distinct doc types (or 3+ without earnings) → cap 8
        #   Low    → single doc type (all same source) → cap 7
        if n_doc_types >= 3 and has_earnings:
            diversity_level, diversity_ceiling = "High", 10
        elif n_doc_types >= 2:
            diversity_level, diversity_ceiling = "Medium", 8
        else:
            diversity_level, diversity_ceiling = "Low", 7

        log.debug(
            "[moat_analyzer][diversity] ticker=%s moat=%s "
            "unique_origins=%d unique_doc_types=%s has_earnings=%s "
            "level=%s diversity_ceiling=%d",
            ticker,
            moat_key,
            len(unique_origins),
            sorted(unique_doc_types),
            has_earnings,
            diversity_level,
            diversity_ceiling,
        )

        # ── Evidence level → base score (LLM structured output) ─────────────
        evidence_level, evidence_score = _parse_evidence_level(analysis_text)
        log.debug(
            "[moat_analyzer][evidence_level] ticker=%s moat=%s level=%s evidence_score=%d",
            ticker,
            moat_key,
            evidence_level,
            evidence_score,
        )

        # ── Confidence ceiling: retrieval quality gate (source count only) ───
        # indicator_count is removed — evidence strength is now expressed by
        # the structured EVIDENCE_LEVEL tag, not keyword frequency in the text.
        if source_count >= 3:
            confidence, ceiling = "High", 10
        elif source_count >= 2:
            confidence, ceiling = "Medium", 8
        else:
            confidence, ceiling = "Low", 5

        # ── Apply both ceilings ───────────────────────────────────────────────
        # confidence gates retrieval quality; diversity gates source breadth.
        # evidence_score is the LLM-assessed strength; ceilings cap downward.
        final_score = min(evidence_score, ceiling, diversity_ceiling)

        log.debug(
            "[moat_analyzer][score] ticker=%s moat=%s evidence_level=%s "
            "evidence_score=%d sources=%d confidence=%s ceiling=%d "
            "diversity=%s diversity_ceiling=%d final_score=%d",
            ticker,
            moat_key,
            evidence_level,
            evidence_score,
            source_count,
            confidence,
            ceiling,
            diversity_level,
            diversity_ceiling,
            final_score,
        )

        return MoatScore(
            name=moat_config["name"],
            score=final_score,
            evidence=evidence[:3],
            confidence=confidence,
            sources_used=source_count,
            avg_relevance=avg_relevance,
            evidence_level=evidence_level,
            evidence_score=evidence_score,
            confidence_ceiling=ceiling,
            diversity_level=diversity_level,
            diversity_ceiling=diversity_ceiling,
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

    def analyze_moats(
        self,
        ticker: str,
        config: Optional[AnalysisConfig] = None,
        top_k: Optional[int] = None,
        temperature: Optional[float] = None,
    ) -> MoatAnalysis:
        log.info(
            "[moat_analyzer][start] ticker=%s pipeline_version=%s",
            ticker,
            PIPELINE_VERSION,
        )

        if config:
            enabled_moats = config.get_enabled_moats()
        else:
            enabled_moats = list(self.MOAT_TYPES.keys())

        moats = {}
        total_score = 0
        relevance_samples: List[float] = []
        total_sources = 0

        valid_moats = {k: self.MOAT_TYPES[k] for k in enabled_moats if k in self.MOAT_TYPES}

        t0 = time.monotonic()
        with ThreadPoolExecutor(max_workers=5) as executor:
            futures = {
                executor.submit(
                    self.analyze_single_moat, ticker, moat_key, moat_config,
                    top_k, temperature
                ): moat_key
                for moat_key, moat_config in valid_moats.items()
            }
            for future in as_completed(futures):
                moat_key = futures[future]
                try:
                    moat_score = future.result()
                except Exception as exc:
                    log.warning(
                        "[moat_analyzer][parallel_error] ticker=%s moat=%s error=%s",
                        ticker, moat_key, exc,
                    )
                    moat_score = MoatScore(
                        name=valid_moats[moat_key]["name"],
                        score=0,
                        evidence=["Analysis failed"],
                        confidence="Low",
                        sources_used=0,
                        avg_relevance=0.0,
                    )
                moats[moat_key] = moat_score
                total_score += moat_score.score
                if moat_score.sources_used > 0:
                    relevance_samples.append(moat_score.avg_relevance)
                    total_sources += moat_score.sources_used

        duration = time.monotonic() - t0
        log.info(
            "[moat_analyzer][parallel_complete] ticker=%s duration=%.1fs moats=%d",
            ticker, duration, len(moats),
        )

        avg_relevance = (
            sum(relevance_samples) / len(relevance_samples)
            if relevance_samples
            else 0.0
        )

        max_possible = len(moats) * 10
        score_pct = (total_score / max_possible * 100) if max_possible > 0 else 0
        moat_strength = self._moat_strength_from_score(int(score_pct))

        competitive_position = self._assess_competitive_position(moats)
        recommendation = self._generate_recommendation(total_score, moat_strength)

        log.info(
            "[moat_analyzer][complete] ticker=%s strength=%s score=%d/%d "
            "pipeline_version=%s",
            ticker,
            moat_strength,
            total_score,
            max_possible,
            PIPELINE_VERSION,
        )

        return MoatAnalysis(
            ticker=ticker,
            overall_score=total_score,
            moat_strength=moat_strength,
            moats=moats,
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

    def _assess_competitive_position(self, moats: Dict[str, MoatScore]) -> str:
        strong = [k for k, v in moats.items() if v.score >= 7]
        moderate = [k for k, v in moats.items() if 4 <= v.score < 7]

        if len(strong) >= 2:
            return "Strong competitive position with multiple durable moats"
        elif len(strong) >= 1 or len(moderate) >= 2:
            return "Moderate competitive position with some protective advantages"
        else:
            return "Weak competitive position with limited moat evidence"

    def _generate_recommendation(self, overall_score: int, moat_strength: str) -> str:
        """
        Returns a moat quality description only.
        Investment decision (BUY/HOLD/PASS) is made exclusively by
        IntegratedAnalyzer._make_decision() based on combined_score.
        """
        if moat_strength == "Wide":
            return "Wide economic moat — strong durable competitive advantages"
        elif moat_strength == "Narrow":
            return "Narrow economic moat — moderate competitive advantages"
        else:
            return "No identifiable economic moat"
