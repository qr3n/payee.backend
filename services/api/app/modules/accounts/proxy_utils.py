"""
Proxy parsing and validation utilities for Telegram MTProto client.
Converts standard proxy URLs into Telethon/python-socks dictionaries.
"""

from typing import Any
from urllib.parse import unquote, urlparse


def parse_proxy_url(proxy_url: str | None) -> dict[str, Any] | None:
    """
    Parse a proxy URL string into a dict compatible with Telethon and python-socks.

    Supported schemes: socks5, socks4, http, https.
    URL format: scheme://[username:password@]host:port
    """
    if not proxy_url or not proxy_url.strip():
        return None

    cleaned_url = proxy_url.strip()
    parsed = urlparse(cleaned_url)

    if not parsed.scheme or not parsed.hostname or not parsed.port:
        raise ValueError(
            f"Invalid proxy URL '{proxy_url}'. Expected format: scheme://[user:pass@]host:port"
        )

    scheme = parsed.scheme.lower()
    if scheme not in ("socks5", "socks4", "http", "https"):
        raise ValueError(
            f"Unsupported proxy scheme '{scheme}'. "
            "Must be one of: socks5, socks4, http, https"
        )

    proxy_dict: dict[str, Any] = {
        "proxy_type": scheme,
        "addr": parsed.hostname,
        "port": parsed.port,
    }

    if parsed.username:
        proxy_dict["username"] = unquote(parsed.username)
    if parsed.password:
        proxy_dict["password"] = unquote(parsed.password)
    if scheme in ("socks5", "socks4"):
        proxy_dict["rdns"] = True

    return proxy_dict
