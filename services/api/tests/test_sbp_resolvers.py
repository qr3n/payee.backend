"""
Unit and integration tests for SBP link resolvers (Antilopay and Cardlink).
"""

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.modules.payments.resolvers import (
    AntilopaySBPResolver,
    CardlinkSBPResolver,
    resolve_sbp_link,
)


def test_antilopay_can_handle() -> None:
    assert AntilopaySBPResolver.can_handle(
        "https://gate.antilopay.com/payment/APAY5D4E974B1791029870535"
    )
    assert not AntilopaySBPResolver.can_handle("https://gate.antilopay.com/other/123")
    assert not AntilopaySBPResolver.can_handle("https://cardlink.link/transfer/ABC123")
    assert not AntilopaySBPResolver.can_handle("invalid-url")


def test_cardlink_can_handle() -> None:
    assert CardlinkSBPResolver.can_handle("https://cardlink.link/transfer/P2BnY5ao2E")
    assert not CardlinkSBPResolver.can_handle("https://cardlink.link/other/P2BnY5ao2E")
    assert not CardlinkSBPResolver.can_handle(
        "https://gate.antilopay.com/payment/APAY123"
    )


@pytest.mark.asyncio
async def test_resolve_already_clean_nspk_link() -> None:
    url = "https://qr.nspk.ru/BD10103JPBOS0DSJ830RJ8GV36O7SHV2"
    resolved, is_clean = await resolve_sbp_link(url)
    assert resolved == url
    assert is_clean is True

    sub_url = "https://sub.nspk.ru/AD10103JPBOS0DSJ830RJ8GV36O7SHV2"
    resolved_sub, is_clean_sub = await resolve_sbp_link(sub_url)
    assert resolved_sub == sub_url
    assert is_clean_sub is True


@pytest.mark.asyncio
async def test_resolve_unsupported_url() -> None:
    url = "https://example.com/checkout/123"
    resolved, is_clean = await resolve_sbp_link(url)
    assert resolved == url
    assert is_clean is False


@pytest.mark.asyncio
async def test_antilopay_resolve_qrc_in_get() -> None:
    """Test when Antilopay already includes qrcId in initial GET /payment response."""
    test_url = "https://gate.antilopay.com/payment/APAY12345"

    mock_payment_data = {
        "status": "PENDING",
        "provideMethod": "SBP",
        "qrcId": "BD10103JPBOS0DSJ830RJ8GV36O7SHV2",
    }

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.json = MagicMock(return_value=mock_payment_data)

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        result = await AntilopaySBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/BD10103JPBOS0DSJ830RJ8GV36O7SHV2"


@pytest.mark.asyncio
async def test_antilopay_resolve_via_perform() -> None:
    """Test when Antilopay returns qrcId after POST /api/v1/payment/perform."""
    test_url = "https://gate.antilopay.com/payment/APAY12345"

    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 200
    mock_get_resp.json = MagicMock(
        return_value={
            "status": "PENDING",
            "provideMethod": "SBP",
            "qrcId": None,
        }
    )

    mock_post_resp = AsyncMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json = MagicMock(
        return_value={
            "awaiting": True,
            "qrcId": "AD10102GL0VMM7VV97E952F70MAQ91Q2",
        }
    )

    with (
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_get_resp)),
        patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_post_resp)),
    ):
        result = await AntilopaySBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/AD10102GL0VMM7VV97E952F70MAQ91Q2"


@pytest.mark.asyncio
async def test_antilopay_resolve_failure_returns_none() -> None:
    """Test that Antilopay resolver returns None gracefully on HTTP error."""
    test_url = "https://gate.antilopay.com/payment/APAY12345"

    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 500

    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_get_resp)),
    ):
        result = await AntilopaySBPResolver.resolve(test_url)
        assert result is None

        # verify resolve_sbp_link gracefully falls back to original
        link, is_clean = await resolve_sbp_link(test_url)
        assert link == test_url
        assert is_clean is False


@pytest.mark.asyncio
async def test_antilopay_resolve_recovers_from_dns_error() -> None:
    """Test that transient DNS ConnectError on initial GET is retried and succeeds."""
    test_url = "https://gate.antilopay.com/payment/APAY12345"

    mock_success_get = AsyncMock()
    mock_success_get.status_code = 200
    mock_success_get.json = MagicMock(
        return_value={
            "status": "PENDING",
            "provideMethod": "SBP",
            "qrcId": "AD1010DNSRECOVERY888",
        }
    )

    get_side_effects = [
        httpx.ConnectError("[Errno -5] No address associated with hostname"),
        mock_success_get,
    ]

    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=get_side_effects)),
    ):
        result = await AntilopaySBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/AD1010DNSRECOVERY888"


