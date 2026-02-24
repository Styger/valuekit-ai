"""
Index 10 companies into ChromaDB for WIPRO demonstration.
Run once to populate the vector store cache.

Usage:
    python scripts/index_companies.py
"""

import logging
import sys
from pathlib import Path

root_dir = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.data_pipeline.load_sec_data import load_company_data
from backend.valuekit_ai.config.config import PIPELINE_VERSION

logging.basicConfig(
    level=logging.INFO,
    format="[%(asctime)s][%(name)s] %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
log = logging.getLogger(__name__)

# WIPRO Requirement: minimum 10 companies indexed
# Selection covers all 5 moat types for representative evaluation
COMPANIES = [
    "AAPL",  # Apple       — Brand Power, Switching Costs
    "MSFT",  # Microsoft   — Switching Costs, Network Effects
    "GOOGL",  # Alphabet    — Network Effects, Efficient Scale
    "UNH",  # UnitedHealth — Cost Advantages
    "JNJ",  # J&J         — Brand Power, Switching Costs
    "V",  # Visa        — Network Effects, Efficient Scale
    "MA",  # Mastercard  — Network Effects
    "KO",  # Coca-Cola   — Brand Power
    "NVDA",  # Nvidia      — Cost Advantages, Efficient Scale
    "AMZN",  # Amazon      — Cost Advantages, Network Effects
]


def main():
    log.info(
        "[index_companies][start] pipeline_version=%s companies=%d",
        PIPELINE_VERSION,
        len(COMPANIES),
    )

    results = {"success": [], "failed": []}

    for ticker in COMPANIES:
        log.info("[index_companies][indexing] ticker=%s", ticker)
        try:
            result = load_company_data(ticker)
            if result.get("status") == "success":
                results["success"].append(ticker)
                log.info(
                    "[index_companies][success] ticker=%s chunks=%s",
                    ticker,
                    result.get("chunks_indexed", "N/A"),
                )
            else:
                results["failed"].append(ticker)
                log.warning(
                    "[index_companies][failed] ticker=%s reason=%s",
                    ticker,
                    result.get("message", "unknown"),
                )
        except Exception as e:
            results["failed"].append(ticker)
            log.error("[index_companies][error] ticker=%s error=%s", ticker, e)

    log.info(
        "[index_companies][complete] success=%d failed=%d pipeline_version=%s",
        len(results["success"]),
        len(results["failed"]),
        PIPELINE_VERSION,
    )
    log.info("[index_companies][success_list] %s", results["success"])
    if results["failed"]:
        log.warning("[index_companies][failed_list] %s", results["failed"])

    print(f"\n✅ Indexed:  {results['success']}")
    if results["failed"]:
        print(f"❌ Failed:   {results['failed']}")
    print(
        f"\nTotal: {len(results['success'])}/{len(COMPANIES)} companies indexed "
        f"(pipeline_version={PIPELINE_VERSION})"
    )


if __name__ == "__main__":
    main()
