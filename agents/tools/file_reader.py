import os
from langchain_core.tools import tool

MAX_CHARS = 8000  # Limit output to avoid flooding context window


@tool
def file_reader(file_path: str) -> str:
    """
    Read the text contents of a file (txt, md, py, json, csv, etc.).
    Input must be an absolute or relative file path.
    Returns the first 8000 characters of the file.
    """
    try:
        if not os.path.exists(file_path):
            return f"File not found: '{file_path}'"
        if not os.path.isfile(file_path):
            return f"'{file_path}' is not a file."
        size = os.path.getsize(file_path)
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read(MAX_CHARS)
        truncated = " [truncated]" if size > MAX_CHARS else ""
        return f"File: {file_path} ({size} bytes){truncated}\n\n{content}"
    except Exception as e:
        return f"Error reading file: {e}"
