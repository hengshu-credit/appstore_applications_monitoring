"""Development-only Uvicorn factory; reload watches the mounted Python source."""
from .config import load
from .web import create_app


def create_development_app():
    return create_app(load())
