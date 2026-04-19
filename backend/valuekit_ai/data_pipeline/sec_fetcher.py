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
from backend.valuekit_ai.utils.sanitize import _sanitize_for_prompt

log = logging.getLogger(__name__)


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

    # ── Year helpers ──────────────────────────────────────────────────────────

    @staticmethod
    def _parse_filing_year(dir_name: str) -> Optional[int]:
        """
        Extract 4-digit filing year from a SEC accession-number directory name.

        Accession number format: {10-digit-CIK}-{2-digit-year}-{6-digit-seq}
        Example: "0000320193-24-000123"  →  2024

        Returns None if the format does not match.
        """
        parts = dir_name.split("-")
        if len(parts) >= 2 and parts[1].isdigit() and len(parts[1]) == 2:
            return 2000 + int(parts[1])
        return None

    def _pick_main_file(self, filing_dir: Path) -> Optional[Path]:
        """Return the best 10-K file from a filing directory."""
        txt_files = list(filing_dir.glob("*.txt"))
        if not txt_files:
            txt_files = list(filing_dir.glob("*.htm*"))
        if not txt_files:
            log.warning(
                "[sec_fetcher][no_files] dir=%s files=%s",
                filing_dir.name,
                [f.name for f in filing_dir.glob("*")],
            )
            return None
        # Prefer full-submission.txt; fallback to largest file
        main_file = next(
            (f for f in txt_files if "full-submission" in f.name.lower()), None
        )
        return main_file or max(txt_files, key=lambda f: f.stat().st_size)

    def _get_10k_files(
        self, ticker: str, limit: int
    ) -> List[tuple]:
        """
        Download up to `limit` most-recent 10-K filings for `ticker`.

        Returns:
            List of (file_path, year) tuples, newest filing first.
            `year` is the 4-digit integer derived from the accession number,
            or None if parsing fails.
        """
        try:
            log.info("[sec_fetcher][download] ticker=%s limit=%d", ticker, limit)
            self.dl.get("10-K", ticker, limit=limit)

            search_dir = self.data_dir / "sec-edgar-filings" / ticker / "10-K"
            if not search_dir.exists():
                log.error("[sec_fetcher][dir_not_found] path=%s", search_dir)
                return []

            filing_dirs = sorted(search_dir.iterdir(), reverse=True)[:limit]
            if not filing_dirs:
                log.error("[sec_fetcher][no_filings] ticker=%s", ticker)
                return []

            results = []
            for filing_dir in filing_dirs:
                year = self._parse_filing_year(filing_dir.name)
                main_file = self._pick_main_file(filing_dir)
                if main_file is None:
                    log.warning(
                        "[sec_fetcher][skip_dir] ticker=%s dir=%s reason=no_file",
                        ticker, filing_dir.name,
                    )
                    continue
                log.info(
                    "[sec_fetcher][file_selected] ticker=%s dir=%s year=%s file=%s",
                    ticker, filing_dir.name, year, main_file.name,
                )
                results.append((main_file, year))

            return results

        except Exception as e:
            log.error("[sec_fetcher][download_error] ticker=%s error=%s", ticker, e)
            return []

    # ── Section extraction helpers ────────────────────────────────────────────

    @staticmethod
    def _extract_item_section(
        text: str,
        start_pattern: str,
        end_pattern: str,
        max_chars: int = 8000,
    ) -> Optional[str]:
        """
        Extract a named 10-K section using case-insensitive regex boundaries.

        The start match is included in the returned text; the end match is
        excluded.  Searching for the end boundary begins immediately after
        the end of the start match to prevent self-overlap (e.g. the Item 1A
        start pattern `item\s+1a` would otherwise match inside its own end
        boundary search for `item\s+1[\.\s]`).

        Args:
            text:          Full filing text (original case, not uppercased).
            start_pattern: Regex marking the beginning of the section.
            end_pattern:   Regex marking where the section ends (exclusive).
            max_chars:     Hard truncation limit for the extracted text.

        Returns:
            Extracted section string, or None if the start pattern is not found.
        """
        start_match = _re.search(start_pattern, text, _re.IGNORECASE)
        if not start_match:
            return None

        start_pos  = start_match.start()
        search_from = start_pos + len(start_match.group())

        end_match = _re.search(end_pattern, text[search_from:], _re.IGNORECASE)
        end_pos   = (search_from + end_match.start()) if end_match else len(text)

        section = text[start_pos:end_pos].strip()
        if not section:
            return None

        if len(section) > max_chars:
            section = section[:max_chars] + "\n\n[Section truncated at 8 000 characters]"

        return section

    # ── Single-filing parser ───────────────────────────────────────────────────

    def _fetch_single_10k(
        self, ticker: str, file_path: Path, year: Optional[int]
    ) -> Optional[Dict]:
        """
        Parse one 10-K file into a sections dict.

        Returns:
            Dict with keys: ticker, year, filing_date, file_path, sections
            or None if text extraction fails.
        """
        full_text = self.extract_text_from_html(file_path)
        if not full_text:
            return None

        log.info(
            "[sec_fetcher][extracted] ticker=%s year=%s chars=%d",
            ticker, year, len(full_text),
        )

        raw_sections: Dict[str, Optional[str]] = {
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
            # Item 1 and Item 1A extracted with the exact boundary patterns
            # requested: case-insensitive, [\.\s] separator, 8 000-char cap.
            # These use _extract_item_section (re.IGNORECASE on original text)
            # rather than extract_section (UPPERCASE text) so the patterns
            # match more consistently across filing formats.
            "item1": self._extract_item_section(
                full_text,
                start_pattern=r"item\s+1[\.\s]",
                end_pattern=r"item\s+1a[\.\s]",
                max_chars=8000,
            ),
            "item1a": self._extract_item_section(
                full_text,
                start_pattern=r"item\s+1a[\.\s]",
                end_pattern=r"item\s+2[\.\s]",
                max_chars=8000,
            ),
        }

        # DEBUG: log hit/miss for every section key
        for key, text_val in raw_sections.items():
            if text_val:
                log.debug(
                    "[sec_fetcher][section_ok] ticker=%s year=%s section=%s chars=%d",
                    ticker, year, key, len(text_val),
                )
            else:
                log.debug(
                    "[sec_fetcher][section_miss] ticker=%s year=%s section=%s",
                    ticker, year, key,
                )

        sections = {k: _sanitize_for_prompt(v) for k, v in raw_sections.items() if v}

        filing_date = (
            file_path.parent.name.split("-")[0]
            if "-" in file_path.parent.name
            else "unknown"
        )

        log.info(
            "[sec_fetcher][sections_found] ticker=%s year=%s sections=%s",
            ticker, year, list(sections.keys()),
        )

        return {
            "ticker": ticker,
            "year": year,
            "filing_date": filing_date,
            "file_path": str(file_path),
            "sections": sections,
        }

    # ── Multi-year public API ──────────────────────────────────────────────────

    def get_10k_multi_year(self, ticker: str, limit: int = 3) -> List[Dict]:
        """
        Return up to `limit` most-recent 10-K filings for `ticker`.

        Each filing is cached individually under the key
        ``{ticker}_10K_{year}`` so re-runs skip already-parsed files.

        Returns:
            List of filing dicts (newest first).  Each dict contains
            ``ticker``, ``year``, ``filing_date``, ``file_path``,
            and ``sections``.
        """
        file_list = self._get_10k_files(ticker, limit)

        results = []
        for file_path, year in file_list:
            # Use year-scoped cache key; fall back to dir name if year unknown
            cache_key = (
                f"{ticker}_10K_{year}" if year is not None
                else f"{ticker}_10K_{file_path.parent.name}"
            )
            filing_data = self.cache.get_or_fetch(
                key=cache_key,
                data_type="sec_10k",
                fetch_fn=lambda fp=file_path, y=year: self._fetch_single_10k(
                    ticker, fp, y
                ),
            )
            if filing_data:
                results.append(filing_data)

        log.info(
            "[sec_fetcher][multi_year] ticker=%s requested=%d loaded=%d years=%s",
            ticker,
            limit,
            len(results),
            [r.get("year") for r in results],
        )
        return results

    # ── Legacy single-filing methods (kept for backward compatibility) ─────────

    def get_latest_10k_file(self, ticker: str) -> Optional[Path]:
        """Return the file path of the single latest 10-K (legacy helper)."""
        files = self._get_10k_files(ticker, limit=1)
        return files[0][0] if files else None

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


