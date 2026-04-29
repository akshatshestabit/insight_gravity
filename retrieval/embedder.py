"""
Text embedding using Google text-embedding-004 (768-dim vectors).
"""
import logging
from typing import List

from langchain_google_genai import GoogleGenerativeAIEmbeddings
from backend.config import settings

logger = logging.getLogger(__name__)

_embedder = None


def _get_embedder() -> GoogleGenerativeAIEmbeddings:
    global _embedder
    if _embedder is None:
        _embedder = GoogleGenerativeAIEmbeddings(
            model=settings.EMBEDDING_MODEL,
            google_api_key=settings.GEMINI_API_KEY,
        )
    return _embedder


def embed_text(text: str) -> List[float]:
    """Embed a single text string → 768-dim vector."""
    return _get_embedder().embed_query(text)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """Batch embed a list of texts."""
    if not texts:
        return []
    return _get_embedder().embed_documents(texts)
