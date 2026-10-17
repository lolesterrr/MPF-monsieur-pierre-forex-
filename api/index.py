"""Vercel entrypoint.

Vercel's Python runtime discovers functions by file inside `api/`, so the WSGI
app has to be reachable from here. `vercel.json` rewrites every path to this
function, and `src.wsgi` then serves both the static dashboard and the API —
same code that runs locally and under gunicorn.
"""

import sys
from pathlib import Path

# The function's working directory is the project root, but imports resolve
# against this file. Add the root so `src` is importable.
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from src.wsgi import app  # noqa: E402,F401
