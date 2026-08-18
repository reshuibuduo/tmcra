"""Compatibility import for the public asynchronous client."""

from .client import AsyncClient

AsyncTMCRAClient = AsyncClient

__all__ = ["AsyncClient", "AsyncTMCRAClient"]
