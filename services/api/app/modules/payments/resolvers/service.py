"""
Central service for resolving raw payment gateway links into direct SBP (NSPK) URLs.
"""

from urllib.parse import urlsplit

from app.core.logging import get_logger
from app.modules.payments.resolvers.antilopay import AntilopaySBPResolver
from app.modules.payments.resolvers.cardlink import CardlinkSBPResolver

logger = get_logger(__name__)


async def resolve_sbp_link(url: str, timeout: float = 25.0) -> tuple[str, bool]:
    """
    Attempt to extract a clean, direct SBP link (e.g. https://qr.nspk.ru/...)
    from supported payment gateway links (Antilopay, Cardlink).

    Returns:
        (resolved_link, is_resolved):
        - If extraction succeeded: (clean_nspk_url, True)
        - If already a direct SBP URL: (url, True)
        - If extraction failed or unsupported: (original_url, False)
    """
    if not url:
        return url, False

    try:
        parsed = urlsplit(url)
        # 1. Already a direct NSPK link
        if parsed.netloc in {"qr.nspk.ru", "sub.nspk.ru"}:
            return url, True

        # 2. Antilopay gateway
        if AntilopaySBPResolver.can_handle(url):
            logger.info("resolving_antilopay_sbp_link", url=url)
            clean_link = await AntilopaySBPResolver.resolve(url, timeout=timeout)
            if clean_link:
                logger.info(
                    "antilopay_sbp_link_resolved",
                    original_url=url,
                    resolved_url=clean_link,
                )
                return clean_link, True
            logger.warning(
                "antilopay_sbp_link_resolution_unsuccessful",
                original_url=url,
            )
            return url, False

        # 3. Cardlink gateway
        if CardlinkSBPResolver.can_handle(url):
            logger.info("resolving_cardlink_sbp_link", url=url)
            clean_link = await CardlinkSBPResolver.resolve(url, timeout=timeout)
            if clean_link:
                logger.info(
                    "cardlink_sbp_link_resolved",
                    original_url=url,
                    resolved_url=clean_link,
                )
                return clean_link, True
            logger.warning(
                "cardlink_sbp_link_resolution_unsuccessful",
                original_url=url,
            )
            return url, False

        # 4. Unknown/unsupported gateway — retain original URL
        return url, False

    except Exception as exc:
        logger.warning(
            "sbp_resolver_unexpected_error",
            url=url,
            error=str(exc),
        )
        return url, False
