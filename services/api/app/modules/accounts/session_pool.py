"""
Connection pool and lifecycle manager for warm, persistent Telethon clients.
Avoids repetitive MTProto handshakes and TCP connection overhead by keeping
sessions open in memory with an idle TTL timeout.
"""

import asyncio
import contextlib
from dataclasses import dataclass
from uuid import UUID

from telethon import TelegramClient
from telethon.sessions import StringSession

from app.core.config import settings
from app.core.exceptions import AppException
from app.core.logging import get_logger
from app.modules.accounts.models import TelegramAccount
from app.modules.accounts.proxy_utils import parse_proxy_url

logger = get_logger(__name__)


def create_telethon_client(account: TelegramAccount) -> TelegramClient:
    """Instantiate a TelegramClient using account session, proxy, and device profile."""
    resolved_api_id = account.api_id or settings.TELEGRAM_DEFAULT_API_ID
    resolved_api_hash = account.api_hash or settings.TELEGRAM_DEFAULT_API_HASH
    proxy_dict = parse_proxy_url(account.proxy_url) if account.proxy_url else None

    return TelegramClient(
        StringSession(account.session_string),
        api_id=resolved_api_id,
        api_hash=resolved_api_hash,
        proxy=proxy_dict,
        device_model=account.device_model,
        system_version=account.system_version,
        app_version=account.app_version,
        system_lang_code=account.system_lang_code,
        lang_code=account.lang_code,
        timeout=15,
        connection_retries=3,
        auto_reconnect=True,
    )


@dataclass
class PooledSession:
    client: TelegramClient
    session_string: str
    last_used: float
    lock: asyncio.Lock


class TelegramSessionPool:
    """
    In-memory pool for active TelegramClient instances.
    Keeps clients connected across multiple payment operations or status checks.
    """

    def __init__(
        self, idle_ttl: float | None = None, auto_cleanup: bool = True
    ) -> None:
        self._idle_ttl = idle_ttl
        self._auto_cleanup = auto_cleanup
        self._sessions: dict[UUID, PooledSession] = {}
        self._pool_lock = asyncio.Lock()
        self._cleanup_task: asyncio.Task[None] | None = None

    @property
    def idle_ttl(self) -> float:
        if self._idle_ttl is not None:
            return self._idle_ttl
        return float(settings.TELEGRAM_SESSION_IDLE_TTL)

    async def get_connected_client(self, account: TelegramAccount) -> TelegramClient:
        """
        Acquire a warm, connected TelegramClient for the given account.
        Reuses an existing open connection when available, eliminating
        TCP connect and MTProto authorization round-trip delays.
        """
        async with self._pool_lock:
            pooled = self._sessions.get(account.id)
            if pooled is None:
                client = create_telethon_client(account)
                pooled = PooledSession(
                    client=client,
                    session_string=account.session_string,
                    last_used=asyncio.get_running_loop().time(),
                    lock=asyncio.Lock(),
                )
                self._sessions[account.id] = pooled
                if self._auto_cleanup:
                    self._ensure_cleanup_task()

        async with pooled.lock:
            # Recreate client if session string changed
            if pooled.session_string != account.session_string:
                try:
                    if pooled.client.is_connected():
                        await pooled.client.disconnect()
                except Exception as e:
                    logger.debug("Failed disconnecting outdated client", error=str(e))
                pooled.client = create_telethon_client(account)
                pooled.session_string = account.session_string

            if not pooled.client.is_connected():
                logger.info(
                    "session_pool_connecting_client",
                    account_id=str(account.id),
                    phone=account.phone,
                )
                await pooled.client.connect()

            if not await pooled.client.is_user_authorized():
                async with self._pool_lock:
                    self._sessions.pop(account.id, None)
                try:
                    if pooled.client.is_connected():
                        await pooled.client.disconnect()
                except Exception:
                    pass
                raise AppException(
                    message="Assigned Telegram account is not authorized.",
                    code="ACCOUNT_UNAUTHORIZED",
                    status_code=500,
                )

            pooled.last_used = asyncio.get_running_loop().time()
            return pooled.client

    async def touch(self, account_id: UUID) -> None:
        """Update last_used timestamp to keep the session alive."""
        async with self._pool_lock:
            pooled = self._sessions.get(account_id)
            if pooled:
                pooled.last_used = asyncio.get_running_loop().time()

    async def close_account(self, account_id: UUID) -> None:
        """Disconnect and remove a specific account from the warm pool."""
        async with self._pool_lock:
            pooled = self._sessions.pop(account_id, None)

        if pooled:
            async with pooled.lock:
                try:
                    if pooled.client.is_connected():
                        await pooled.client.disconnect()
                        logger.info(
                            "session_pool_disconnected",
                            account_id=str(account_id),
                        )
                except Exception as e:
                    logger.warning("Error disconnecting pooled client", error=str(e))

    async def evict_idle_sessions(self) -> int:
        """
        Evict sessions idle longer than idle_ttl.
        Returns count of evicted accounts.
        """
        now = asyncio.get_running_loop().time()
        to_evict: list[UUID] = []

        async with self._pool_lock:
            for acc_id, pooled in list(self._sessions.items()):
                if (now - pooled.last_used) >= self.idle_ttl:
                    to_evict.append(acc_id)

        for acc_id in to_evict:
            logger.info("session_pool_evicting_idle", account_id=str(acc_id))
            await self.close_account(acc_id)

        return len(to_evict)

    async def close_all(self) -> None:
        """Gracefully disconnect all pooled sessions during app shutdown."""
        if self._cleanup_task and not self._cleanup_task.done():
            self._cleanup_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._cleanup_task
            self._cleanup_task = None

        async with self._pool_lock:
            sessions_to_close = list(self._sessions.values())
            self._sessions.clear()

        for pooled in sessions_to_close:
            try:
                if pooled.client.is_connected():
                    await pooled.client.disconnect()
            except Exception:
                pass
        logger.info("session_pool_all_closed", count=len(sessions_to_close))

    def _ensure_cleanup_task(self) -> None:
        if self._cleanup_task is None or self._cleanup_task.done():
            self._cleanup_task = asyncio.create_task(self._eviction_loop())

    async def _eviction_loop(self) -> None:
        """Background loop to periodically evict idle sessions."""
        try:
            while True:
                await asyncio.sleep(30.0)
                await self.evict_idle_sessions()
                async with self._pool_lock:
                    if not self._sessions:
                        break
        except asyncio.CancelledError:
            pass


# Global singleton instance
telegram_session_pool = TelegramSessionPool()
