"""
Async SBP link extractor for Antilopay payment gateway (gate.antilopay.com).
Extracts clean https://qr.nspk.ru/... from https://gate.antilopay.com/payment/APAY...
"""

import re
from urllib.parse import urlsplit

import httpx

from app.core.logging import get_logger

logger = get_logger(__name__)


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
    async def resolve(cls, url: str, timeout: float = 15.0) -> str | None:
        """
        Execute API flow to obtain clean qr.nspk.ru link without browser/fingerprints.
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

        try:
            async with httpx.AsyncClient(headers=headers, timeout=timeout) as client:
                # 1. GET payment details
                res = await client.get(
                    f"{cls.BASE_URL}/payment",
                    params={"id": payment_id, "referer": ""},
                )
                if res.status_code != 200:
                    logger.warning(
                        "antilopay_get_payment_failed",
                        status_code=res.status_code,
                        payment_id=payment_id,
                    )
                    return None

                data = res.json()
                if not isinstance(data, dict):
                    return None

                # Check if qrcId is already present in payment details
                qrc_id = data.get("qrcId")
                if (
                    qrc_id
                    and isinstance(qrc_id, str)
                    and re.fullmatch(r"[A-Za-z0-9]+", qrc_id)
                ):
                    prefix = (
                        "https://sub.nspk.ru/"
                        if data.get("isSubscription")
                        else "https://qr.nspk.ru/"
                    )
                    return f"{prefix}{qrc_id}"

                if data.get("captcha") or data.get("isSubscription"):
                    return None

                if data.get("status") != "PENDING":
                    return None

                payways = data.get("payways") or {}
                if data.get("provideMethod") != "SBP" and "SBP" not in payways:
                    return None

                # 2. POST perform SBP payment
                form_files = {
                    "payment_id": (None, payment_id),
                    "payment_method": (None, "SBP"),
                    "only_sbp": (None, "true"),
                    "cardholder": (None, ""),
                }
                res_perform = await client.post(
                    f"{cls.BASE_URL}/api/v1/payment/perform",
                    files=form_files,
                    headers={"Origin": cls.BASE_URL},
                )
                if res_perform.status_code != 200:
                    logger.warning(
                        "antilopay_perform_failed",
                        status_code=res_perform.status_code,
                        payment_id=payment_id,
                    )
                    return None

                result_data = res_perform.json()
                if not isinstance(result_data, dict):
                    return None

                qrc_id = result_data.get("qrcId")
                if (
                    qrc_id
                    and isinstance(qrc_id, str)
                    and re.fullmatch(r"[A-Za-z0-9]+", qrc_id)
                ):
                    prefix = (
                        "https://sub.nspk.ru/"
                        if data.get("isSubscription")
                        else "https://qr.nspk.ru/"
                    )
                    return f"{prefix}{qrc_id}"

                logger.debug(
                    "antilopay_perform_no_qrc_id",
                    awaiting=result_data.get("awaiting"),
                )
                return None

        except Exception as exc:
            logger.warning(
                "antilopay_resolve_exception",
                url=url,
                error=str(exc),
            )
            return None
