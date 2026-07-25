"""Vercel Function entrypoint: exposes the FastAPI app as an ASGI handler.

The backend package lives in backend/src, which is not on the path when Vercel
imports this module, so add it before importing ffb.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "backend" / "src"))

from ffb.api import app  # noqa: E402

__all__ = ["app"]
