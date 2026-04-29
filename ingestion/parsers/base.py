"""
Base parser interfaces and data classes for document extraction.

Every parser produces a ParseResult containing:
  - text_chunks  → plain text segments (chunked by character count)
  - table_chunks → structured table data (JSON rows + Markdown rendering)
  - image_chunks → raw image bytes with bounding box + page metadata
"""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple


@dataclass
class TextChunk:
    content: str
    page_number: int
    chunk_index: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class TableChunk:
    content_json: List[List[str]]   # 2-D array (rows × cols)
    content_markdown: str           # Markdown table for display / LLM prompting
    page_number: int
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ImageChunk:
    image_bytes: bytes
    extension: str                  # png | jpg | jpeg
    page_number: int
    bbox: Tuple[float, float, float, float]   # (x0, y0, x1, y1)
    caption: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)


@dataclass
class ParseResult:
    filename: str
    text_chunks: List[TextChunk] = field(default_factory=list)
    table_chunks: List[TableChunk] = field(default_factory=list)
    image_chunks: List[ImageChunk] = field(default_factory=list)
    error: Optional[str] = None

    @property
    def total_chunks(self) -> int:
        return len(self.text_chunks) + len(self.table_chunks) + len(self.image_chunks)


class BaseParser(ABC):
    """Abstract base class for all document parsers."""

    @abstractmethod
    def parse(self, file_path: str) -> ParseResult:
        """Parse a file and return structured artifacts."""

    @abstractmethod
    def supports(self, file_extension: str) -> bool:
        """Return True if this parser handles the given file extension."""
