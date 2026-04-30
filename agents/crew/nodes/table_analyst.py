"""Table Analyst node — reasons over extracted tables using Pandas/DuckDB."""
from __future__ import annotations

import json
import logging
from io import StringIO

import duckdb
import pandas as pd
from langchain_core.messages import AIMessage, HumanMessage
from langchain_google_genai import ChatGoogleGenerativeAI

from agents.crew.state import CrewState, DEFAULT_BUDGETS, update_budget
from backend.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """You are a quantitative table analyst. You will be given:
1. A research question
2. Extracted table data (as JSON arrays or Markdown)

Your job:
- Identify the most relevant table(s)
- Perform numerical reasoning (comparisons, trends, aggregations)
- Produce a concise, data-driven analysis with specific numbers cited

Format your response as clear paragraphs with numbers and units preserved.
"""


def _tables_to_dataframes(docs: list[dict]) -> list[tuple[str, pd.DataFrame]]:
    """Extract table chunks and convert them to DataFrames."""
    frames = []
    for doc in docs:
        if doc.get("modality") != "table":
            continue
        content = doc.get("content", "")
        if not content:
            continue
        provenance = doc.get("provenance", {})
        label = f"{provenance.get('filename', 'table')} p.{provenance.get('page', '?')}"
        try:
            # Try JSON 2-D array first
            data = json.loads(content)
            if isinstance(data, list) and data:
                if isinstance(data[0], list):
                    df = pd.DataFrame(data[1:], columns=data[0])
                else:
                    df = pd.DataFrame(data)
                frames.append((label, df))
        except (json.JSONDecodeError, ValueError):
            # Fall back to Markdown table parsing
            try:
                df = pd.read_csv(StringIO(content), sep="|", skipinitialspace=True)
                df = df.dropna(axis=1, how="all").iloc[1:]  # drop separator row
                df.columns = [c.strip() for c in df.columns]
                frames.append((label, df))
            except Exception:
                pass
    return frames


def _run_duckdb_summary(df: pd.DataFrame, label: str) -> str:
    """Produce a quick statistical summary via DuckDB."""
    try:
        conn = duckdb.connect()
        conn.register("tbl", df)
        desc = conn.execute("SUMMARIZE tbl").df().to_string(index=False)
        conn.close()
        return f"=== {label} ===\n{desc}"
    except Exception as exc:
        return f"=== {label} ===\n(DuckDB error: {exc})"


async def table_analyst_node(state: CrewState) -> dict:
    """Analyse all table chunks from retrieval results."""
    agent = "table_analyst"

    budgets = state.get("budgets") or dict(DEFAULT_BUDGETS)
    if budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_used"] >= \
            budgets.get(agent, DEFAULT_BUDGETS[agent])["tokens_limit"]:
        return {"budget_exceeded": True, "error": "Table Analyst budget exceeded."}

    docs = state.get("retrieved_docs") or []
    frames = _tables_to_dataframes(docs)

    if not frames:
        return {
            "table_analysis": "No structured tables found in the retrieved documents.",
            "messages": [AIMessage(content="[TableAnalyst] No table chunks to analyse.")],
        }

    # Build context string
    summaries = [_run_duckdb_summary(df, label) for label, df in frames]
    # Also include Markdown representation for readability
    md_tables = []
    for label, df in frames:
        try:
            md_tables.append(f"**{label}**\n" + df.head(20).to_markdown(index=False))
        except Exception:
            md_tables.append(f"**{label}**\n" + df.head(20).to_string())

    context = "\n\n".join(summaries + md_tables)

    response = None
    llm = ChatGoogleGenerativeAI(
        model=settings.GEMINI_MODEL,
        google_api_key=settings.GEMINI_API_KEY,
        temperature=0,
    )

    prompt = (
        f"Research question: {state['query']}\n\n"
        f"Extracted tables:\n{context[:6000]}\n\n"
        "Provide a detailed quantitative analysis answering the research question."
    )

    try:
        response = await llm.ainvoke([
            HumanMessage(content=_SYSTEM_PROMPT),
            HumanMessage(content=prompt),
        ])
        analysis = response.content
    except Exception as exc:
        logger.error("Table analyst LLM error: %s", exc)
        analysis = "\n\n".join(summaries)

    usage = getattr(response, "usage_metadata", None) or {}
    in_tok  = usage.get("input_tokens", len(prompt) // 4)
    out_tok = usage.get("output_tokens", 300)
    budget_update = update_budget(state, agent, in_tok, out_tok)

    return {
        "table_analysis": analysis,
        "messages": [AIMessage(content=f"[TableAnalyst] Analysed {len(frames)} table(s).")],
        **budget_update,
    }
