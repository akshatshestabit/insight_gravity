import logging
from langchain_core.tools import tool
from google import genai
from google.genai import types
from backend.config import settings

logger = logging.getLogger(__name__)

@tool
def web_search(query: str) -> str:
    """
    Search the web for up-to-date information on any topic using Google Search via Gemini.
    Returns a summary of the top results and answers to the query.
    """
    try:
        client = genai.Client(api_key=settings.GEMINI_API_KEY)
        response = client.models.generate_content(
            model=settings.GEMINI_MODEL,
            contents=query,
            config=types.GenerateContentConfig(
                tools=[{"google_search": {}}],
            )
        )
        return response.text
    except Exception as e:
        logger.error(f"Web search failed: {e}")
        return f"Web search failed: {e}"
