"""
FMP Earnings Call Transcripts Fetcher
Fetches earnings call transcripts for moat analysis with intelligent caching
"""

import re as _re
import requests
import logging
import sys
from pathlib import Path
from typing import Dict, List, Optional

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.api.fmp_api import get_api_key
from backend.cache import get_cache_manager

log = logging.getLogger(__name__)


def _sanitize_for_prompt(text: str) -> str:
    """Redact prompt injection attempts from untrusted document text."""
    patterns = [
        r"ignore previous instructions",
        r"system:",
        r"you are now",
        r"new instructions:",
    ]
    for pattern in patterns:
        if _re.search(pattern, text, _re.IGNORECASE):
            log.warning(
                "[earnings_fetcher][prompt_injection_redacted] pattern='%s'", pattern
            )
            text = _re.sub(pattern, "[REDACTED]", text, flags=_re.IGNORECASE)
    return text


class EarningsTranscriptFetcher:
    """Fetch earnings call transcripts from FMP API"""

    def __init__(self):
        self.api_key = get_api_key()
        self.base_url = "https://financialmodelingprep.com/api/v3"
        self.cache = get_cache_manager()

    def get_latest_transcripts(self, ticker: str, limit: int = 4) -> List[Dict]:
        """
        Get latest earnings call transcripts with caching

        Args:
            ticker: Stock ticker
            limit: Number of transcripts to fetch (default 4 = last 4 quarters)

        Returns:
            List of transcript data
        """
        cache_key = f"{ticker}_earnings_Q{limit}"
        return self.cache.get_or_fetch(
            key=cache_key,
            data_type="earnings",
            fetch_fn=lambda: self._fetch_transcripts_uncached(ticker, limit),
        )

    def _fetch_transcripts_uncached(self, ticker: str, limit: int) -> List[Dict]:
        """
        Fetch transcripts without cache (internal use)

        Args:
            ticker: Stock ticker
            limit: Number of transcripts

        Returns:
            List of transcript data with sanitized content
        """
        log.info("[earnings_fetcher][fetch] ticker=%s limit=%d", ticker, limit)

        url = f"{self.base_url}/earning_call_transcript/{ticker}"
        params = {"apikey": self.api_key, "limit": limit}

        try:
            response = requests.get(url, params=params, timeout=30)
            response.raise_for_status()
            data = response.json()

            if not data:
                log.warning("[earnings_fetcher][no_data] ticker=%s", ticker)
                return []

            if not isinstance(data, list):
                log.warning(
                    "[earnings_fetcher][malformed_response] ticker=%s type=%s body=%s",
                    ticker, type(data).__name__, str(data)[:200],
                )
                return []

            log.info(
                "[earnings_fetcher][fetched] ticker=%s count=%d", ticker, len(data)
            )

            # Sanitize content against prompt injection before caching
            return [
                {**t, "content": _sanitize_for_prompt(t.get("content", ""))}
                for t in data
                if isinstance(t, dict)
            ]

        except requests.exceptions.RequestException as e:
            log.error("[earnings_fetcher][fetch_error] ticker=%s error=%s", ticker, e)
            return []

    def extract_moat_relevant_sections(self, transcript_text: str) -> str:
        """
        Extract sections most relevant for moat analysis

        Args:
            transcript_text: Full transcript text

        Returns:
            Filtered transcript with moat-relevant content
        """
        moat_keywords = [
            "competition",
            "competitive",
            "market share",
            "pricing power",
            "price increase",
            "margin",
            "customer retention",
            "churn",
            "switching cost",
            "brand",
            "loyalty",
            "network effect",
            "platform",
            "ecosystem",
            "moat",
            "advantage",
            "differentiation",
            "proprietary",
            "patent",
            "innovation",
            "r&d",
            "research and development",
            "barrier to entry",
        ]

        lines = transcript_text.split("\n")
        relevant_sections = []

        for i, line in enumerate(lines):
            if any(keyword in line.lower() for keyword in moat_keywords):
                start = max(0, i - 2)
                end = min(len(lines), i + 3)
                context = "\n".join(lines[start:end])
                if context not in relevant_sections:
                    relevant_sections.append(context)

        if relevant_sections:
            filtered_text = "\n\n---\n\n".join(relevant_sections)
            log.info(
                "[earnings_fetcher][filter] filtered=%d original=%d chars",
                len(filtered_text),
                len(transcript_text),
            )
            return filtered_text

        log.info(
            "[earnings_fetcher][filter] no moat sections found, using full transcript"
        )
        return transcript_text[:50000]

    def parse_transcript_metadata(self, transcript: Dict) -> Dict[str, str]:
        """
        Parse transcript metadata from FMP response

        Args:
            transcript: Raw transcript dict from FMP API

        Returns:
            Cleaned metadata dict
        """
        return {
            "ticker": transcript.get("symbol", ""),
            "date": transcript.get("date", ""),
            "quarter": str(transcript.get("quarter", "")),
            "year": str(transcript.get("year", "")),
            "document_type": "earnings_call",
            "source": "FMP",
        }


def fetch_and_prepare_for_rag(
    ticker: str, limit: int = 4, filter_moat_content: bool = True
) -> List[Dict]:
    """
    Fetch earnings transcripts and prepare for RAG ingestion

    Args:
        ticker: Stock ticker
        limit: Number of transcripts (default 4 quarters)
        filter_moat_content: Whether to filter for moat-relevant sections

    Returns:
        List of dicts with 'text' and 'metadata' keys
    """
    if not _re.match(r"^[A-Z]{1,5}$", ticker.strip().upper()):
        raise ValueError(f"Invalid ticker symbol: '{ticker}'")
    ticker = ticker.strip().upper()

    fetcher = EarningsTranscriptFetcher()
    raw_transcripts = fetcher.get_latest_transcripts(ticker, limit)

    if not raw_transcripts:
        log.warning(
            "[earnings_fetcher][no_transcripts] ticker=%s message='No earnings transcripts available for %s'",
            ticker, ticker,
        )
        return []

    documents = []

    for transcript in raw_transcripts:
        content = transcript.get("content", "")

        if not content:
            log.warning(
                "[earnings_fetcher][empty_transcript] ticker=%s quarter=%s year=%s",
                ticker,
                transcript.get("quarter"),
                transcript.get("year"),
            )
            continue

        if filter_moat_content:
            content = fetcher.extract_moat_relevant_sections(content)

        metadata = fetcher.parse_transcript_metadata(transcript)
        documents.append({"text": content, "metadata": metadata})

        log.info(
            "[earnings_fetcher][prepared] ticker=%s quarter=%s year=%s chars=%d",
            ticker,
            metadata["quarter"],
            metadata["year"],
            len(content),
        )

    return documents


if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    docs = fetch_and_prepare_for_rag("AAPL", limit=2)
    log.info("Result: %d documents", len(docs))
