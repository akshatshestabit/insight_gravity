"""
vLLM client for chart/image captioning.

Replaces the Gemini Vision API call in ingestion/tasks.py when VLM_ENABLED=true.
The vLLM server exposes an OpenAI-compatible API, so we use the openai client.

Usage (automatic — set in .env):
    VLM_ENABLED=true
    VLM_BASE_URL=http://localhost:8001/v1
    VLM_MODEL=insightforge-vlm
"""
from __future__ import annotations

import base64
import logging
import os
from typing import Optional

logger = logging.getLogger(__name__)

VLM_ENABLED  = os.getenv("VLM_ENABLED", "false").lower() == "true"
VLM_BASE_URL = os.getenv("VLM_BASE_URL", "http://localhost:8001/v1")
VLM_MODEL    = os.getenv("VLM_MODEL",    "insightforge-vlm")
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", "512"))


async def caption_image_vllm(image_bytes: bytes, page: int, filename: str) -> str:
    """
    Caption an image using the locally hosted vLLM server (Qwen2-VL / Llama Vision).

    Falls back to the Gemini API if vLLM is unavailable or disabled.

    Args:
        image_bytes: Raw image bytes (JPEG or PNG)
        page:        Page number in the source document
        filename:    Source document filename (for context)

    Returns:
        Caption string describing the image content.
    """
    if not VLM_ENABLED:
        return await _caption_gemini_fallback(image_bytes, page, filename)

    try:
        from openai import AsyncOpenAI
        client = AsyncOpenAI(base_url=VLM_BASE_URL, api_key="vllm-local")

        b64 = base64.b64encode(image_bytes).decode()
        img_url = f"data:image/jpeg;base64,{b64}"

        prompt = (
            f"You are analyzing a figure from document '{filename}', page {page}. "
            "Describe this image in detail: chart type, axes, key values, trends, "
            "and any text visible. Be specific and quantitative."
        )

        response = await client.chat.completions.create(
            model=VLM_MODEL,
            messages=[{
                "role": "user",
                "content": [
                    {"type": "text",      "text": prompt},
                    {"type": "image_url", "image_url": {"url": img_url}},
                ],
            }],
            max_tokens=VLM_MAX_TOKENS,
            temperature=0.1,
        )
        caption = response.choices[0].message.content.strip()
        logger.info("vLLM caption: %s chars for %s p.%d", len(caption), filename, page)
        return caption

    except Exception as exc:
        logger.warning("vLLM captioning failed (%s) — falling back to Gemini", exc)
        return await _caption_gemini_fallback(image_bytes, page, filename)


async def _caption_gemini_fallback(image_bytes: bytes, page: int, filename: str) -> str:
    """Fallback: use Gemini Vision API (current behaviour in ingestion/tasks.py)."""
    try:
        import google.generativeai as genai
        from backend.config import settings
        genai.configure(api_key=settings.GEMINI_API_KEY)
        model = genai.GenerativeModel("gemini-2.5-flash")
        import PIL.Image, io
        img = PIL.Image.open(io.BytesIO(image_bytes))
        resp = model.generate_content([
            f"Describe this figure from '{filename}' page {page}. Include chart type, axes, values, trends.",
            img,
        ])
        return resp.text.strip()
    except Exception as exc:
        logger.error("Gemini Vision fallback failed: %s", exc)
        return f"[Image from {filename} p.{page} — captioning unavailable]"


def get_vllm_stats() -> dict:
    """Return vLLM server stats if running."""
    if not VLM_ENABLED:
        return {"enabled": False}
    try:
        import httpx, asyncio
        resp = httpx.get(f"{VLM_BASE_URL}/models", timeout=2.0)
        models = resp.json().get("data", [])
        return {"enabled": True, "base_url": VLM_BASE_URL, "models": [m["id"] for m in models]}
    except Exception as exc:
        return {"enabled": True, "base_url": VLM_BASE_URL, "error": str(exc)}
