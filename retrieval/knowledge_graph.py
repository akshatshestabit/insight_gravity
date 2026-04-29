"""
Lightweight NetworkX knowledge graph per job_id.

At ingest time:  build_graph(job_id, text_chunks) — extracts entity triples via Gemini
At query time:   query_graph(job_id, entity)       — returns neighbor context as text
"""
import json
import logging
from typing import Any, Dict, List

import networkx as nx
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import HumanMessage

from backend.config import settings

logger = logging.getLogger(__name__)

# In-memory store: job_id → nx.DiGraph
_GRAPHS: Dict[str, nx.DiGraph] = {}


def _llm():
    return ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
    )


def _extract_triples(text: str) -> List[Dict[str, str]]:
    """Extract (subject, predicate, object) triples from text via Gemini."""
    if len(text) < 50:
        return []
    prompt = (
        "Extract key entity relationships from this text as a JSON array. "
        "Each item has keys: subject, predicate, object. "
        "Focus on named entities (people, orgs, products, metrics). "
        "Return at most 8 triples. If none, return [].\n\n"
        f"Text: {text[:800]}"
    )
    try:
        resp = _llm().invoke([HumanMessage(content=prompt)])
        content = resp.content.strip()
        # Strip markdown code fences if present
        if "```" in content:
            content = content.split("```")[1].lstrip("json").strip()
        triples = json.loads(content)
        return triples if isinstance(triples, list) else []
    except Exception as e:
        logger.debug("Triple extraction failed: %s", e)
        return []


def build_graph(job_id: str, text_chunks: List[str]) -> int:
    """
    Build/update knowledge graph for a job from text chunks.
    Processes up to 15 chunks to limit API usage.
    Returns number of triples added.
    """
    if job_id not in _GRAPHS:
        _GRAPHS[job_id] = nx.DiGraph()
    G = _GRAPHS[job_id]
    total = 0
    for chunk in text_chunks[:15]:
        for t in _extract_triples(chunk):
            subj = str(t.get("subject", "")).strip()
            pred = str(t.get("predicate", "")).strip()
            obj = str(t.get("object", "")).strip()
            if subj and pred and obj:
                G.add_edge(subj, obj, relation=pred)
                total += 1
    logger.info("[%s] Graph: %d nodes, %d edges", job_id, G.number_of_nodes(), G.number_of_edges())
    return total


def query_graph(job_id: str, entity: str) -> str:
    """Return relationships for an entity as human-readable text."""
    G = _GRAPHS.get(job_id)
    if not G or G.number_of_nodes() == 0:
        return "No knowledge graph available for this job."

    # Case-insensitive entity match
    target = entity
    for node in G.nodes():
        if entity.lower() in node.lower() or node.lower() in entity.lower():
            target = node
            break

    if target not in G:
        return f"Entity '{entity}' not found in knowledge graph."

    lines = [f"Knowledge graph context for '{target}':"]
    for _, nbr, data in G.out_edges(target, data=True):
        lines.append(f"  {target} --[{data.get('relation', 'related')}]--> {nbr}")
    for pred, _, data in G.in_edges(target, data=True):
        lines.append(f"  {pred} --[{data.get('relation', 'related')}]--> {target}")
    return "\n".join(lines)


def get_graph_summary(job_id: str) -> Dict[str, Any]:
    G = _GRAPHS.get(job_id)
    if not G:
        return {"nodes": 0, "edges": 0, "entities": []}
    return {
        "nodes": G.number_of_nodes(),
        "edges": G.number_of_edges(),
        "entities": list(G.nodes())[:20],
    }
