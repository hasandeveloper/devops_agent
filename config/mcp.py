import os
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def stdio_server(module_path: str) -> dict:
    """Connection config for an MCP server run as `python -m <module_path>`.

    Uses sys.executable (the running venv's interpreter) and an explicit cwd
    of the project root, so this resolves correctly regardless of where the
    calling process (worker.py, tests, etc.) was itself launched from.

    Every MCP server in this codebase is only ever spawned by worker-side pipeline
    code (gather_context.py, investigate_further.py), never by the FastAPI process --
    so its own logging (see config/logging.py's LOG_FILE_NAME handling) is routed to
    jobs.log, matching where the rest of that pipeline's logs already go. {**os.environ,
    ...} rather than a bare override dict: passing "env" at all replaces the
    subprocess's entire environment, so omitting the parent's env here would silently
    break AWS credential discovery, DB env vars, PATH, everything.
    """
    return {
        "command": sys.executable,
        "args": ["-m", module_path],
        "transport": "stdio",
        "cwd": str(PROJECT_ROOT),
        "env": {**os.environ, "LOG_FILE_NAME": "jobs.log"},
    }
