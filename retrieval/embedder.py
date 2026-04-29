"""
Text embedding using Google's gemini-embedding-001 via the official google-genai SDK.

Per the official docs (https://ai.google.dev/gemini-api/docs/embeddings):
  - Model: gemini-embedding-001  (3072-dim vectors, task_type support)
  - Model: gemini-embedding-2    (3072-dim, multimodal, newer)
  - SDK:   from google import genai

We use gemini-embedding-001 (768-dim) to match our Qdrant collection config.
Task types used:
  - RETRIEVAL_QUERY    → for user search queries
  - RETRIEVAL_DOCUMENT → for indexing document content
"""
import logging
from typing import List

from google import genai
from google.genai import types

from backend.config import settings

logger = logging.getLogger(__name__)

_client = None


def _get_client() -> genai.Client:
    global _client
    if _client is None:
        _client = genai.Client(api_key=settings.GEMINI_API_KEY)
    return _client


def embed_text(text: str) -> List[float]:
    """
    Embed a query string → 768-dim vector.
    Uses RETRIEVAL_QUERY task type (optimised for user search questions).
    """
    client = _get_client()
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=text,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_QUERY"),
    )
    return list(result.embeddings[0].values)


def embed_texts(texts: List[str]) -> List[List[float]]:
    """
    Batch embed document chunks → list of 768-dim vectors.
    Uses RETRIEVAL_DOCUMENT task type (optimised for indexing content).
    """
    if not texts:
        return []
    client = _get_client()
    result = client.models.embed_content(
        model="gemini-embedding-001",
        contents=texts,
        config=types.EmbedContentConfig(task_type="RETRIEVAL_DOCUMENT"),
    )
    return [list(e.values) for e in result.embeddings]
