"""
Async SBP link extractor for Antilopay payment gateway (gate.antilopay.com).
Extracts clean https://qr.nspk.ru/... from https://gate.antilopay.com/payment/APAY...
"""

import asyncio
import re
from typing import Any
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


def _extract_nspk_link(val: Any, is_subscription: bool = False) -> str | None:
    """
    Helper to convert a qrcId or NSPK URL into a normalized
    https://(qr|sub).nspk.ru/... link.
    """
    if not val or not isinstance(val, str):
        return None
    val_clean = val.strip()
    if val_clean.startswith("https://qr.nspk.ru/") or val_clean.startswith(
        "https://sub.nspk.ru/"
    ):
        return val_clean
    if re.fullmatch(r"[A-Za-z0-9]+", val_clean):
        prefix = "https://sub.nspk.ru/" if is_subscription else "https://qr.nspk.ru/"
        return f"{prefix}{val_clean}"
    return None


def _find_nspk_in_dict(data: dict[str, Any]) -> str | None:
    """Find any candidate SBP identifier across common gateway response fields."""
    is_sub = bool(data.get("isSubscription"))
    for key in ("qrcId", "banklink", "qr"):
        link = _extract_nspk_link(data.get(key), is_subscription=is_sub)
        if link:
            return link
    return None


