"""
SEC Edgar Data Fetcher - Using sec-edgar-downloader library
Reliable way to fetch 10-K filings with intelligent caching
"""

import re as _re
import os
import logging
import shutil
import sys
from pathlib import Path
from typing import Dict, List, Optional

from sec_edgar_downloader import Downloader
from bs4 import BeautifulSoup

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

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
                "[sec_fetcher][prompt_injection_redacted] pattern='%s'", pattern
            )
            text = _re.sub(pattern, "[REDACTED]", text, flags=_re.IGNORECASE)
    return text


class SECEdgarFetcher:
    """Fetch financial documents from SEC Edgar using reliable library"""

    def __init__(
        self, company_name: str = "ValueKit", email: str = "jonas@valuekit.com"
    ):
        self.original_dir = Path.cwd()
        self.data_dir = (
            Path(__file__).resolve().parent.parent.parent.parent
            / "data"
            / "sec-filings"
        )
        self.data_dir.mkdir(parents=True, exist_ok=True)
        log.info("[sec_fetcher][init] data_dir=%s", self.data_dir)

        self.dl = Downloader(company_name, email, self.data_dir)
        self.temp_dir = Path("./sec_temp")
        self.cache = get_cache_manager()

    def cleanup_temp(self):
        """Remove temporary download directory"""
        if self.temp_dir.exists():
            shutil.rmtree(self.temp_dir)

    def get_latest_10k_file(self, ticker: str) -> Optional[Path]:
        """
        Download latest 10-K and return file path

        Args:
            ticker: Stock ticker

        Returns:
            Path to downloaded file or None
        """
        try:
            log.info("[sec_fetcher][download] ticker=%s", ticker)
            self.dl.get("10-K", ticker, limit=1)

            search_dir = self.data_dir / "sec-edgar-filings" / ticker / "10-K"

            if not search_dir.exists():
                log.error("[sec_fetcher][dir_not_found] path=%s", search_dir)
                return None

            filing_dirs = sorted(search_dir.iterdir(), reverse=True)
            if not filing_dirs:
                log.error("[sec_fetcher][no_filings] ticker=%s", ticker)
                return None

            latest_dir = filing_dirs[0]
            log.info("[sec_fetcher][latest_dir] dir=%s", latest_dir.name)

            txt_files = list(latest_dir.glob("*.txt"))
            if not txt_files:
                html_files = list(latest_dir.glob("*.htm*"))
                if not html_files:
                    log.error(
                        "[sec_fetcher][no_files] dir=%s files=%s",
                        latest_dir,
                        [f.name for f in latest_dir.glob("*")],
                    )
                    return None
                txt_files = html_files

            # Prefer full-submission.txt; fallback to largest file
            main_file = next(
                (f for f in txt_files if "full-submission" in f.name.lower()), None
            )
            if not main_file:
                main_file = max(txt_files, key=lambda f: f.stat().st_size)

            log.info("[sec_fetcher][file_selected] file=%s", main_file.name)
            return main_file

        except Exception as e:
            log.error("[sec_fetcher][download_error] ticker=%s error=%s", ticker, e)
            return None

    def extract_text_from_html(self, file_path: Path) -> Optional[str]:
        """
        Extract plain text from HTML/TXT SEC filing

        Args:
            file_path: Path to filing file

        Returns:
            Extracted text or None
        """
        try:
            content = file_path.read_text(encoding="utf-8", errors="ignore")

            if file_path.suffix.lower() in [".html", ".htm"]:
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text(separator="\n")
            else:
                # Plain text / full-submission.txt
                soup = BeautifulSoup(content, "html.parser")
                for tag in soup(["script", "style"]):
                    tag.decompose()
                text = soup.get_text(separator="\n")

            # Collapse whitespace
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            return "\n".join(lines)

        except Exception as e:
            log.error("[sec_fetcher][extract_error] file=%s error=%s", file_path, e)
            return None

    def extract_section(
        self,
        text: str,
        start_patterns: List[str],
        end_patterns: List[str],
    ) -> Optional[str]:
        """
        Extract a section from filing text using regex patterns

        Args:
            text: Full filing text
            start_patterns: Regex patterns marking section start
            end_patterns: Regex patterns marking section end

        Returns:
            Extracted section text or None
        """
        text_upper = text.upper()
        start_pos = -1

        for pattern in start_patterns:
            match = _re.search(pattern, text_upper)
            if match:
                start_pos = match.start()
                break

        if start_pos == -1:
            return None

        end_pos = len(text)
        for pattern in end_patterns:
            match = _re.search(pattern, text_upper[start_pos + 100 :])
            if match:
                end_pos = start_pos + 100 + match.start()
                break

        section = text[start_pos:end_pos]

        if len(section) > 20000:
            section = section[:20000] + "\n\n[Section truncated for length]"

        return section

    def get_latest_10k(self, ticker: str) -> Optional[Dict]:
        """
        Get latest 10-K with caching

        Args:
            ticker: Stock ticker

        Returns:
            Dict with sections or None
        """
        cache_key = f"{ticker}_10K_latest"
        return self.cache.get_or_fetch(
            key=cache_key,
            data_type="sec_10k",
            fetch_fn=lambda: self._fetch_10k_uncached(ticker),
        )

    def _fetch_10k_uncached(self, ticker: str) -> Optional[Dict]:
        """
        Fetch and parse 10-K without cache (internal use)

        Args:
            ticker: Stock ticker

        Returns:
            Dict with parsed sections or None
        """
        log.info("[sec_fetcher][fetch] ticker=%s", ticker)

        file_path = self.get_latest_10k_file(ticker)
        if not file_path:
            return None

        log.info("[sec_fetcher][downloaded] file=%s", file_path.name)

        full_text = self.extract_text_from_html(file_path)
        if not full_text:
            return None

        log.info("[sec_fetcher][extracted] chars=%d", len(full_text))

        sections = {
            "business": self.extract_section(
                full_text,
                [r"ITEM\s+1[\.\:\-\s]+BUSINESS", r"ITEM\s+1\b(?!\s*A)"],
                [r"ITEM\s+1A", r"ITEM\s+2\b"],
            ),
            "risk_factors": self.extract_section(
                full_text,
                [r"ITEM\s+1A[\.\:\-\s]+RISK\s+FACTORS", r"ITEM\s+1A\b"],
                [r"ITEM\s+1B", r"ITEM\s+2\b"],
            ),
            "mda": self.extract_section(
                full_text,
                [r"ITEM\s+7[\.\:\-\s]+MANAGEMENT", r"ITEM\s+7\b(?!\s*A)"],
                [r"ITEM\s+7A", r"ITEM\s+8\b"],
            ),
        }

        # Sanitize all sections against prompt injection before caching
        sections = {k: _sanitize_for_prompt(v) for k, v in sections.items() if v}

        found = list(sections.keys())
        log.info("[sec_fetcher][sections_found] ticker=%s sections=%s", ticker, found)

        filing_date = (
            file_path.parent.name.split("-")[0]
            if "-" in file_path.parent.name
            else "unknown"
        )

        return {
            "ticker": ticker,
            "filing_date": filing_date,
            "file_path": str(file_path),
            "sections": sections,
        }


