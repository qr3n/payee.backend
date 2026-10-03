"""
Base protocol and interface for SBP payment link resolvers.
"""

from typing import Protocol, runtime_checkable


@runtime_checkable
class SBPResolver(Protocol):
    """Protocol for extracting clean NSPK SBP links from gateway URLs."""

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """Check if resolver can handle the given payment URL."""
        ...

    @classmethod
    async def resolve(cls, url: str, timeout: float = 15.0) -> str | None:
        """
        Extract clean SBP link (e.g. https://qr.nspk.ru/...).
        Returns clean SBP URL on success, or None on failure/expiration.
        """
        ...