class AntilopaySBPResolver:
    """Extracts clean NSPK SBP link from Antilopay payment invoices."""

    BASE_URL = "https://gate.antilopay.com"
    APAY_VERSION = "7555721bc"
    USER_AGENT = (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/150.0.0.0 Safari/537.36"
    )

    @classmethod
    def can_handle(cls, url: str) -> bool:
        """Check if URL is an Antilopay payment link."""
        try:
            parsed = urlsplit(url)
            return parsed.netloc == "gate.antilopay.com" and bool(
                re.search(r"/payment/(APAY[A-Za-z0-9]+)", parsed.path)
            )
        except Exception:
            return False

    @classmethod
    async def resolve(
        cls,
        url: str,
        timeout: float = 25.0,
        max_attempts: int = 3,
        retry_delay: float = 1.5,
    ) -> str | None:
        """
        Execute API flow to obtain clean qr.nspk.ru link without browser/fingerprints.
        Includes settling delay, session verification, and retry polling for
        cases where upstream acquiring banks take a few seconds to allocate the QR code.
        """
        parsed = urlsplit(url)
        match = re.search(r"/payment/(APAY[A-Za-z0-9]+)", parsed.path)
        if not match:
            return None

        payment_id = match.group(1)
        headers = {
            "User-Agent": cls.USER_AGENT,
            "Accept": "*/*",
            "Accept-Language": "ru-RU,ru;q=0.9",
            "Referer": f"{cls.BASE_URL}/",
            "X-Apay-Version": cls.APAY_VERSION,
        }

        timeout_cfg = httpx.Timeout(
            connect=min(10.0, float(timeout)),
            read=float(timeout),
            write=10.0,
            pool=10.0,
        )

        try:
            async with httpx.AsyncClient(
                headers=headers, timeout=timeout_cfg
            ) as client:
                # 1. GET payment details with connection / DNS retry
                res = None
                for get_attempt in range(1, 4):
                    try:
                        res = await client.get(
                            f"{cls.BASE_URL}/payment",
                            params={"id": payment_id, "referer": ""},
                        )
                        if res.status_code == 200:
                            break
                        logger.warning(
                            "antilopay_get_payment_failed",
                            status_code=res.status_code,
                            payment_id=payment_id,
                            attempt=get_attempt,
                        )
                    except (httpx.HTTPError, httpx.TimeoutException, OSError) as exc:
                        logger.warning(
                            "antilopay_get_payment_connect_error",
                            payment_id=payment_id,
                            attempt=get_attempt,
                            error=str(exc),
                        )
                    if get_attempt < 3:
                        await asyncio.sleep(0.5 * get_attempt)

                if res is None or res.status_code != 200:
                    return None

                data = res.json()
                if not isinstance(data, dict):
                    return None

                # Check if qrcId is already present in payment details
                direct_link = _find_nspk_in_dict(data)
                if direct_link:
                    return direct_link

                if data.get("captcha") or data.get("isSubscription"):
                    return None

                if data.get("status") != "PENDING":
                    return None

                payways = data.get("payways") or {}
                if data.get("provideMethod") != "SBP" and "SBP" not in payways:
                    return None

                # Prepare perform form data
                form_files: dict[str, tuple[None, str]] = {
                    "payment_id": (None, payment_id),
                    "payment_method": (None, "SBP"),
                    "only_sbp": (None, "true"),
                    "cardholder": (None, ""),
                }
                session_user_id = data.get("sessionUserId")
                if session_user_id:
                    form_files["sessionUserId"] = (None, str(session_user_id))

                perform_headers = {
                    "Origin": cls.BASE_URL,
                    "Referer": f"{cls.BASE_URL}/payment/{payment_id}",
                }

                # 2. POST perform SBP payment with retry/poll loop
                for attempt in range(1, max_attempts + 1):
                    # Frontend has a 2s delay; a small initial settling pause ensures
                    # Antilopay backend has completed internal gateway setup
                    if attempt == 1:
                        await asyncio.sleep(0.5)

                    try:
                        res_perform = await client.post(
                            f"{cls.BASE_URL}/api/v1/payment/perform",
                            files=form_files,
                            headers=perform_headers,
                        )
                    except (httpx.TimeoutException, httpx.HTTPError, OSError) as exc:
                        logger.warning(
                            "antilopay_perform_request_error",
                            payment_id=payment_id,
                            attempt=attempt,
                            error=str(exc),
                        )
                        if attempt < max_attempts:
                            await asyncio.sleep(retry_delay)
                            continue
                        break

                    if res_perform.status_code == 200:
                        try:
                            result_data = res_perform.json()
                        except Exception:
                            result_data = {}

                        if isinstance(result_data, dict):
                            link = _find_nspk_in_dict(result_data)
                            if link:
                                return link

                            error_msg = result_data.get("error") or result_data.get(
                                "formerror"
                            )
                            logger.info(
                                "antilopay_perform_no_qrc_id",
                                payment_id=payment_id,
                                attempt=attempt,
                                awaiting=result_data.get("awaiting"),
                                error=error_msg,
                                response_keys=list(result_data.keys()),
                            )
                    else:
                        logger.warning(
                            "antilopay_perform_bad_status",
                            payment_id=payment_id,
                            attempt=attempt,
                            status_code=res_perform.status_code,
                        )

                    # If not resolved yet, re-check GET /payment before next attempt
                    # (Acquiring banks often update payment records asynchronously)
                    if attempt < max_attempts:
                        await asyncio.sleep(retry_delay)
                        try:
                            recheck = await client.get(
                                f"{cls.BASE_URL}/payment",
                                params={"id": payment_id, "referer": ""},
                            )
                            if recheck.status_code == 200:
                                recheck_data = recheck.json()
                                if isinstance(recheck_data, dict):
                                    recheck_link = _find_nspk_in_dict(recheck_data)
                                    if recheck_link:
                                        logger.info(
                                            "antilopay_qrc_id_found_on_recheck",
                                            payment_id=payment_id,
                                            attempt=attempt,
                                        )
                                        return recheck_link
                        except (
                            httpx.TimeoutException,
                            httpx.HTTPError,
                            OSError,
                        ) as exc:
                            logger.debug(
                                "antilopay_recheck_failed",
                                payment_id=payment_id,
                                error=str(exc),
                            )

                return None

        except Exception as exc:
            logger.warning(
                "antilopay_resolve_exception",
                url=url,
                error=str(exc),
            )
            return None