def fetch_and_prepare_for_rag(ticker: str) -> List[Dict]:
    """
    Fetch SEC data and prepare for RAG ingestion

    Args:
        ticker: Stock ticker

    Returns:
        List of dicts with 'text' and 'metadata' keys
    """
    if not _re.match(r"^[A-Z]{1,5}$", ticker.strip().upper()):
        raise ValueError(f"Invalid ticker symbol: '{ticker}'")
    ticker = ticker.strip().upper()

    fetcher = SECEdgarFetcher()

    try:
        filing_data = fetcher.get_latest_10k(ticker)
        if not filing_data:
            return []

        section_names = {
            "business": "Business Description",
            "risk_factors": "Risk Factors",
            "mda": "Management Discussion & Analysis",
        }

        documents = []
        for section_key, section_text in filing_data["sections"].items():
            if section_text:
                documents.append(
                    {
                        "text": section_text,
                        "metadata": {
                            "company": ticker,
                            "ticker": ticker,
                            "document_type": "10-K",
                            "section": section_key,
                            "section_name": section_names.get(section_key, section_key),
                            "date": filing_data["filing_date"],
                        },
                    }
                )

        log.info(
            "[sec_fetcher][prepared] ticker=%s documents=%d", ticker, len(documents)
        )
        return documents

    finally:
        pass


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    docs = fetch_and_prepare_for_rag("AAPL")
    log.info("Result: %d documents", len(docs))
