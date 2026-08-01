import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def stdio_server(module_path: str) -> dict:
    """Connection config for an MCP server run as `python -m <module_path>`.

    Uses sys.executable (the running venv's interpreter) and an explicit cwd
    of the project root, so this resolves correctly regardless of where the
    calling process (worker.py, tests, etc.) was itself launched from.
    """
    return {
        "command": sys.executable,
        "args": ["-m", module_path],
        "transport": "stdio",
        "cwd": str(PROJECT_ROOT),
    }
