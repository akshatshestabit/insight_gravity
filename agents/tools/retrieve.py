"""
retrieve_docs agent tool — lets the ReAct agent search indexed documents.
"""
from langchain_core.tools import tool


@tool
def retrieve_docs(query: str) -> str:
    """
    Search the indexed documents for relevant information.
    Returns the top 5 passages with source citations (filename and page number).
    Use this before answering any question that might be answered by uploaded documents.

    Args:
        query: the search question or phrase
    """
    try:
        from retrieval.retriever import retrieve
        results = retrieve(query, top_k=5)
        if not results:
            return "No relevant documents found in the index. The knowledge base may be empty."

        lines = []
        for i, r in enumerate(results, 1):
            prov = r.get("provenance", {})
            citation = f"[{prov.get('filename', '?')}, p.{prov.get('page', '?')}]"
            modality = r.get("modality", "text").upper()
            score = r.get("score", 0.0)
            lines.append(f"{i}. [{modality}] {citation} (score={score:.3f})\n   {r['content'][:400]}")

        return "\n\n".join(lines)
    except Exception as e:
        return f"Retrieval failed: {e}"