def fetch_and_prepare_for_rag(ticker: str, limit: int = 3) -> List[Dict]:
    """
    Fetch the last `limit` 10-K filings for `ticker` and prepare them for
    RAG ingestion.

    Args:
        ticker: Stock ticker (validated: 1–5 uppercase letters)
        limit:  Number of annual filings to load (default 3 → 2022/23/24)

    Returns:
        List of dicts with 'text' and 'metadata' keys, one entry per
        (filing × section).  Metadata includes a 'year' field derived from
        the SEC accession number.
    """
    if not _re.match(r"^[A-Z]{1,5}$", ticker.strip().upper()):
        raise ValueError(f"Invalid ticker symbol: '{ticker}'")
    ticker = ticker.strip().upper()

    fetcher = SECEdgarFetcher()

    filings = fetcher.get_10k_multi_year(ticker, limit=limit)
    if not filings:
        return []

    section_names = {
        "business":     "Business Description",
        "risk_factors": "Risk Factors",
        "mda":          "Management Discussion & Analysis",
        "item1":        "Item 1 — Business",
        "item1a":       "Item 1A — Risk Factors",
    }

    documents = []
    for filing_data in filings:
        year = filing_data.get("year")
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
                            "year": year,
                        },
                    }
                )

    log.info(
        "[sec_fetcher][prepared] ticker=%s filings=%d documents=%d years=%s",
        ticker,
        len(filings),
        len(documents),
        [f.get("year") for f in filings],
    )
    return documents


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.DEBUG,
        format="[%(asctime)s][%(name)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    docs = fetch_and_prepare_for_rag("AAPL")
    log.info("Result: %d documents", len(docs))