@pytest.mark.asyncio
async def test_antilopay_resolve_retry_via_recheck() -> None:
    """
    Test when initial POST /perform returns 200 without qrcId (awaiting=None),
    but the subsequent re-check of GET /payment finds qrcId populated by the bank.
    """
    test_url = "https://gate.antilopay.com/payment/APAY12345"

    # 1. Initial GET /payment has no qrcId
    mock_initial_get = AsyncMock()
    mock_initial_get.status_code = 200
    mock_initial_get.json = MagicMock(
        return_value={
            "status": "PENDING",
            "provideMethod": "SBP",
            "qrcId": None,
            "sessionUserId": "mock_suid_123",
        }
    )

    # 2. Recheck GET /payment has qrcId populated
    mock_recheck_get = AsyncMock()
    mock_recheck_get.status_code = 200
    mock_recheck_get.json = MagicMock(
        return_value={
            "status": "PENDING",
            "provideMethod": "SBP",
            "qrcId": "AD10107RECHECK999",
        }
    )

    get_side_effects = [mock_initial_get, mock_recheck_get]

    # POST /perform returns 200 with awaiting=None and no qrcId
    mock_post_resp = AsyncMock()
    mock_post_resp.status_code = 200
    mock_post_resp.json = MagicMock(
        return_value={
            "awaiting": None,
            "url": None,
        }
    )

    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient.get", new=AsyncMock(side_effect=get_side_effects)),
        patch("httpx.AsyncClient.post", new=AsyncMock(return_value=mock_post_resp)),
    ):
        result = await AntilopaySBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/AD10107RECHECK999"


@pytest.mark.asyncio
async def test_antilopay_resolve_retry_via_second_perform() -> None:
    """Test when attempt 1 perform fails/times out, but attempt 2 perform succeeds."""
    test_url = "https://gate.antilopay.com/payment/APAY12345"

    mock_get = AsyncMock()
    mock_get.status_code = 200
    mock_get.json = MagicMock(
        return_value={
            "status": "PENDING",
            "provideMethod": "SBP",
            "qrcId": None,
        }
    )

    mock_post_1 = AsyncMock()
    mock_post_1.status_code = 200
    mock_post_1.json = MagicMock(return_value={"error": "Gateway busy"})

    mock_post_2 = AsyncMock()
    mock_post_2.status_code = 200
    mock_post_2.json = MagicMock(
        return_value={
            "awaiting": True,
            "qrcId": "BD1010SECOND999",
        }
    )

    post_side_effects = [mock_post_1, mock_post_2]

    with (
        patch("asyncio.sleep", new=AsyncMock()),
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_get)),
        patch("httpx.AsyncClient.post", new=AsyncMock(side_effect=post_side_effects)),
    ):
        result = await AntilopaySBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/BD1010SECOND999"


@pytest.mark.asyncio
async def test_cardlink_resolve_in_html() -> None:
    """Test Cardlink when payment URL is already embedded in page HTML."""
    test_url = "https://cardlink.link/transfer/P2BnY5ao2E"
    html_content = (
        '<html><body><a href="https://qr.nspk.ru/BD10103JPBOS0DSJ830RJ8GV36O7SHV2">'
        "Оплатить через СБП</a></body></html>"
    )

    mock_resp = AsyncMock()
    mock_resp.status_code = 200
    mock_resp.text = html_content
    mock_resp.url = test_url

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        result = await CardlinkSBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/BD10103JPBOS0DSJ830RJ8GV36O7SHV2"


@pytest.mark.asyncio
async def test_cardlink_resolve_via_livewire() -> None:
    """Test Cardlink multi-step Livewire interaction returning direct URL."""
    test_url = "https://cardlink.link/transfer/P2BnY5ao2E"
    initial_html = (
        '<html><head><meta name="csrf-token" content="mock_token"></head>'
        '<body><div data-update-uri="/livewire/update">'
        '<div wire:snapshot=\'{"memo": {"name": "payment-send-form.methods.sbp"}, '
        '"data": {"componentInited": true, "waitingForActivePayment": false}}\'></div>'
        "</div></body></html>"
    )

    mock_get_resp = AsyncMock()
    mock_get_resp.status_code = 200
    mock_get_resp.text = initial_html
    mock_get_resp.url = test_url

    snapshot_data = '{"memo": {"name": "payment-send-form.methods.sbp"}, "data": {}}'
    livewire_resp = AsyncMock()
    livewire_resp.status_code = 200
    livewire_resp.json = MagicMock(
        return_value={
            "components": [
                {
                    "snapshot": snapshot_data,
                    "effects": {
                        "html": '<a href="https://qr.nspk.ru/QR123456789">СБП</a>'
                    },
                }
            ]
        }
    )

    with (
        patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_get_resp)),
        patch("httpx.AsyncClient.post", new=AsyncMock(return_value=livewire_resp)),
    ):
        result = await CardlinkSBPResolver.resolve(test_url)
        assert result == "https://qr.nspk.ru/QR123456789"

        # verify resolve_sbp_link wrapper
        link, is_clean = await resolve_sbp_link(test_url)
        assert link == "https://qr.nspk.ru/QR123456789"
        assert is_clean is True


@pytest.mark.asyncio
async def test_cardlink_resolve_failure_fallback() -> None:
    """Test Cardlink returns None on failure and fallback returns original."""
    test_url = "https://cardlink.link/transfer/P2BnY5ao2E"

    mock_resp = AsyncMock()
    mock_resp.status_code = 404

    with patch("httpx.AsyncClient.get", new=AsyncMock(return_value=mock_resp)):
        result = await CardlinkSBPResolver.resolve(test_url)
        assert result is None

        link, is_clean = await resolve_sbp_link(test_url)
        assert link == test_url
        assert is_clean is False
