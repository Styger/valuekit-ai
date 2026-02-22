"""
Vector Store Manager - ChromaDB with Voyage AI Embeddings
"""

import logging
import os
import sys
from pathlib import Path
from typing import Any, Dict, List

import chromadb
from langchain_chroma import Chroma
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter
from voyageai import Client as VoyageClient

root_dir = Path(__file__).resolve().parent.parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.insert(0, str(root_dir))

from backend.valuekit_ai.config.config import RAGConfig

log = logging.getLogger(__name__)


class VoyageEmbeddings:
    """LangChain-compatible wrapper for Voyage AI embeddings"""

    def __init__(self, api_key: str, model: str = "voyage-finance-2"):
        self.client = VoyageClient(api_key=api_key)
        self.model = model

    def embed_documents(self, texts: List[str]) -> List[List[float]]:
        result = self.client.embed(texts, model=self.model, input_type="document")
        return result.embeddings

    def embed_query(self, text: str) -> List[float]:
        result = self.client.embed([text], model=self.model, input_type="query")
        return result.embeddings[0]


class VectorStore:
    """Vector Store Manager for Financial Documents"""

    def __init__(self):
        self.config = RAGConfig()
        self.embeddings = VoyageEmbeddings(
            api_key=self.config.VOYAGE_API_KEY,
            model=self.config.EMBEDDING_MODEL,
        )
        self.text_splitter = RecursiveCharacterTextSplitter(
            chunk_size=self.config.CHUNK_SIZE,
            chunk_overlap=self.config.CHUNK_OVERLAP,
            separators=["\n\n", "\n", ".", " ", ""],
        )
        self._initialize_chroma()
        log.info(
            "[vector_store][init] collection=%s persist_dir=%s",
            self.config.COLLECTION_NAME,
            self.config.CHROMA_PERSIST_DIR,
        )

    def _initialize_chroma(self):
        """Initialize ChromaDB with persistence"""
        os.makedirs(self.config.CHROMA_PERSIST_DIR, exist_ok=True)
        self.vectorstore = Chroma(
            collection_name=self.config.COLLECTION_NAME,
            embedding_function=self.embeddings,
            persist_directory=self.config.CHROMA_PERSIST_DIR,
        )

    def add_documents(self, documents: List[Any]) -> int:
        """
        Add documents to vector store

        Args:
            documents: LangChain Documents or dicts with 'text' and 'metadata' keys

        Returns:
            Number of chunks created
        """
        docs = []
        for doc in documents:
            if isinstance(doc, Document):
                docs.append(doc)
            elif isinstance(doc, dict):
                docs.append(
                    Document(
                        page_content=doc["text"],
                        metadata=doc.get("metadata", {}),
                    )
                )
            else:
                raise TypeError(f"Expected Document or dict, got {type(doc)}")

        chunks = self.text_splitter.split_documents(docs)
        self.vectorstore.add_documents(chunks)

        log.info(
            "[vector_store][add_documents] docs=%d chunks=%d",
            len(docs),
            len(chunks),
        )
        return len(chunks)

    def similarity_search(self, query: str, k: int = None) -> List[Document]:
        """Search for similar documents"""
        if k is None:
            k = self.config.TOP_K_RESULTS
        return self.vectorstore.similarity_search(query, k=k)

    def similarity_search_with_score(self, query: str, k: int = None) -> List[tuple]:
        """Search with relevance scores"""
        if k is None:
            k = self.config.TOP_K_RESULTS
        return self.vectorstore.similarity_search_with_score(query, k=k)

    def delete_collection(self):
        """Delete entire collection"""
        self.vectorstore.delete_collection()
        log.warning("[vector_store][delete_collection] collection deleted")

    def get_collection_stats(self) -> Dict[str, Any]:
        """Get collection statistics"""
        collection = self.vectorstore._collection
        return {"name": collection.name, "count": collection.count()}


def get_vector_store() -> VectorStore:
    """Factory function"""
    return VectorStore()
