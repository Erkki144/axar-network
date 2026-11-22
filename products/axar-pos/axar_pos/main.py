"""Local entry point: ``uvicorn axar_pos.main:app``.

Bind to 127.0.0.1 only; this slice has no authentication.
"""

from .api import create_app

app = create_app()
