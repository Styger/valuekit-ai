"""
Integration: Load Earnings Call Transcripts into RAG system
Follows same pattern as load_sec_data.py
"""

from backend.valuekit_ai.data_pipeline.earnings_fetcher import fetch_and_prepare_for_rag
from backend.valuekit_ai.data_pipeline.load_sec_data import _delete_ticker_chunks
from backend.valuekit_ai.rag.rag_service import get_rag_service
from langchain_core.documents import Document

import sys
from pathlib import Path

import logging

log = logging.getLogger(__name__)

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


def load_earnings_data(
    ticker: str, quarters: int = 4, filter_moat_content: bool = True
) -> dict:
    """
    Fetch earnings transcripts and load into RAG system

    Args:
        ticker: Stock ticker
        quarters: Number of quarters to fetch (default 4 = 1 year)
        filter_moat_content: Whether to filter for moat-relevant sections

    Returns:
        Status dict
    """
    log.info(f"📞 Loading earnings data for {ticker}...")

    # Delete existing chunks for this ticker/type before reloading
    _delete_ticker_chunks(ticker, "earnings_call")

    # Step 1: Fetch earnings transcripts
    log.info(f"  → Fetching {quarters} most recent earnings calls...")

    raw_docs = fetch_and_prepare_for_rag(ticker, quarters, filter_moat_content)

    if not raw_docs:
        return {
            "status": "error",
            "message": f"No earnings transcripts found for {ticker}",
        }

    # Convert to LangChain Document format
    documents = []
    for doc in raw_docs:
        langchain_doc = Document(page_content=doc["text"], metadata=doc["metadata"])
        documents.append(langchain_doc)

    log.info(f"  ✅ Found {len(documents)} transcript(s)")

    # Step 2: Load into RAG
    log.info("  → Loading into RAG system...")
    rag = get_rag_service()
    result = rag.add_financial_documents(documents)

    if result["status"] == "success":
        log.info(
            f"  ✅ Added {result['documents_added']} transcripts, created {result['chunks_created']} chunks"
        )

        # Step 3: Get stats
        stats = rag.get_knowledge_base_stats()
        log.info(f"  📚 Total documents in knowledge base: {stats['count']}")

        return {
            "status": "success",
            "ticker": ticker,
            "documents_added": result["documents_added"],
            "chunks_created": result["chunks_created"],
            "total_kb_size": stats["count"],
        }
    else:
        return {"status": "error", "message": result.get("error", "Unknown error")}


def load_combined_data(ticker: str) -> dict:
    """
    Load both SEC filings AND earnings transcripts

    Args:
        ticker: Stock ticker

    Returns:
        Combined status dict
    """
    from backend.valuekit_ai.data_pipeline.load_sec_data import load_company_data

    log.info(f"\n{'=' * 70}")
    log.info(f"🎯 Loading COMPLETE dataset for {ticker}")
    log.info(f"{'=' * 70}\n")

    results = {}

    # Load SEC data
    log.info("📊 Step 1: SEC 10-K Filings")
    log.info("-" * 70)
    sec_result = load_company_data(ticker)
    results["sec"] = sec_result

    # Load Earnings data
    log.info("\n📞 Step 2: Earnings Call Transcripts")
    log.info("-" * 70)
    earnings_result = load_earnings_data(ticker, quarters=4)
    results["earnings"] = earnings_result

    # Summary
    log.info(f"\n{'=' * 70}")
    log.info("SUMMARY")
    log.info(f"{'=' * 70}")

    total_docs = 0
    total_chunks = 0

    if sec_result["status"] == "success":
        log.info(f"✅ SEC Filings: {sec_result['documents_added']} documents")
        total_docs += sec_result["documents_added"]
        total_chunks += sec_result["chunks_created"]

    if earnings_result["status"] == "success":
        log.info(f"✅ Earnings Calls: {earnings_result['documents_added']} transcripts")
        total_docs += earnings_result["documents_added"]
        total_chunks += earnings_result["chunks_created"]

    log.info(f"\n📚 Total: {total_docs} documents → {total_chunks} chunks in RAG")
    log.info(f"{'=' * 70}\n")

    return {
        "status": "success" if total_docs > 0 else "error",
        "ticker": ticker,
        "total_documents": total_docs,
        "total_chunks": total_chunks,
        "breakdown": results,
    }


if __name__ == "__main__":
    # Example usage
    ticker = "AAPL"

    # Option 1: Load only earnings
    # result = load_earnings_data(ticker)

    # Option 2: Load SEC + Earnings (recommended)
    result = load_combined_data(ticker)

    log.info(f"✅ Data loading complete!")
