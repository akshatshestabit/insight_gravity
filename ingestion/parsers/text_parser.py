"""
Plain-text parser for .txt, .md, .rst, .csv files.
Splits content intelligently using LangChain's RecursiveCharacterTextSplitter.
"""
from pathlib import Path

from langchain_text_splitters import RecursiveCharacterTextSplitter
from ingestion.parsers.base import BaseParser, ParseResult, TextChunk

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

            # Smart chunking that respects paragraphs, sentences, and words
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=150,
                separators=["\n\n", "\n", " ", ""]
            )
            
            chunks = splitter.split_text(text)

            for idx, segment in enumerate(chunks):
                if segment.strip():
                    result.text_chunks.append(
                        TextChunk(
                            content=segment.strip(),
                            page_number=1,
                            chunk_index=idx,
                            metadata={"source": path.name},
                        )
                    )
        except Exception as e:
            result.error = str(e)

        return result
