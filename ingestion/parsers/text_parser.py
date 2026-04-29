"""
Plain-text parser for .txt, .md, .rst, .csv files.
Splits content into CHUNK_SIZE character chunks.
"""
from pathlib import Path

from ingestion.parsers.base import BaseParser, ParseResult, TextChunk

CHUNK_SIZE = 1000
SUPPORTED = {".txt", ".md", ".rst", ".csv", ".log"}


class TextParser(BaseParser):

    def supports(self, file_extension: str) -> bool:
        return file_extension.lower() in SUPPORTED

    def parse(self, file_path: str) -> ParseResult:
        path = Path(file_path)
        result = ParseResult(filename=path.name)

        try:
            with open(file_path, "r", encoding="utf-8", errors="replace") as f:
                text = f.read()

            for idx, i in enumerate(range(0, len(text), CHUNK_SIZE)):
                segment = text[i : i + CHUNK_SIZE]
                if segment.strip():
                    result.text_chunks.append(
                        TextChunk(
                            content=segment,
                            page_number=1,
                            chunk_index=idx,
                            metadata={"source": path.name},
                        )
                    )
        except Exception as e:
            result.error = str(e)

        return result
