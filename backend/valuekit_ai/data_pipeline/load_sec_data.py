"""
Integration: Load SEC Edgar data into RAG system
"""

from backend.valuekit_ai.data_pipeline.sec_fetcher import fetch_and_prepare_for_rag
from backend.valuekit_ai.data_pipeline.yahoo_news_fetcher import fetch_yahoo_news
from backend.valuekit_ai.data_pipeline.yahoo_info_fetcher import fetch_yahoo_info
from backend.valuekit_ai.rag.rag_service import get_rag_service
from langchain_core.documents import Document

import sys
from pathlib import Path
import logging

log = logging.getLogger(__name__)

root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))


def _delete_ticker_chunks(ticker: str, document_type: str) -> int:
    """
    Delete all existing RAG chunks for a given ticker + document_type.
    Called before reloading a data source to prevent duplicate chunks.

    Returns: number of chunks deleted.
    """
    try:
        rag = get_rag_service()
        col = rag.vector_store.vectorstore._collection
        existing = col.get(
            where={"$and": [{"ticker": {"$eq": ticker}}, {"document_type": {"$eq": document_type}}]},
            include=[],
        )
        n = len(existing["ids"])
        if n > 0:
            col.delete(
                where={"$and": [{"ticker": {"$eq": ticker}}, {"document_type": {"$eq": document_type}}]}
            )
            log.info(
                "[load_sec_data][pre_delete] ticker=%s document_type=%s deleted=%d",
                ticker, document_type, n,
            )
        return n
    except Exception as e:
        log.warning(
            "[load_sec_data][pre_delete_error] ticker=%s document_type=%s error=%s",
            ticker, document_type, e,
        )
        return 0


def load_company_data(ticker: str, years: int = 3) -> dict:
    """
    Fetch the last `years` 10-K filings for `ticker` and load them into the
    RAG vector store.

    Args:
        ticker: Stock ticker
        years:  Number of annual 10-K filings to load (default 3)

    Returns:
        Status dict with keys: status, ticker, years_loaded, documents_added,
        chunks_created, total_kb_size
    """
    log.info(
        "[load_sec_data][start] ticker=%s years=%d", ticker, years
    )

    # Delete existing chunks for this ticker/type before reloading
    _delete_ticker_chunks(ticker, "10-K")

    # Step 1: Fetch SEC documents (multi-year)
    raw_docs = fetch_and_prepare_for_rag(ticker, limit=years)

    if not isinstance(raw_docs, list):
        log.error(
            "[load_sec_data][malformed_response] ticker=%s type=%s",
            ticker, type(raw_docs).__name__,
        )
        return {"status": "error", "message": f"Malformed SEC data returned for {ticker}"}

    if not raw_docs:
        log.warning("[load_sec_data][no_docs] ticker=%s", ticker)
        return {"status": "error", "message": f"No SEC filings found for {ticker}"}

    # Derive which years were actually loaded from metadata
    loaded_years = sorted(
        {doc["metadata"].get("year") for doc in raw_docs if doc["metadata"].get("year")},
        reverse=True,
    )
    log.info(
        "[load_sec_data][fetched] ticker=%s sections=%d years=%s",
        ticker, len(raw_docs), loaded_years,
    )

    # Step 2: Convert to LangChain Documents and load into RAG
    documents = [
        Document(page_content=doc["text"], metadata=doc["metadata"])
        for doc in raw_docs
    ]

    rag = get_rag_service()
    result = rag.add_financial_documents(documents)

    if result["status"] == "success":
        stats = rag.get_knowledge_base_stats()
        log.info(
            "[load_sec_data][complete] ticker=%s years=%s documents=%d chunks=%d kb_total=%d",
            ticker,
            loaded_years,
            result["documents_added"],
            result["chunks_created"],
            stats["count"],
        )
        return {
            "status": "success",
            "ticker": ticker,
            "years_loaded": loaded_years,
            "documents_added": result["documents_added"],
            "chunks_created": result["chunks_created"],
            "total_kb_size": stats["count"],
        }
    else:
        return {"status": "error", "message": result.get("error", "Unknown error")}


def load_news_data(ticker: str, max_articles: int = 10) -> dict:
    """
    Fetch recent Yahoo Finance news articles for `ticker` and load them into
    the RAG vector store.

    Args:
        ticker:       Stock ticker
        max_articles: Maximum number of articles to load (default 10)

    Returns:
        Status dict with keys: status, ticker, documents_added, chunks_created,
        total_kb_size
    """
    log.info("[load_sec_data][news_start] ticker=%s max_articles=%d", ticker, max_articles)

    # Delete existing chunks for this ticker/type before reloading
    _delete_ticker_chunks(ticker, "news_article")

    raw_docs = fetch_yahoo_news(ticker, max_articles=max_articles)

    if not isinstance(raw_docs, list):
        log.error(
            "[load_sec_data][news_malformed_response] ticker=%s type=%s",
            ticker, type(raw_docs).__name__,
        )
        return {"status": "error", "message": f"Malformed news data returned for {ticker}"}

    if not raw_docs:
        log.warning("[load_sec_data][news_no_docs] ticker=%s", ticker)
        return {"status": "error", "message": f"No news articles found for {ticker}"}

    log.info(
        "[load_sec_data][news_fetched] ticker=%s articles=%d",
        ticker, len(raw_docs),
    )

    documents = [
        Document(page_content=doc["text"], metadata=doc["metadata"])
        for doc in raw_docs
    ]

    rag = get_rag_service()
    result = rag.add_financial_documents(documents)

    if result["status"] == "success":
        stats = rag.get_knowledge_base_stats()
        log.info(
            "[load_sec_data][news_complete] ticker=%s documents=%d chunks=%d kb_total=%d",
            ticker,
            result["documents_added"],
            result["chunks_created"],
            stats["count"],
        )
        return {
            "status": "success",
            "ticker": ticker,
            "documents_added": result["documents_added"],
            "chunks_created": result["chunks_created"],
            "total_kb_size": stats["count"],
        }
    else:
        return {"status": "error", "message": result.get("error", "Unknown error")}


