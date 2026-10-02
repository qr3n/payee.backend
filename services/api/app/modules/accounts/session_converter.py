"""
Utility for converting Telegram session files (.session)
and parsing accompanying client JSON.
Supports Telethon SQLite session format and standard JSON profile metadata.
"""

import contextlib
import json
import os
import tempfile
from typing import Any

from telethon.sessions import SQLiteSession, StringSession

from app.core.exceptions import AppException
from app.core.logging import get_logger

logger = get_logger(__name__)


def parse_json_proxy(raw_proxy: Any) -> str | None:
    """
    Parse arbitrary proxy format from client JSON into standard URL format.
    Supports strings, dicts, and lists.
    """
    if not raw_proxy:
        return None

    if isinstance(raw_proxy, str):
        raw = raw_proxy.strip()
        if "://" in raw:
            return raw
        # Format host:port:user:pass or host:port
        parts = raw.split(":")
        if len(parts) == 4:
            p_host, p_port, p_user, p_pass = parts
            return f"socks5://{p_user}:{p_pass}@{p_host}:{p_port}"
        elif len(parts) == 2:
            p_host, p_port = parts
            return f"socks5://{p_host}:{p_port}"
        return raw

    if isinstance(raw_proxy, dict):
        d_proto = str(raw_proxy.get("type") or raw_proxy.get("proto") or "socks5")
        d_host = raw_proxy.get("host") or raw_proxy.get("ip") or raw_proxy.get("server")
        d_port = raw_proxy.get("port")
        d_user = raw_proxy.get("user") or raw_proxy.get("username")
        d_pass = raw_proxy.get("pass") or raw_proxy.get("password")
        if d_host and d_port:
            if d_user and d_pass:
                return f"{d_proto}://{d_user}:{d_pass}@{d_host}:{d_port}"
            return f"{d_proto}://{d_host}:{d_port}"

    if isinstance(raw_proxy, (list, tuple)) and len(raw_proxy) >= 3:
        items = [str(x) for x in raw_proxy if x is not None]
        if items[0] in ("socks5", "socks4", "http", "https"):
            l_proto = items[0]
            l_host = items[1]
            l_port = items[2]
            l_user: str | None = items[3] if len(items) > 3 else None
            l_pass: str | None = items[4] if len(items) > 4 else None
        else:
            l_proto = "socks5"
            l_host = items[0]
            l_port = items[1]
            l_user = items[2] if len(items) > 2 else None
            l_pass = items[3] if len(items) > 3 else None

        if l_user and l_pass:
            return f"{l_proto}://{l_user}:{l_pass}@{l_host}:{l_port}"
        return f"{l_proto}://{l_host}:{l_port}"

    return None


def convert_sqlite_session_bytes_to_string(session_bytes: bytes) -> str:
    """
    Convert raw SQLite .session bytes into a Telethon StringSession.
    """
    if len(session_bytes) < 100:
        raise AppException("Uploaded session file is too small or empty")

    with tempfile.NamedTemporaryFile(suffix=".session", delete=False) as tmp_file:
        tmp_file.write(session_bytes)
        tmp_file.flush()
        tmp_path = tmp_file.name

    try:
        base_name = tmp_path.removesuffix(".session")
        session = SQLiteSession(base_name)
        if not session.auth_key:
            raise AppException(
                "Session database does not contain valid authorization key"
            )

        return str(StringSession.save(session))
    except AppException:
        raise
    except Exception as exc:
        logger.error("failed_to_convert_session_file", error=str(exc))
        raise AppException(f"Failed to read .session file: {exc}") from exc
    finally:
        for suffix in ("", "-journal", "-wal", "-shm"):
            p = tmp_path + suffix if suffix else tmp_path
            if os.path.exists(p):
                with contextlib.suppress(OSError):
                    os.remove(p)


def parse_client_json(content: str | bytes) -> dict[str, Any]:
    """
    Parse client JSON metadata accompanying the session file.
    Extracts app_id, app_hash, device profile, and proxy.
    """
    try:
        if isinstance(content, bytes):
            data = json.loads(content.decode("utf-8", errors="replace"))
        else:
            data = json.loads(content)
    except Exception as exc:
        raise AppException(f"Invalid JSON format in metadata file: {exc}") from exc

    if not isinstance(data, dict):
        raise AppException("Client JSON metadata must be a JSON object")

    api_id = data.get("app_id") or data.get("api_id")
    if api_id is not None:
        try:
            api_id = int(api_id)
        except (ValueError, TypeError):
            api_id = None

    api_hash = data.get("app_hash") or data.get("api_hash")
    if api_hash is not None:
        api_hash = str(api_hash).strip() or None

    device = data.get("device") or data.get("device_model") or "Desktop"
    sdk = data.get("sdk") or data.get("system_version") or "Windows 11"
    app_version = data.get("app_version") or "10.0.0"
    system_lang = (
        data.get("system_lang_code") or data.get("system_lang_pack") or "en-US"
    )
    lang_code = data.get("lang_code") or data.get("lang_pack") or "en"
    proxy_url = parse_json_proxy(data.get("proxy"))
    phone = data.get("phone")
    two_fa = data.get("twoFA")

    return {
        "api_id": api_id,
        "api_hash": api_hash,
        "device_model": str(device)[:128],
        "system_version": str(sdk)[:64],
        "app_version": str(app_version)[:64],
        "system_lang_code": str(system_lang)[:16],
        "lang_code": str(lang_code)[:16],
        "proxy_url": proxy_url,
        "phone": str(phone).strip() if phone else None,
        "two_fa": str(two_fa) if two_fa else None,
        "raw_json": data,
    }
