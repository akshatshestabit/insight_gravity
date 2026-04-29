"""
PDF Parser using:
  - pdfplumber  → text extraction + table detection
  - PyMuPDF     → image extraction with bounding boxes

Text is split into CHUNK_SIZE character chunks per page.
Tables are converted to both JSON (list-of-rows) and Markdown format.
Images are extracted as raw bytes with their page bbox.
"""
from pathlib import Path

import fitz  # PyMuPDF
import pdfplumber

from ingestion.parsers.base import (
    BaseParser,
    ImageChunk,
    ParseResult,
    TableChunk,
    TextChunk,
)

CHUNK_SIZE = 1000  # characters per text chunk


class PDFParser(BaseParser):

    def supports(self, file_extension: str) -> bool:
        return file_extension.lower() == ".pdf"

    def parse(self, file_path: str) -> ParseResult:
        path = Path(file_path)
        result = ParseResult(filename=path.name)

        try:
            self._extract_text_and_tables(file_path, result, path.name)
            self._extract_images(file_path, result, path.name)
        except Exception as e:
            result.error = str(e)

        return result

    # ── Text & Tables via pdfplumber ──────────────────────────────────────────

    def _extract_text_and_tables(self, file_path: str, result: ParseResult, source: str):
        chunk_index = 0
        with pdfplumber.open(file_path) as pdf:
            for page_num, page in enumerate(pdf.pages, start=1):
                # Text
                text = page.extract_text() or ""
                if text.strip():
                    for i in range(0, len(text), CHUNK_SIZE):
                        segment = text[i : i + CHUNK_SIZE]
                        if segment.strip():
                            result.text_chunks.append(
                                TextChunk(
                                    content=segment,
                                    page_number=page_num,
                                    chunk_index=chunk_index,
                                    metadata={"source": source, "page": page_num},
                                )
                            )
                            chunk_index += 1

                # Tables
                for table in page.extract_tables() or []:
                    if table:
                        result.table_chunks.append(
                            TableChunk(
                                content_json=table,
                                content_markdown=self._to_markdown(table),
                                page_number=page_num,
                                metadata={"source": source, "page": page_num},
                            )
                        )

    # ── Images via PyMuPDF ────────────────────────────────────────────────────

    def _extract_images(self, file_path: str, result: ParseResult, source: str):
        doc = fitz.open(file_path)
        try:
            for page_num, page in enumerate(doc, start=1):
                for img_idx, img_info in enumerate(page.get_images(full=True)):
                    xref = img_info[0]
                    base_image = doc.extract_image(xref)
                    bbox = page.get_image_bbox(img_info)
                    result.image_chunks.append(
                        ImageChunk(
                            image_bytes=base_image["image"],
                            extension=base_image.get("ext", "png"),
                            page_number=page_num,
                            bbox=(bbox.x0, bbox.y0, bbox.x1, bbox.y1),
                            caption=f"Image {img_idx + 1} on page {page_num}",
                            metadata={"source": source, "page": page_num},
                        )
                    )
        finally:
            doc.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _to_markdown(table: list) -> str:
        if not table:
            return ""
        rows = []
        header = [str(c or "") for c in table[0]]
        rows.append("| " + " | ".join(header) + " |")
        rows.append("|" + "|".join(["---"] * len(header)) + "|")
        for row in table[1:]:
            rows.append("| " + " | ".join(str(c or "") for c in row) + " |")
        return "\n".join(rows)
