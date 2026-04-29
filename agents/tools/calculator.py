import math
from langchain_core.tools import tool


@tool
def calculator(expression: str) -> str:
    """
    Evaluate a mathematical expression safely.
    Supports basic arithmetic and math functions (sin, cos, sqrt, log, etc.).
    Example: '2 + 2', 'sqrt(144)', 'log(100, 10)'
    """
    try:
        # Whitelist only math names — no builtins
        safe_globals = {name: getattr(math, name) for name in dir(math) if not name.startswith("_")}
        safe_globals["__builtins__"] = {}
        result = eval(expression, safe_globals)  # noqa: S307
        return f"{result}"
    except Exception as e:
        return f"Calculation error: {e}"
