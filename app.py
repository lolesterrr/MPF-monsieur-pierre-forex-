"""Vercel / gunicorn entrypoint.

Vercel looks for a top-level `app` in app.py. Render and friends want
`gunicorn app:app`. Both get the same WSGI application.
"""

from src.wsgi import app  # noqa: F401
