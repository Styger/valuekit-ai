"""
RAG Service - Main analysis service combining retrieval with Claude
"""

import logging
import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

from anthropic import Anthropic
from langchain_core.documents import Document

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import RAGConfig, PIPELINE_VERSION
from backend.valuekit_ai.rag.vector_store import get_vector_store

log = logging.getLogger(__name__)


class RAGService:
    """Main RAG Service for ValueKit Financial Analysis"""

    def __init__(self):
        self.config = RAGConfig()
        self.client = Anthropic(api_key=self.config.ANTHROPIC_API_KEY)
        self.vector_store = get_vector_store()
        log.info(
            "[rag_service][init] model=%s pipeline_version=%s",
            self.config.LLM_MODEL,
            PIPELINE_VERSION,
        )

    def add_financial_documents(
        self, documents: List[Dict[str, Any]]
    ) -> Dict[str, Any]:
        """
        Add financial documents to the knowledge base

        Args:
            documents: List of dicts with 'text' and 'metadata' keys

        Returns:
            Status dict with counts
        """
        try:
            num_chunks = self.vector_store.add_documents(documents)
            log.info(
                "[rag_service][add_documents] docs=%d chunks=%d",
                len(documents),
                num_chunks,
            )
            return {
                "status": "success",
                "documents_added": len(documents),
                "chunks_created": num_chunks,
            }
        except Exception as e:
            log.error("[rag_service][add_documents_error] error=%s", e)
            return {"status": "error", "error": str(e)}

    def _format_context(self, documents: List[Document]) -> str:
        """Format retrieved documents into context string"""
        parts = []
        for i, doc in enumerate(documents, 1):
            meta = doc.metadata
            parts.append(
                f"Document {i}:\n"
                f"Source: {meta.get('document_type', 'Unknown')} - {meta.get('company', meta.get('ticker', 'Unknown'))}\n"
                f"Content: {doc.page_content}\n"
            )
        return "\n---\n".join(parts)

    def analyze_with_rag(
        self,
        query: str,
        quantitative_data: Optional[Dict[str, Any]] = None,
        max_tokens: int = 4096,
    ) -> Dict[str, Any]:
        """
        Perform RAG-enhanced analysis

        Args:
            query: Analysis query
            quantitative_data: Dict with MOS, ROIC, etc.
            max_tokens: Maximum response length

        Returns:
            Dict with analysis, sources, and metadata
        """
        retrieved_docs = self.vector_store.similarity_search_with_score(query)
        # Deduplicate by content — same chunk retrieved multiple times inflates context
        seen = set()
        unique_docs = []
        for doc, score in retrieved_docs:
            h = hash(doc.page_content)
            if h not in seen:
                seen.add(h)
                unique_docs.append((doc, score))
        retrieved_docs = unique_docs
        context = self._format_context([doc for doc, score in retrieved_docs])
        prompt = self._build_analysis_prompt(query, context, quantitative_data)

        try:
            message = self.client.messages.create(
                model=self.config.LLM_MODEL,
                max_tokens=max_tokens,
                temperature=self.config.LLM_TEMPERATURE,
                messages=[{"role": "user", "content": prompt}],
            )

            log.info(
                "[rag_service][analysis_complete] model=%s sources=%d pipeline_version=%s",
                self.config.LLM_MODEL,
                len(retrieved_docs),
                PIPELINE_VERSION,
            )

            return {
                "status": "success",
                "analysis": message.content[0].text,
                "sources": [
                    {
                        "content": doc.page_content[:200] + "...",
                        "metadata": doc.metadata,
                        "relevance_score": float(score),
                    }
                    for doc, score in retrieved_docs
                ],
                "quantitative_metrics": quantitative_data,
                "model": self.config.LLM_MODEL,
                "pipeline_version": PIPELINE_VERSION,
            }

        except Exception as e:
            log.error("[rag_service][analysis_error] error=%s", e)
            return {
                "status": "error",
                "error": "Analysis failed. See server logs for details.",
            }

    def _build_analysis_prompt(
        self,
        query: str,
        context: str,
        quantitative_data: Optional[Dict[str, Any]],
    ) -> str:
        """Build WIPRO-compliant analysis prompt"""
        n_sources = context.count("Document ")

        source_warning = ""
        if n_sources < 3:
            source_warning = (
                f"\nThis assessment is based on {n_sources} retrieved document "
                f"sections and should be interpreted with caution."
            )

        return f"""You are a quantitative value investing analyst conducting a scientific investment analysis.

QUANTITATIVE METRICS (PRIMARY DECISION FACTORS):
{self._format_quantitative_data(quantitative_data) if quantitative_data else "No quantitative data provided."}

RETRIEVED DOCUMENT EXCERPTS (QUALITATIVE CONTEXT):
{context}

USER QUERY:
{query}

ANALYSIS INSTRUCTIONS:
1. Answer using ONLY information present in the retrieved documents or provided quantitative metrics.
2. Distinguish clearly between observed data, calculated metrics, and model-generated interpretations.
3. Use precise, neutral, third-person language. Hedging is required where uncertainty exists: use "suggests", "indicates", "may", "appears to" — not "proves" or "demonstrates" unless based on hard data.
4. Every numerical claim must reference its source (SEC filing section or FMP metric name).
5. If a moat type cannot be assessed from available documents, state that explicitly — do not infer or construct supporting evidence.
6. Avoid colloquialisms, contractions, and value-loaded adjectives without quantitative evidence.
7. An incomplete but honest answer is always preferred over a complete but fabricated one.{source_warning}

Provide your analysis:"""

    def _format_quantitative_data(self, data: Dict[str, Any]) -> str:
        """Format quantitative metrics for prompt"""
        if not data:
            return "No data available"

        sections = []
        if "dcf" in data:
            sections.append(f"DCF Analysis:\n{self._format_dict(data['dcf'])}")
        if "roic" in data:
            sections.append(f"ROIC: {data['roic']}")
        if "margin_of_safety" in data:
            sections.append(f"Margin of Safety: {data['margin_of_safety']}")
        if "other_metrics" in data:
            sections.append(
                f"Other Metrics:\n{self._format_dict(data['other_metrics'])}"
            )
        return "\n\n".join(sections)

    def _format_dict(self, d: Dict, indent: int = 2) -> str:
        """Format dict for readable prompt output"""
        lines = []
        for key, value in d.items():
            if isinstance(value, dict):
                lines.append(f"{' ' * indent}{key}:")
                lines.append(self._format_dict(value, indent + 2))
            else:
                lines.append(f"{' ' * indent}{key}: {value}")
        return "\n".join(lines)

    def get_knowledge_base_stats(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""
        return self.vector_store.get_collection_stats()


def get_rag_service() -> RAGService:
    """Factory function"""
    return RAGService()
