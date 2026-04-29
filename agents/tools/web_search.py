import os
from langchain_core.tools import tool


@tool
def web_search(query: str) -> str:
    """
    Search the web for up-to-date information on any topic.
    Returns a summary of the top results.
    Requires TAVILY_API_KEY environment variable.
    """
    try:
        from tavily import TavilyClient
        client = TavilyClient(api_key=os.getenv("TAVILY_API_KEY", ""))
        response = client.search(query=query, max_results=3, search_depth="basic")
        results = response.get("results", [])
        if not results:
            return "No results found."
        parts = []
        for r in results:
            parts.append(f"**{r.get('title', 'No title')}**\n{r.get('content', '')}\nSource: {r.get('url', '')}")
        return "\n\n---\n\n".join(parts)
    except Exception as e:
        return f"Web search failed: {e}"
