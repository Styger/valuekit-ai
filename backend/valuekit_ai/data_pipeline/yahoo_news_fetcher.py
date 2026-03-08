"""
Yahoo Finance News Fetcher - Using yfinance
Fetches recent news article summaries for moat analysis
"""

import re as _re
import logging
import sys
from pathlib import Path
from typing import Dict, List

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

log = logging.getLogger(__name__)


def _sanitize_for_prompt(text: str) -> str:
    """Redact prompt injection attempts from untrusted news text."""
    patterns = [
        r"ignore previous instructions",
        r"system:",
        r"you are now",
        r"new instructions:",
    ]
    for pattern in patterns:
        if _re.search(pattern, text, _re.IGNORECASE):
            log.warning(
                "[yahoo_news_fetcher][prompt_injection_redacted] pattern='%s'", pattern
            )
            text = _re.sub(pattern, "[REDACTED]", text, flags=_re.IGNORECASE)
    return text


def fetch_yahoo_news(ticker: str, max_articles: int = 10) -> List[Dict]:
    """
    Fetch recent news article summaries for a ticker via yfinance.

    Each returned dict has 'text' and 'metadata' keys suitable for RAG ingestion.
    Articles with no usable text are silently skipped.

    Args:
        ticker:       Stock ticker (1–5 uppercase letters).
        max_articles: Maximum number of articles to include (default 10).

    Returns:
        List of dicts with 'text' and 'metadata' keys.
    """
    if not _re.match(r"^[A-Z]{1,5}$", ticker.strip().upper()):
        raise ValueError(f"Invalid ticker symbol: '{ticker}'")
    ticker = ticker.strip().upper()

    try:
        import yfinance as yf
    except ImportError:
        log.error("[yahoo_news_fetcher][import_error] yfinance is not installed")
        return []

    log.info("[yahoo_news_fetcher][fetch] ticker=%s max_articles=%d", ticker, max_articles)

    try:
        raw_news = yf.Ticker(ticker).news or []
    except Exception as e:
        log.error("[yahoo_news_fetcher][fetch_error] ticker=%s error=%s", ticker, e)
        return []

    if not raw_news:
        log.warning("[yahoo_news_fetcher][no_data] ticker=%s", ticker)
        return []

    documents = []
    for article in raw_news[:max_articles]:
        # yfinance returns a dict; field names vary slightly across versions
        content = article.get("content") or article.get("summary") or ""
        title = article.get("title") or ""

        # Combine title + content for richer retrieval context
        text = f"{title}\n\n{content}".strip() if content else title.strip()
        if not text:
            log.debug(
                "[yahoo_news_fetcher][skip_empty] ticker=%s title=%s", ticker, title
            )
            continue

        text = _sanitize_for_prompt(text)

        pub_date = article.get("providerPublishTime") or article.get("pubDate") or ""
        # Convert Unix timestamp to ISO date string if numeric
        if isinstance(pub_date, (int, float)) and pub_date > 0:
            from datetime import datetime, timezone
            pub_date = datetime.fromtimestamp(pub_date, tz=timezone.utc).strftime(
                "%Y-%m-%d"
            )

        metadata = {
            "ticker": ticker,
            "document_type": "news_article",
            "source": "yahoo_finance",
            "title": title[:200],
            "date": str(pub_date),
        }
        documents.append({"text": text, "metadata": metadata})
        log.debug(
            "[yahoo_news_fetcher][prepared] ticker=%s title=%s chars=%d",
            ticker,
            title[:60],
            len(text),
        )

    log.info(
        "[yahoo_news_fetcher][complete] ticker=%s fetched=%d prepared=%d",
        ticker,
        len(raw_news),
        len(documents),
    )
    return documents


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    docs = fetch_yahoo_news("AAPL", max_articles=5)
    log.info("Result: %d documents", len(docs))
    for d in docs:
        log.info("  title=%s chars=%d", d["metadata"]["title"][:60], len(d["text"]))
