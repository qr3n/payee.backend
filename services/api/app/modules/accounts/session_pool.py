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


def compute_account_fingerprint(account: TelegramAccount) -> str:
    """
    Generate SHA256 fingerprint of all connection-affecting properties of the account.
    """
    import hashlib

    raw = (
        f"{account.session_string}|{account.proxy_url}|{account.api_id}|"
        f"{account.api_hash}|{account.device_model}|{account.system_version}|"
        f"{account.app_version}|{account.system_lang_code}|{account.lang_code}"
    )
    return hashlib.sha256(raw.encode()).hexdigest()


@dataclass
class PooledSession:
    client: TelegramClient
    session_string: str
    config_fingerprint: str
    last_used: float
    lock: asyncio.Lock
    listener_registered: bool = False


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
        fp = compute_account_fingerprint(account)
        async with self._pool_lock:
            pooled = self._sessions.get(account.id)
            if pooled is None:
                client = create_telethon_client(account)
                pooled = PooledSession(
                    client=client,
                    session_string=account.session_string,
                    config_fingerprint=fp,
                    last_used=asyncio.get_running_loop().time(),
                    lock=asyncio.Lock(),
                    listener_registered=False,
                )
                self._sessions[account.id] = pooled
                if self._auto_cleanup:
                    self._ensure_cleanup_task()

        async with pooled.lock:
            # Recreate client if session string or connection configuration changed
            if pooled.config_fingerprint != fp:
                try:
                    if pooled.client.is_connected():
                        await pooled.client.disconnect()
                except Exception as e:
                    logger.debug("Failed disconnecting outdated client", error=str(e))
                pooled.client = create_telethon_client(account)
                pooled.session_string = account.session_string
                pooled.config_fingerprint = fp
                pooled.listener_registered = False

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

            # Register incoming payment notification listener if not registered
            if not pooled.listener_registered:
                from telethon import events

                acc_id = account.id

                async def _on_new_message(event: events.NewMessage.Event) -> None:
                    try:
                        sender = await event.get_sender()
                        sender_username = getattr(sender, "username", None) or ""
                        text = getattr(event, "raw_text", "") or ""
                        if not sender_username or not text:
                            return

                        from app.core.db import async_session_maker
                        from app.modules.payments.notifications import (
                            process_bot_notification,
                        )

                        async with async_session_maker() as db:
                            try:
                                updated = await process_bot_notification(
                                    session=db,
                                    account_id=acc_id,
                                    sender_username=sender_username,
                                    message_text=text,
                                )
                                if updated:
                                    await db.commit()
                            except Exception as e:
                                await db.rollback()
                                logger.error(
                                    "error_processing_bot_payment_notification",
                                    error=str(e),
                                )
                    except Exception as exc:
                        logger.debug("error_in_bot_message_listener", error=str(exc))

                try:
                    import inspect

                    res = pooled.client.add_event_handler(
                        _on_new_message, events.NewMessage(incoming=True)
                    )
                    if inspect.isawaitable(res):
                        await res
                    pooled.listener_registered = True
                except Exception as reg_err:
                    logger.debug(
                        "failed_registering_pool_message_listener",
                        account_id=str(account.id),
                        error=str(reg_err),
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
        Preserves sessions that currently hold active PENDING payments.
        Returns count of evicted accounts.
        """
        now = asyncio.get_running_loop().time()
        to_evict: list[UUID] = []

        async with self._pool_lock:
            for acc_id, pooled in list(self._sessions.items()):
                if (now - pooled.last_used) >= self.idle_ttl:
                    to_evict.append(acc_id)

        filtered_to_evict: list[UUID] = []
        try:
            from sqlmodel import select

            from app.core.db import async_session_maker
            from app.modules.payments.models import Payment, PaymentStatus

            async with async_session_maker() as db:
                for acc_id in to_evict:
                    stmt = (
                        select(Payment.id)
                        .where(
                            Payment.account_id == acc_id,
                            Payment.status == PaymentStatus.PENDING,
                        )
                        .limit(1)
                    )
                    res = await db.exec(stmt)
                    if res.first():
                        await self.touch(acc_id)
                    else:
                        filtered_to_evict.append(acc_id)
        except Exception:
            filtered_to_evict = to_evict

        for acc_id in filtered_to_evict:
            logger.info("session_pool_evicting_idle", account_id=str(acc_id))
            await self.close_account(acc_id)

        return len(filtered_to_evict)

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