def load_yahoo_info_data(ticker: str) -> dict:
    """
    Fetch Yahoo Finance company metadata for `ticker` and load it into
    the RAG vector store as a structured profile document.

    Args:
        ticker: Stock ticker

    Returns:
        Status dict with keys: status, ticker, documents_added, chunks_created,
        total_kb_size
    """
    log.info("[load_sec_data][yahoo_info_start] ticker=%s", ticker)

    # Delete existing chunks for this ticker/type before reloading
    _delete_ticker_chunks(ticker, "company_info")

    info = fetch_yahoo_info(ticker)
    if not isinstance(info, dict):
        log.error(
            "[load_sec_data][yahoo_info_malformed] ticker=%s type=%s",
            ticker, type(info).__name__,
        )
        return {"status": "error", "message": f"Malformed company info returned for {ticker}"}

    if not info:
        log.warning("[load_sec_data][yahoo_info_no_data] ticker=%s", ticker)
        return {"status": "error", "message": f"No company info found for {ticker}"}

    # Format the metadata dict as a structured text document for RAG ingestion
    _field_labels = {
        "sector": "Sector",
        "industry": "Industry",
        "fullTimeEmployees": "Full-Time Employees",
        "marketCap": "Market Cap",
        "trailingPE": "Trailing P/E",
        "returnOnEquity": "Return on Equity",
        "grossMargins": "Gross Margins",
        "longBusinessSummary": "Business Summary",
    }
    lines = [f"Company Profile: {ticker}"]
    for field, label in _field_labels.items():
        value = info.get(field)
        if value is not None:
            lines.append(f"{label}: {value}")

    text = "\n".join(lines)
    metadata = {
        "ticker": ticker,
        "document_type": "company_info",
        "source": "yahoo_finance",
    }

    documents = [Document(page_content=text, metadata=metadata)]
    rag = get_rag_service()
    result = rag.add_financial_documents(documents)

    if result["status"] == "success":
        stats = rag.get_knowledge_base_stats()
        log.info(
            "[load_sec_data][yahoo_info_complete] ticker=%s documents=%d chunks=%d kb_total=%d",
            ticker,
            result["documents_added"],
            result["chunks_created"],
            stats["count"],
        )
        return {
            "status": "success",
            "ticker": ticker,
            "documents_added": result["documents_added"],
            "chunks_created": result["chunks_created"],
            "total_kb_size": stats["count"],
        }
    else:
        return {"status": "error", "message": result.get("error", "Unknown error")}


def analyze_company(ticker: str, quantitative_data: dict = None) -> dict:
    """
    Full analysis: SEC data + quantitative metrics

    Args:
        ticker: Stock ticker
        quantitative_data: Your ValueKit calculations

    Returns:
        Analysis results
    """
    log.info(f"\n🔍 Analyzing {ticker}...\n")

    # Load company data first
    load_result = load_company_data(ticker)

    if load_result["status"] != "success":
        return load_result

    # Sample quantitative data if not provided
    if not quantitative_data:
        quantitative_data = {
            "dcf": {
                "intrinsic_value": 195.50,
                "current_price": 175.20,
                "upside": "11.6%",
            },
            "roic": "45.2%",
            "margin_of_safety": "11.6%",
        }

    # Perform RAG analysis
    log.info("\n📈 Running investment analysis...")
    rag = get_rag_service()

    analysis = rag.analyze_with_rag(
        query=f"Based on quantitative metrics and SEC filings, should I invest in {ticker}? Focus on identifying any red flags or moat characteristics.",
        quantitative_data=quantitative_data,
    )

    if analysis["status"] == "success":
        log.info("\n" + "=" * 60)
        log.info("INVESTMENT ANALYSIS")
        log.info("=" * 60)
        log.info(f"\nCompany: {ticker}")
        log.info(f"\nQuantitative Metrics:")
        log.info(f"  - Margin of Safety: {quantitative_data['margin_of_safety']}")
        log.info(f"  - ROIC: {quantitative_data['roic']}")
        log.info(f"  - DCF Upside: {quantitative_data['dcf']['upside']}")
        log.info(f"\n{analysis['analysis']}")
        log.info("\n" + "=" * 60)
        return {
            "status": "success",
            "ticker": ticker,
            "analysis": analysis["analysis"],
            "sources_used": len(analysis["sources"]),
        }
    else:
        return analysis


if __name__ == "__main__":
    # Example usage
    ticker = "AAPL"

    # Option 1: Just load data
    # load_company_data(ticker)

    # Option 2: Full analysis
    result = analyze_company(ticker)

    log.info(f"\n✅ Analysis complete!")
